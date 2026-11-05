"""In-process stand-ins for Jube, Docling, OpenAleph and yente (TAX-087, ADR-037).

Used only by the bench tests and by ``pipeline.demo`` on a disposable ``verify-*`` site
when a service has no configuration. They are transports (Docling, OpenAleph, yente)
handed to the *real* integration adapters, so the adapters' request building, response
checks and error mapping still run; Jube is replaced at the port level, as in the
TAX-039 bridge tests. Every fake identifies itself: the demo report marks each service
``live`` or ``fake``. Nothing here is used in normal operation; there is no silent
fallback (ADR-035).

``down = True`` makes a fake answer 503, which the adapters report as a retryable
``upstream_unavailable``: that is how the unavailable-service path is exercised.
"""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

FAKE_DATASET = "vati_synthetic"
FAKE_DATASET_VERSION = "20261101000000-syn"
FAKE_COLLECTION = 1
# Site-config shaped settings for config.docling_extraction / openaleph_ingest /
# yente_matcher. The URLs are never contacted: the fake transport replaces them.
FAKE_SERVICES: dict[str, Any] = {
    "vati_services": {
        "docling": {"base_url": "http://127.0.0.1:9", "api_key": "fake-docling-key-0000"},
        "openaleph": {
            "base_url": "http://127.0.0.1:9",
            "api_key": "fake-openaleph-key-0000",
            "collection_id": FAKE_COLLECTION,
        },
        "yente": {"base_url": "http://127.0.0.1:9", "dataset": FAKE_DATASET},
    }
}


@dataclass(frozen=True)
class Response:
    """Shape shared by the three adapters' transports (status, body, retry_after)."""

    status: int
    body: Any = None
    retry_after: float | None = None


@dataclass
class FakeDocling:
    """Docling Serve async routes: submit, poll, result (docling-serve 1.38.2 shapes)."""

    down: bool = False
    submissions: int = 0
    lose_tasks: bool = False
    tasks: dict[str, int] = field(default_factory=dict)  # task_id -> size of the document

    def __call__(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> Response:
        if self.down:
            return Response(503)
        if method == "POST" and path == "/v1/convert/file/async":
            self.submissions += 1
            task_id = f"fake-docling-task-{self.submissions}"
            self.tasks[task_id] = len(body or b"")
            return Response(200, {"task_id": task_id, "task_status": "pending"})
        task_id = path.split("?")[0].rsplit("/", 1)[-1]
        if self.lose_tasks or task_id not in self.tasks:
            return Response(404)
        if path.startswith("/v1/status/poll/"):
            return Response(200, {"task_id": task_id, "task_status": "success"})
        if path.startswith("/v1/result/"):
            return Response(200, self.result(task_id))
        return Response(404)

    @staticmethod
    def result(task_id: str) -> dict[str, Any]:
        box = {"l": 10.0, "t": 50.0, "r": 200.0, "b": 30.0, "coord_origin": "BOTTOMLEFT"}
        return {
            "status": "success",
            "errors": [],
            "document": {
                "json_content": {
                    "pages": {"1": {"page_no": 1}},
                    "texts": [
                        {
                            "self_ref": "#/texts/0",
                            "label": "text",
                            "text": f"Synthetic document converted by the fake Docling ({task_id}).",
                            "prov": [{"page_no": 1, "bbox": box}],
                        }
                    ],
                    "tables": [],
                }
            },
        }


@dataclass
class FakeOpenAleph:
    """``POST /api/2/collections/{id}/_bulk`` answering 204 and keeping what it got."""

    down: bool = False
    writes: int = 0
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __call__(self, method: str, path: str, body: Any = None) -> Response:
        if self.down:
            return Response(503)
        if method == "POST" and path == f"/api/2/collections/{FAKE_COLLECTION}/_bulk":
            self.writes += 1
            for entity in body or []:
                self.entities[str(entity["id"])] = entity
            return Response(204, b"")
        return Response(404, b"")


@dataclass
class FakeYente:
    """``GET /catalog`` and ``POST /match/{dataset}`` (yente 5.5.0 shapes).

    Parties whose name contains ``Group`` get one synthetic candidate above the
    threshold, so the officer review queue has something to show; others get none.
    """

    down: bool = False
    matches: int = 0

    def __call__(self, method: str, path: str, body: Any = None) -> Response:
        if self.down:
            return Response(503, b"")
        if method == "GET" and path == "/catalog":
            catalog = {
                "datasets": [
                    {
                        "name": FAKE_DATASET,
                        "version": FAKE_DATASET_VERSION,
                        "index_version": FAKE_DATASET_VERSION,
                        "load": True,
                    }
                ],
                "current": [FAKE_DATASET],
                "outdated": [],
            }
            return Response(200, json.dumps(catalog).encode())
        if method == "POST" and path.startswith(f"/match/{FAKE_DATASET}"):
            self.matches += 1
            query = (body or {}).get("queries", {}).get("q", {})
            names = query.get("properties", {}).get("name", [])
            results = []
            if any("group" in str(n).lower() for n in names):
                results.append(
                    {
                        "id": "syn-yente-group-0001",
                        "caption": "Synthetic Group Holdings (fake yente)",
                        "schema": "Company",
                        "properties": {"name": ["Synthetic Group Holdings"]},
                        "datasets": [FAKE_DATASET],
                        "score": 0.82,
                        "match": True,
                    }
                )
            answer = {
                "responses": {
                    "q": {"status": 200, "results": results, "total": len(results), "query": query}
                },
                "limit": 5,
            }
            return Response(200, json.dumps(answer).encode())
        return Response(404, b"")


class FakeJube:
    """Jube lifecycle and risk ports (TAX-034/039 shapes), as in the bridge bench tests.

    ``record_link`` is ``vati_tax.ids.mapping.record_link``: the fake records the Jube
    case link exactly as the real adapter's ``MappingLinkStore`` does.
    """

    def __init__(self, record_link: Callable[..., Any]) -> None:
        self._record_link = record_link
        self.down = False
        self.archived: set[str] = set()
        self.cases: dict[str, types.SimpleNamespace] = {}
        self.invocations = 0

    def invoke_declared(self, *, case_id, taxpayer_id, period_end, facts, correlation_id):
        self._check()
        self.invocations += 1
        self.archived.add(case_id)
        return f"fake-jube-invoke:{case_id}:{self.invocations}"

    def ensure_case(self, tenant_id, case_id, *, correlation_id, source_event_id=None):
        self._check()
        if case_id not in self.cases:
            if case_id not in self.archived:
                raise _JubeError("risk_evaluation_missing", retryable=True)
            jube_id = 9000 + len(self.cases) + 1
            self.cases[case_id] = types.SimpleNamespace(
                jube_case_id=jube_id, status="RECEIVED", escalated=False, closed_state="open"
            )
            self._record_link(
                tenant_id=tenant_id,
                case_id=case_id,
                service="jube",
                external_type="case",
                external_id=str(jube_id),
            )
        return types.SimpleNamespace(case=self.cases[case_id], changed=False)

    def get_case(self, tenant_id, case_id, *, correlation_id):
        self._check()
        if case_id not in self.cases:
            raise _JubeError("no_jube_link", retryable=False)
        return self.cases[case_id]

    def _check(self) -> None:
        if self.down:
            raise _JubeError("jube_unavailable", retryable=True)


class _JubeError(Exception):
    """Shape of the adapter's JubeAdapterError: code, reason, retryable."""

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(reason)
        self.code = "upstream_unavailable"
        self.reason = reason
        self.retryable = retryable
