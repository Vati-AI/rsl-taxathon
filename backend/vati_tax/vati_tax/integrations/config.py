"""Build the integration/ service adapters from site configuration (TAX-085, ADR-035).

Frappe-free so it can be unit-tested without a bench; ``factories`` binds it to
``frappe.conf``. The adapters themselves live in ``integration/`` (Jube case
lifecycle and risk invoker, Docling, OpenAleph, yente). In the developer stack
that directory is mounted read-only and its adapter directories are on the bench
path (``scripts/frappe_dev.py init``); they use only the standard library, so no
dependency is installed for them.

``frappe_dev.py init`` writes one ``vati_services`` object into the site config
from the ignored local files (``.local/*.env``, ``.local/jube-case-workflow.json``,
root ``.env``)::

    "vati_services": {
      "jube": {"base_url", "case_adapter_api_key", "vati_tenant_id", "workflow_guid",
               "workflow_version", "status_guids", "risk_invoker_api_key",
               "risk_model_guid"},
      "docling": {"base_url", "api_key"},
      "openaleph": {"base_url", "api_key", "collection_id"},
      "yente": {"base_url", "dataset"}
    }

A missing or malformed section, or adapter code that is not on the bench path, is
``ServiceNotConfigured`` (``upstream_unavailable``, HTTP 503, reason
``<service>_not_configured``). Nothing is faked: there is no fixture fallback.
Error messages never contain configuration values, so keys cannot leak into
logs or responses.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from types import ModuleType
from typing import Any

from vati_tax.ids.canonical import MappingError

SERVICES_KEY = "vati_services"
# Site-config keys the existing Frappe bindings read (decisions/store.py, bridge/store.py).
SITE_FACTORIES = {
    "vati_case_lifecycle": "vati_tax.integrations.factories.case_lifecycle",
    "vati_risk_evaluator": "vati_tax.integrations.factories.risk_evaluator",
}
_LABELS = {
    "jube": "Jube case",
    "jube_risk": "Jube risk",
    "docling": "Docling extraction",
    "openaleph": "OpenAleph",
    "yente": "yente matching",
}
# Same field as decisions/store.py used for jube_not_configured before TAX-085.
_FIELDS = {"jube": "case_id", "jube_risk": "case_id"}


class ServiceNotConfigured(MappingError):
    """The site has no usable configuration (or adapter code) for ``service``."""

    code = "upstream_unavailable"
    http_status = 503
    retryable = True

    def __init__(self, service: str, reason: str | None = None) -> None:
        self.service = service
        self.reason = reason or f"{service}_not_configured"
        super().__init__(
            f"The {_LABELS.get(service, service)} service is not configured for this site.",
            _FIELDS.get(service, "service"),
            self.reason,
        )


def section(
    conf: Mapping[str, Any], service: str, config_key: str | None = None
) -> Mapping[str, Any]:
    services = conf.get(SERVICES_KEY)
    value = services.get(config_key or service) if isinstance(services, Mapping) else None
    if not isinstance(value, Mapping) or not value:
        raise ServiceNotConfigured(service)
    return value


def adapter_module(name: str, service: str) -> ModuleType:
    """Import one integration/ adapter module from the bench path, or report it missing."""
    try:
        return importlib.import_module(name)
    except ImportError:
        raise ServiceNotConfigured(service, "adapter_not_installed") from None


def _text(values: Mapping[str, Any], key: str, service: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ServiceNotConfigured(service)
    return value.strip()


def configured(conf: Mapping[str, Any]) -> dict[str, bool]:
    """Which services have a configuration section; no values are returned."""
    raw = conf.get(SERVICES_KEY)
    services: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}

    def part(name: str) -> Mapping[str, Any]:
        value = services.get(name)
        return value if isinstance(value, Mapping) else {}

    jube = part("jube")
    return {
        "jube": bool(jube.get("case_adapter_api_key")),
        "jube_risk": bool(jube.get("risk_invoker_api_key") and jube.get("risk_model_guid")),
        "docling": bool(part("docling").get("api_key")),
        "openaleph": bool(part("openaleph").get("api_key")),
        "yente": bool(part("yente").get("base_url")),
    }


# --------------------------------------------------------------------------- Jube


def case_lifecycle(
    conf: Mapping[str, Any],
    *,
    record_link: Callable[..., Any],
    case_service_links: Callable[..., Any],
    transport: Any = None,
) -> Any:
    """TAX-034 ``JubeCaseAdapter`` with the Vati Service Link store (``ids.mapping``)."""
    values = section(conf, "jube")
    cl = adapter_module("case_lifecycle", "jube")
    try:
        workflow = cl.load_workflow()
        version = values.get("workflow_version")
        if version is not None and version != workflow.version:
            # Status GUIDs were provisioned for another workflow version.
            raise ServiceNotConfigured("jube", "jube_workflow_mismatch")
        guids = values.get("status_guids")
        if not isinstance(guids, Mapping):
            raise ServiceNotConfigured("jube")
        config = cl.AdapterConfig.build(
            workflow,
            base_url=_text(values, "base_url", "jube"),
            api_key=values.get("case_adapter_api_key"),
            vati_tenant_id=_text(values, "vati_tenant_id", "jube"),
            workflow_guid=_text(values, "workflow_guid", "jube"),
            status_guids=guids,
        )
        links = cl.MappingLinkStore(record_link, case_service_links)
        return cl.JubeCaseAdapter(config, workflow, links, transport=transport)
    except (cl.ConfigError, cl.WorkflowError, OSError):
        raise ServiceNotConfigured("jube") from None


def risk_evaluator(conf: Mapping[str, Any], *, transport: Any = None) -> Any:
    """TAX-039 ``DeclaredRiskEvaluator`` (TAX-035 model, risk invoker API key)."""
    values = section(conf, "jube_risk", "jube")
    ri = adapter_module("risk_invoke", "jube_risk")
    tr = adapter_module("tax_risk", "jube_risk")
    try:
        config = ri.RiskInvokerConfig(
            base_url=_text(values, "base_url", "jube_risk").rstrip("/"),
            api_key=_text(values, "risk_invoker_api_key", "jube_risk"),
            model_guid=_text(values, "risk_model_guid", "jube_risk"),
        )
        return ri.DeclaredRiskEvaluator(ri.RiskInvoker(config, transport), tr.load_ruleset())
    except (ri.RiskConfigError, tr.RulesetError, OSError, KeyError, TypeError):
        raise ServiceNotConfigured("jube_risk") from None


# --------------------------------------------------------------------------- Docling


def docling_extraction(
    conf: Mapping[str, Any], *, jobs: Any, sink: Any, transport: Any = None
) -> Any:
    """TAX-038 ``DoclingExtractionAdapter``; the caller supplies the job store and sink."""
    values = section(conf, "docling")
    ea = adapter_module("extraction_adapter", "docling")
    try:
        config = ea.AdapterConfig.build(
            base_url=_text(values, "base_url", "docling"), api_key=values.get("api_key")
        )
    except (ea.ConfigError, TypeError):
        raise ServiceNotConfigured("docling") from None
    return ea.DoclingExtractionAdapter(config, jobs=jobs, sink=sink, transport=transport)


# --------------------------------------------------------------------------- OpenAleph


def _guarded(
    transport: Callable[..., Any], unavailable: Callable[[], Exception]
) -> Callable[..., Any]:
    """An unreachable host is the module's own retryable ``upstream_unavailable``."""

    def call(method: str, path: str, body: Any = None) -> Any:
        try:
            return transport(method, path, body)
        except OSError:  # URLError, timeouts, refused connections
            raise unavailable() from None

    return call


class OpenAlephIngest:
    """TAX-036 entity ingest bound to this site's OpenAleph collection."""

    def __init__(self, module: ModuleType, transport: Callable[..., Any], collection_id: int):
        self._module = module
        self.transport = transport
        self.collection_id = collection_id

    def ingest(
        self,
        entities: list[dict[str, Any]],
        digests: Mapping[str, str],
        ledger: Mapping[str, str],
    ) -> dict[str, str]:
        return self._module.ingest(self.transport, self.collection_id, entities, digests, ledger)


def openaleph_ingest(conf: Mapping[str, Any], *, transport: Any = None) -> OpenAlephIngest:
    values = section(conf, "openaleph")
    oa = adapter_module("entity_ingest", "openaleph")
    collection = values.get("collection_id")
    try:
        collection_id = int(collection)  # type: ignore[arg-type]
        if collection_id < 1 or isinstance(collection, bool):
            raise ValueError
        send = transport or oa.http_transport(
            _text(values, "base_url", "openaleph"), _text(values, "api_key", "openaleph")
        )
    except (ValueError, TypeError):
        raise ServiceNotConfigured("openaleph") from None

    def unavailable() -> Exception:
        return oa.IngestError("upstream_unavailable", "OpenAleph is unreachable", retryable=True)

    return OpenAlephIngest(oa, _guarded(send, unavailable), collection_id)


# --------------------------------------------------------------------------- yente


class YenteMatcher:
    """TAX-037 candidate lookup bound to this site's yente and dataset."""

    def __init__(self, module: ModuleType, transport: Callable[..., Any], dataset: str):
        self._module = module
        self.transport = transport
        self.dataset = dataset

    def dataset_version(self) -> Any:
        return self._module.dataset_version(self.transport, self.dataset)

    def match(self, entities: Any, *, retrieved_at: str, **options: Any) -> Any:
        return self._module.match(
            self.transport, self.dataset, entities, retrieved_at=retrieved_at, **options
        )


def yente_matcher(conf: Mapping[str, Any], *, transport: Any = None) -> YenteMatcher:
    values = section(conf, "yente")
    cr = adapter_module("candidate_review", "yente")
    try:
        send = transport or cr.http_transport(_text(values, "base_url", "yente"))
    except ValueError:
        raise ServiceNotConfigured("yente") from None

    def unavailable() -> Exception:
        return cr.ReviewError(
            "upstream_unavailable", "yente is unreachable", reason="unreachable", retryable=True
        )

    return YenteMatcher(cr, _guarded(send, unavailable), _text(values, "dataset", "yente"))
