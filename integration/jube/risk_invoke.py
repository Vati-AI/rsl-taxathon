"""Invoke the TAX-035 Jube risk model for one Vati case (TAX-039).

Jube raises a case only from an archived model invocation whose entry value is the
Vati ``case_id`` (ADR-009), so the Frappe bridge invokes the model before it asks the
TAX-034 adapter to create the case. Route and authentication come from the locked
Jube source and the deployed Swagger recorded in ``integration/jube/README.md``:

- ``POST /api/Invoke/EntityAnalysisModel/{model_guid}`` with the invoke user's
  ``X-API-KEY`` (``vati-risk-invoker``: no role permissions, only this model's grant).
- 200 returns the evaluation; a wrong key is 401; a model that is not loaded yet is 404.

The payload is built by ``tax_risk.jube_payload`` (or the bridge's declared-facts
builder) from the reviewed ruleset, so the rule inputs Jube sees are the ones the
Python evaluator uses. Amounts travel as decimal strings.

What the bridge keeps from the response is deliberately small: the HTTP outcome and
the names of the activation rules that fired, as a reference for the outbox effect
ledger. Rule names and activations are internal review signals; they are never
copied to anything a taxpayer can read.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

# Sibling module: callers put integration/jube on sys.path, as for case_lifecycle.
from case_lifecycle import (
    RETRYABLE_HTTP,
    HttpTransport,
    JubeAdapterError,
    Response,
    Transport,
    TransportError,
)

log = logging.getLogger("vati.jube_risk")
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


class RiskConfigError(ValueError):
    """Invoker configuration is missing or malformed (fails at startup)."""


@dataclass(frozen=True)
class RiskInvokerConfig:
    base_url: str
    api_key: str
    model_guid: str
    # Payload key read by the model's entry XPath ``$.case_id`` (ruleset ``entry_field``).
    entry_field: str = "case_id"
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise RiskConfigError("JUBE_BASE_URL must be an http(s) URL")
        if self.base_url.startswith("http://") and not self.base_url.startswith(
            # host.docker.internal: the developer's machine seen from the Frappe container.
            ("http://127.0.0.1", "http://localhost", "http://[::1]", "http://host.docker.internal:")
        ):
            raise RiskConfigError("plain HTTP to Jube is only allowed on loopback")
        if not self.api_key or len(self.api_key) < 20:
            raise RiskConfigError("the Jube risk invoker API key is missing")
        if not GUID.fullmatch(self.model_guid):
            raise RiskConfigError("the Jube risk model GUID is malformed")

    def __repr__(self) -> str:  # never print the key
        return f"RiskInvokerConfig(base_url={self.base_url!r}, model_guid={self.model_guid!r})"


@dataclass(frozen=True)
class RiskInvocation:
    """What Vati keeps from one invocation: the case it was for and which rules fired."""

    case_id: str
    activations: tuple[str, ...]

    @property
    def reference(self) -> str:
        """Short, stable reference for the outbox effect ledger (no amounts or text)."""
        return f"jube-invoke:{self.case_id}:{len(self.activations)}"


def activation_names(body: Any) -> tuple[str, ...]:
    """Fired activation-rule names from a Jube invoke response (keys or a list)."""
    found: set[str] = set()
    if not isinstance(body, Mapping):
        return ()
    activation = body.get("activation") or body.get("Activation") or {}
    if isinstance(activation, Mapping):
        found.update(str(name) for name in activation)
    elif isinstance(activation, list):
        for item in activation:
            name = (item.get("name") or item.get("Name")) if isinstance(item, Mapping) else item
            if name:
                found.add(str(name))
    return tuple(sorted(found))


DECIMAL = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]{2})?$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def declared_payload(
    ruleset: Mapping[str, Any],
    *,
    case_id: str,
    taxpayer_id: str,
    period_end: str,
    facts: Mapping[str, str],
) -> dict[str, Any]:
    """Jube invoke payload from the bridge's declared facts (TAX-039).

    Same shape as ``tax_risk.jube_payload`` (entry field, reference date, one value per
    ruleset feature under its ``jube_name``), but the inputs come from what the taxpayer
    declared in Frappe instead of a TAX-024 record. The facts must name exactly the
    ruleset's features: a ruleset change without a bridge change fails here, loudly.
    """
    features = {f["name"]: f for f in ruleset["features"]}
    if set(facts) != set(features):
        missing, extra = sorted(set(features) - set(facts)), sorted(set(facts) - set(features))
        raise RiskConfigError(f"declared facts do not match the ruleset: {missing=} {extra=}")
    if not DATE.fullmatch(period_end):
        raise RiskConfigError("period_end must be YYYY-MM-DD")
    model = ruleset["jube_model"]
    payload: dict[str, Any] = {
        model["entry_field"]: case_id,
        "taxpayer_id": taxpayer_id,
        model["reference_date_field"]: f"{period_end}T00:00:00Z",
        "synthetic": True,
        "ruleset_version": ruleset["ruleset_version"],
        "facts_basis": "declaration",
    }
    for name, feature in features.items():
        value = facts[name]
        if not isinstance(value, str) or not DECIMAL.fullmatch(value):
            raise RiskConfigError(f"fact {name} must be a decimal string")
        if feature["type"] == "integer" and "." in value:
            raise RiskConfigError(f"fact {name} must be an integer")
        payload[feature["jube_name"]] = value
    return payload


class DeclaredRiskEvaluator:
    """The bridge's ``RiskEvaluator`` port: declared facts -> one Jube invocation."""

    def __init__(self, invoker: RiskInvoker, ruleset: Mapping[str, Any]) -> None:
        if ruleset["jube_model"]["entry_field"] != invoker.config.entry_field:
            raise RiskConfigError("invoker entry field differs from the ruleset's")
        self.invoker = invoker
        self.ruleset = ruleset

    def invoke_declared(
        self,
        *,
        case_id: str,
        taxpayer_id: str,
        period_end: str,
        facts: Mapping[str, str],
        correlation_id: str,
    ) -> str:
        payload = declared_payload(
            self.ruleset,
            case_id=case_id,
            taxpayer_id=taxpayer_id,
            period_end=period_end,
            facts=facts,
        )
        return self.invoker.invoke(payload, correlation_id=correlation_id).reference


class RiskInvoker:
    """Sends one risk invocation. It never creates or changes a Jube case."""

    def __init__(self, config: RiskInvokerConfig, transport: Transport | None = None) -> None:
        self.config = config
        self._send: Callable[[str, str, Any], Response] = transport or HttpTransport(
            config.base_url.rstrip("/"), config.api_key, config.timeout_s
        )

    def invoke(self, payload: Mapping[str, Any], *, correlation_id: str) -> RiskInvocation:
        """Invoke the model with ``payload``; its entry value must be the Vati case_id.

        A repeated invocation is harmless for Jube's state: it archives one more
        evaluation of the same facts and changes no case. That is why the bridge may
        repeat it after a crash, and why a timeout is reported as retryable rather
        than looked up.
        """
        case_id = payload.get(self.config.entry_field)
        if not isinstance(case_id, str) or not case_id:
            raise JubeAdapterError(
                "invalid_request",
                "The risk payload has no case_id entry value.",
                http_status=400,
                reason="missing_entry_value",
                field=self.config.entry_field,
            )
        if any(isinstance(value, float) for value in payload.values()):
            raise JubeAdapterError(
                "invalid_request",
                "Risk inputs must be decimal strings, not floats.",
                http_status=400,
                reason="float_value",
                field="payload",
            )
        route = f"/api/Invoke/EntityAnalysisModel/{self.config.model_guid}"
        try:
            response = self._send("POST", route, dict(payload))
        except TransportError as error:
            raise JubeAdapterError(
                "upstream_timeout" if error.timed_out else "upstream_unavailable",
                "Jube did not answer the risk invocation.",
                http_status=504 if error.timed_out else 503,
                retryable=True,
                reason="jube_timeout" if error.timed_out else "jube_unreachable",
            ) from None
        if response.status == 200:
            result = RiskInvocation(case_id, activation_names(response.body))
            log.info(
                "risk_invoke ok case=%s activations=%d correlation=%s",
                case_id,
                len(result.activations),
                correlation_id,
            )
            return result
        if response.status in RETRYABLE_HTTP or response.status == 404:
            # 404: the model is not loaded yet (new tenant, pending synchronisation).
            raise JubeAdapterError(
                "upstream_unavailable",
                "Jube cannot evaluate risk right now.",
                http_status=503,
                retryable=True,
                reason="risk_model_unavailable" if response.status == 404 else "jube_busy",
                upstream_status=response.status,
            )
        if response.status in (401, 403):
            raise JubeAdapterError(
                "upstream_unavailable",
                "The Jube risk invoker is not authorised.",
                http_status=502,
                retryable=False,
                reason="jube_invoker_unauthorised",
                upstream_status=response.status,
            )
        raise JubeAdapterError(
            "upstream_unavailable",
            "Jube refused the risk invocation.",
            http_status=502,
            retryable=False,
            reason="jube_invoke_refused",
            upstream_status=response.status,
        )
