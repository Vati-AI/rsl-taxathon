"""Provision and contract-test the Jube case lifecycle adapter (TAX-034).

provision  as Administrator, inside the non-landlord Vati Jube tenant: case workflow and
           statuses from integration/jube/case_workflow.v1.json, the adapter and officer roles
           (Read Write Case only, plus workflow/status grants) and the adapter's API key.
           Run `python3 scripts/jube_rules.py apply --approve-local` first (model + invoker).
officer    create the synthetic Jube user an officer UUID is assigned through
contract   live contract tests against the deployed stack with the adapter's own key:
           replay-safe creation, assignment, status, escalation, closure and least privilege

Uses the isolated stack from scripts/jube_dev.py. Never prints secrets. Synthetic data only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration" / "jube"))

import case_lifecycle  # noqa: E402
import tax_risk  # noqa: E402
from case_lifecycle import JubeAdapterError  # noqa: E402

_spec = importlib.util.spec_from_file_location("jube_rules", ROOT / "scripts" / "jube_rules.py")
assert _spec and _spec.loader
jube_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jube_rules)
jube_dev = jube_rules.jube_dev
ok = jube_rules.ok
find_named = jube_rules.find_named

Request = Callable[..., tuple[int, Any]]
LOCAL_CONFIG = ROOT / ".local" / "jube-case-workflow.json"
# Synthetic tenant from docs/PERMISSIONS.md; the one Vati tenant this Jube tenant serves locally.
DEFAULT_VATI_TENANT = "10000000-0000-4000-8000-000000000001"
STATUS_COLOURS = {False: ("#1f2937", "#e5e7eb"), True: ("#7c2d12", "#fed7aa")}


def status_body(workflow_id: int, status: case_lifecycle.StatusDef) -> dict[str, Any]:
    fore, back = STATUS_COLOURS[status.escalated]
    return {
        "caseWorkflowId": workflow_id,
        "name": status.name,
        "active": True,
        "locked": False,
        "priority": status.priority,
        "foreColor": fore,
        "backColor": back,
        # No notification or webhook: status changes must not leave Jube as side effects.
        "enableHttpEndpoint": False,
        "httpEndpointTypeId": 0,
        "enableNotification": False,
        "notificationTypeId": 0,
    }


def ensure_role(
    request: Request, token: str, identity: dict[str, Any], permission_ids: dict[str, int]
) -> tuple[dict[str, Any], list[str]]:
    """Role holding exactly the named permissions; extra active permissions are removed."""
    done: list[str] = []
    role = find_named(request, token, "/api/RoleRegistry/", identity["role"])
    if role is None:
        body = {"name": identity["role"], "active": True, "description": "TAX-034 synthetic"}
        ok(*request("POST", "/api/RoleRegistry/", body, token), "create role")
        role = find_named(request, token, "/api/RoleRegistry/", identity["role"])
        if role is None:
            raise SystemExit("role was not readable after creation")
        done.append(f"role:{identity['role']}")
    wanted = {permission_ids[name] for name in identity["permission_names"]}
    grants = ok(*request("GET", "/api/RoleRegistryPermission/", token=token), "list grants") or []
    mine = [g for g in grants if g.get("roleRegistryId") == role["id"] and g.get("active")]
    for grant in mine:
        if grant["permissionSpecificationId"] not in wanted:
            ok(*request("DELETE", f"/api/RoleRegistryPermission/{grant['id']}", token=token), "rm")
            done.append(f"removed_permission:{grant['permissionSpecificationId']}")
    for spec_id in sorted(wanted - {g["permissionSpecificationId"] for g in mine}):
        body = {"roleRegistryId": role["id"], "permissionSpecificationId": spec_id, "active": True}
        ok(*request("POST", "/api/RoleRegistryPermission/", body, token), "grant permission")
        done.append(f"permission:{spec_id}")
    return role, done


def ensure_workflow_access(
    request: Request, token: str, role_guid: str, workflow: dict[str, Any], statuses: list[dict]
) -> list[str]:
    """Jube shows a case only to roles granted both its workflow and its current status."""
    done = []
    route = f"/api/CaseWorkflowRole/ByCaseWorkflowGuid/{workflow['guid']}"
    if not any(
        g.get("roleRegistryGuid") == role_guid
        for g in ok(*request("GET", route, token=token), "wf roles") or []
    ):
        body = {"caseWorkflowGuid": workflow["guid"], "roleRegistryGuid": role_guid}
        ok(*request("POST", "/api/CaseWorkflowRole/", body, token), "grant workflow")
        done.append("workflow_grant")
    for status in statuses:
        route = f"/api/CaseWorkflowStatusRole/ByCaseWorkflowStatusGuid/{status['guid']}"
        grants = ok(*request("GET", route, token=token), "status roles") or []
        if not any(g.get("roleRegistryGuid") == role_guid for g in grants):
            body = {"caseWorkflowStatusGuid": status["guid"], "roleRegistryGuid": role_guid}
            ok(*request("POST", "/api/CaseWorkflowStatusRole/", body, token), "grant status")
            done.append(f"status_grant:{status['name']}")
    return done


def ensure_user(request: Request, token: str, name: str, role_guid: str) -> tuple[dict, bool]:
    user = find_named(request, token, "/api/UserRegistry/", name)
    if user is not None:
        if user.get("roleRegistryGuid") != role_guid or not user.get("active"):
            body = {**user, "roleRegistryGuid": role_guid, "active": True}
            ok(*request("PUT", "/api/UserRegistry/", body, token), "update user")
        return user, False
    body = {
        "name": name,
        "roleRegistryGuid": role_guid,
        "email": f"{name}@example.invalid",
        "active": True,
    }
    ok(*request("POST", "/api/UserRegistry/", body, token), "create user")
    user = find_named(request, token, "/api/UserRegistry/", name)
    if user is None:
        raise SystemExit("user was not readable after creation")
    return user, True


def provision(
    workflow_def: case_lifecycle.Workflow,
    ruleset: dict[str, Any],
    request: Request,
    token: str,
    vati_tenant_id: str,
    store_key: Callable[[str, str], None],
    env: dict[str, str],
) -> dict[str, Any]:
    """Idempotent: existing entities are reused; only missing or drifted ones change."""
    done: list[str] = []
    model = jube_rules.find_model(request, token, ruleset["jube_model"]["name"])
    if model is None:
        raise SystemExit("Risk model missing in the Vati tenant; run jube_rules.py apply first")
    workflow = find_named(request, token, "/api/CaseWorkflow/", workflow_def.name)
    if workflow is None:
        body = {
            "entityAnalysisModelId": model["id"],
            "name": workflow_def.name,
            "description": workflow_def.description,
            "active": True,
            "locked": False,
            "enableVisualisation": False,
        }
        ok(*request("POST", "/api/CaseWorkflow/", body, token), "create workflow")
        workflow = find_named(request, token, "/api/CaseWorkflow/", workflow_def.name)
        if workflow is None:
            raise SystemExit("workflow was not readable after creation")
        done.append("workflow")
    route = f"/api/CaseWorkflowStatus/ByCaseWorkflowId/{workflow['id']}"
    for status in workflow_def.statuses:
        current = next(
            (
                s
                for s in ok(*request("GET", route, token=token), "statuses") or []
                if s.get("name") == status.name
            ),
            None,
        )
        body = status_body(workflow["id"], status)
        if current is None:
            ok(*request("POST", "/api/CaseWorkflowStatus/", body, token), "create status")
            done.append(f"status:{status.name}")
        elif any(
            current.get(k) != body[k]
            for k in ("priority", "enableHttpEndpoint", "enableNotification", "active")
        ):
            body.update(id=current["id"], guid=current["guid"])
            ok(*request("PUT", "/api/CaseWorkflowStatus/", body, token), "update status")
            done.append(f"status_updated:{status.name}")
    statuses = [
        s
        for s in ok(*request("GET", route, token=token), "statuses") or []
        if s.get("name") in {d.name for d in workflow_def.statuses}
    ]
    specs = ok(*request("GET", "/api/PermissionSpecification/", token=token), "specs") or []
    permission_ids = {s["name"]: int(s["id"]) for s in specs}

    roles = {}
    for key in ("case_adapter", "officer"):
        identity = dict(workflow_def.identities[key])
        role, changed = ensure_role(request, token, identity, permission_ids)
        roles[key] = role
        done += changed
        done += ensure_workflow_access(request, token, role["guid"], workflow, statuses)

    adapter = workflow_def.identities["case_adapter"]
    user, created = ensure_user(request, token, adapter["user"], roles["case_adapter"]["guid"])
    done += ["adapter_user"] if created else []
    if not env.get(adapter["api_key_env"]):
        keys_route = f"/api/UserRegistryApiKey/ByUserRegistryId/{user['id']}"
        for old in ok(*request("GET", keys_route, token=token), "list API keys") or []:
            ok(*request("DELETE", f"/api/UserRegistryApiKey/{old['id']}", token=token), "revoke")
            done.append("revoked_orphan_api_key")
        body = {"userRegistryId": user["id"], "name": "tax-034-local", "description": "synthetic"}
        created_key = ok(*request("POST", "/api/UserRegistryApiKey/", body, token), "API key")
        api_key = (created_key or {}).get("apiKeyDisplay")
        if not isinstance(api_key, str) or len(api_key) < 20:
            raise SystemExit("Jube did not return a usable API key")
        store_key(api_key, adapter["api_key_env"])
        done.append("adapter_api_key")
    config = {
        "workflow_version": workflow_def.version,
        "vati_tenant_id": vati_tenant_id,
        "workflow_guid": workflow["guid"],
        "status_guids": {s["name"]: s["guid"] for s in statuses},
    }
    return {"changed": done, "config": config}


def write_local_config(config: dict[str, Any]) -> None:
    LOCAL_CONFIG.parent.mkdir(exist_ok=True)
    LOCAL_CONFIG.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def load_adapter(link_store: case_lifecycle.LinkStore) -> case_lifecycle.JubeCaseAdapter:
    """Adapter from ignored local config; fails at startup when anything is missing."""
    workflow = case_lifecycle.load_workflow()
    if not LOCAL_CONFIG.exists():
        raise SystemExit("Missing .local/jube-case-workflow.json; run jube_cases.py provision")
    local = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    env = jube_dev.read_env()
    config = case_lifecycle.AdapterConfig.build(
        workflow,
        base_url=jube_dev.base_url(),
        api_key=env.get(workflow.identities["case_adapter"]["api_key_env"]),
        vati_tenant_id=local["vati_tenant_id"],
        workflow_guid=local["workflow_guid"],
        status_guids=local["status_guids"],
    )
    return case_lifecycle.JubeCaseAdapter(config, workflow, link_store)


# --------------------------------------------------------------------------- contract


def expect_error(fn: Callable[[], Any], code: str, reason: str | None = None) -> dict[str, Any]:
    try:
        fn()
    except JubeAdapterError as error:
        if error.code != code or (reason is not None and error.reason != reason):
            raise AssertionError(f"expected {code}/{reason}, got {error.code}/{error.reason}")
        return {"code": error.code, "reason": error.reason, "http_status": error.http_status}
    raise AssertionError(f"expected {code}/{reason}, call succeeded")


def check(rows: list[dict[str, Any]], name: str, fn: Callable[[], Any]) -> Any:
    result = fn()
    rows.append({"check": name, "observed": result})
    return result


def invoke_risk(case_id: str, key: str, model_guid: str) -> int:
    """Invoke the TAX-035 model for a fresh synthetic case (Jube archives it for case creation)."""
    ruleset = tax_risk.load_ruleset()
    records = json.loads(tax_risk.RECORDS_PATH.read_text(encoding="utf-8"))
    record = next(r for r in records if r["case_id"] == "SYN-002")
    payload = tax_risk.jube_payload(ruleset, record)
    payload[ruleset["jube_model"]["entry_field"]] = case_id
    status, _ = jube_rules.api_key_request(
        "POST", f"/api/Invoke/EntityAnalysisModel/{model_guid}", payload, key
    )
    return status


def probe(key: str | None, path: str) -> int:
    return jube_rules.api_key_request("GET", path, token=key or "")[0]


def contract(model_guid: str, officer_id: str, wait_s: int = 90) -> dict[str, Any]:
    env = jube_dev.read_env()
    workflow = case_lifecycle.load_workflow()
    adapter_key = env[workflow.identities["case_adapter"]["api_key_env"]]
    invoker_key = env[jube_rules.API_KEY_ENV]
    links = case_lifecycle.InMemoryLinkStore()
    adapter = load_adapter(links)
    tenant = adapter.config.vati_tenant_id
    case_id = str(uuid.uuid4())  # fresh synthetic case per run
    corr = str(uuid.uuid4())
    rows: list[dict[str, Any]] = []

    check(
        rows, "risk invocation (invoker key)", lambda: invoke_risk(case_id, invoker_key, model_guid)
    )
    deadline = time.monotonic() + wait_s
    while True:  # Jube archives invocations asynchronously
        try:
            first = adapter.ensure_case(tenant, case_id, correlation_id=corr)
            break
        except JubeAdapterError as error:
            if error.reason != "risk_evaluation_missing" or time.monotonic() > deadline:
                raise
            time.sleep(3)
    rows.append(
        {
            "check": "create",
            "observed": {
                "changed": first.changed,
                "jube_case_id": first.case.jube_case_id,
                "status": first.case.status,
                "jube_status": first.case.jube_status,
                "rating": first.case.rating,
            },
        }
    )
    assert first.changed and first.case.status == "RECEIVED"
    jube_id = first.case.jube_case_id

    again = adapter.ensure_case(tenant, case_id, correlation_id=corr)
    check(
        rows,
        "replay via link",
        lambda: {"changed": again.changed, "same": again.case.jube_case_id == jube_id},
    )
    assert not again.changed and again.case.jube_case_id == jube_id
    # A crash after Jube created the case but before the link was recorded.
    fresh = load_adapter(case_lifecycle.InMemoryLinkStore())
    lost = fresh.ensure_case(tenant, case_id, correlation_id=corr)
    check(
        rows,
        "replay via Jube lookup (no link)",
        lambda: {"changed": lost.changed, "same": lost.case.jube_case_id == jube_id},
    )
    assert not lost.changed and lost.case.jube_case_id == jube_id
    body = {
        "caseWorkflowGuid": adapter.config.workflow_guid,
        "caseWorkflowStatusGuid": adapter.config.status_guids[workflow.initial_status],
        "caseKey": workflow.case_key,
        "caseKeyValue": case_id,
    }
    dup = jube_rules.api_key_request("POST", "/api/Case/CreateFromCaseKeyValue", body, adapter_key)[
        0
    ]
    check(rows, "raw duplicate create rejected by Jube", lambda: dup)
    assert dup == 409
    cases = jube_rules.api_key_request("GET", "/api/Case/", token=adapter_key)[1] or []
    count = sum(1 for c in cases if c.get("caseKeyValue") == case_id)
    check(rows, "Jube cases for case_id", lambda: count)
    assert count == 1

    check(
        rows,
        "update without rating",
        lambda: expect_error(
            lambda: adapter.transition(
                tenant, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=corr
            ),
            "invalid_domain_input",
            "jube_rating_required",
        ),
    )
    moved = adapter.transition(
        tenant, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=corr, rating=1
    )
    check(
        rows,
        "transition RECEIVED->VALIDATING",
        lambda: {
            "changed": moved.changed,
            "status": moved.case.status,
            "jube_status": moved.case.jube_status,
        },
    )
    assert moved.changed and moved.case.status == "VALIDATING"
    replay = adapter.transition(
        tenant, case_id, "VALIDATING", expected_status="RECEIVED", correlation_id=corr
    )
    check(
        rows, "transition replay", lambda: {"changed": replay.changed, "status": replay.case.status}
    )
    assert not replay.changed
    check(
        rows,
        "stale transition",
        lambda: expect_error(
            lambda: adapter.transition(
                tenant, case_id, "INVESTIGATING", expected_status="RECEIVED", correlation_id=corr
            ),
            "version_conflict",
            "stale_status",
        ),
    )
    inv = adapter.transition(
        tenant, case_id, "INVESTIGATING", expected_status="VALIDATING", correlation_id=corr
    )
    assert inv.case.status == "INVESTIGATING"

    check(
        rows,
        "assign unprovisioned officer",
        lambda: expect_error(
            lambda: adapter.assign(
                tenant, case_id, str(uuid.uuid4()), expected_officer_id=None, correlation_id=corr
            ),
            "invalid_domain_input",
            "officer_not_provisioned",
        ),
    )
    assigned = adapter.assign(
        tenant, case_id, officer_id, expected_officer_id=None, correlation_id=corr
    )
    check(
        rows,
        "assign officer",
        lambda: {
            "changed": assigned.changed,
            "assigned_officer_id": assigned.case.assigned_officer_id == officer_id,
            "jube_user": assigned.case.assigned_jube_user,
            "locked": assigned.case.locked,
        },
    )
    assert assigned.case.assigned_officer_id == officer_id
    re_assigned = adapter.assign(
        tenant, case_id, officer_id, expected_officer_id=None, correlation_id=corr
    )
    check(rows, "assign replay", lambda: {"changed": re_assigned.changed})
    assert not re_assigned.changed
    check(
        rows,
        "stale assignment",
        lambda: expect_error(
            lambda: adapter.assign(
                tenant, case_id, None, expected_officer_id=str(uuid.uuid4()), correlation_id=corr
            ),
            "version_conflict",
            "stale_assignment",
        ),
    )

    escalated = adapter.escalate(
        tenant, case_id, expected_status="INVESTIGATING", correlation_id=corr
    )
    check(
        rows,
        "escalate",
        lambda: {
            "changed": escalated.changed,
            "status": escalated.case.status,
            "escalated": escalated.case.escalated,
            "jube_status": escalated.case.jube_status,
        },
    )
    assert escalated.case.status == "AWAITING_HUMAN" and escalated.case.escalated
    assert (
        adapter.escalate(
            tenant, case_id, expected_status="INVESTIGATING", correlation_id=corr
        ).changed
        is False
    )

    check(
        rows,
        "resolve without decision",
        lambda: expect_error(
            lambda: adapter.transition(
                tenant, case_id, "RESOLVED", expected_status="AWAITING_HUMAN", correlation_id=corr
            ),
            "invalid_request",
            "invalid_uuid",
        ),
    )
    resolved = adapter.transition(
        tenant,
        case_id,
        "RESOLVED",
        expected_status="AWAITING_HUMAN",
        correlation_id=corr,
        decision_id=str(uuid.uuid4()),
    )
    check(
        rows,
        "resolve with decision",
        lambda: {"status": resolved.case.status, "closed_state": resolved.case.closed_state},
    )
    assert resolved.case.closed_state == "closed"
    check(
        rows,
        "closed case not reopened",
        lambda: expect_error(
            lambda: adapter.transition(
                tenant, case_id, "INVESTIGATING", expected_status="RESOLVED", correlation_id=corr
            ),
            "invalid_domain_input",
            "case_closed",
        ),
    )
    after = load_adapter(case_lifecycle.InMemoryLinkStore()).ensure_case(
        tenant, case_id, correlation_id=corr
    )
    check(
        rows,
        "create after closure finds the closed case",
        lambda: {"changed": after.changed, "same": after.case.jube_case_id == jube_id},
    )
    assert not after.changed and after.case.jube_case_id == jube_id
    read = adapter.get_case(tenant, case_id, correlation_id=corr)
    check(
        rows,
        "read",
        lambda: {
            "status": read.status,
            "jube_status": read.jube_status,
            "escalated": read.escalated,
            "assigned": read.assigned_officer_id == officer_id,
            "rating": read.rating,
            "authority": read.status_authority,
        },
    )
    check(
        rows,
        "other Vati tenant concealed",
        lambda: expect_error(
            lambda: adapter.get_case(str(uuid.uuid4()), case_id, correlation_id=corr),
            "not_found",
            "tenant_not_served",
        ),
    )

    privilege = {}
    for label, key in (("adapter", adapter_key), ("invoker", invoker_key), ("wrong_key", "x" * 40)):
        privilege[label] = {
            path: probe(key, path)
            for path in (
                "/api/Case/",
                "/api/EntityAnalysisModel/",
                "/api/UserRegistry/",
                "/api/RoleRegistry/",
                "/api/TenantRegistry/",
                "/api/CaseWorkflow/",
                "/api/UserInTenant/",
                "/api/CaseWorkflowStatus/",
                "/api/EntityAnalysisModelActivationRule/ByEntityAnalysisModelId/1",
            )
        }
    legacy = env.get("JUBE_TAX_RISK_API_KEY")
    if legacy:
        privilege["retired_landlord_invoker"] = {
            "/api/TenantRegistry/": probe(legacy, "/api/TenantRegistry/")
        }
        assert privilege["retired_landlord_invoker"]["/api/TenantRegistry/"] == 401
    rows.append({"check": "least privilege probes (HTTP status)", "observed": privilege})
    adapter_ok = privilege["adapter"]
    # Jube's Read Write Case permission (1) also lists the tenant's models and user-tenant
    # allocations (listPermissions in EntityAnalysisModelService and UserInTenantService);
    # Jube has no narrower case permission. Own tenant only; anything wider fails here.
    readable = {"/api/Case/", "/api/EntityAnalysisModel/", "/api/UserInTenant/"}
    assert {p for p, v in adapter_ok.items() if v == 200} == readable, adapter_ok
    assert all(v in (401, 403) for p, v in adapter_ok.items() if p not in readable), adapter_ok
    models = jube_rules.api_key_request("GET", "/api/EntityAnalysisModel/", token=adapter_key)[1]
    names = sorted(m.get("name") for m in models or [])
    rows.append({"check": "models visible to adapter (own tenant only)", "observed": names})
    assert names == [tax_risk.load_ruleset()["jube_model"]["name"]], names
    assert all(v in (401, 403) for v in privilege["invoker"].values()), privilege["invoker"]
    assert all(v == 401 for v in privilege["wrong_key"].values())
    return {"case_id": case_id, "jube_case_id": jube_id, "checks": rows, "passed": True}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["provision", "officer", "contract"])
    parser.add_argument("--vati-tenant-id", default=DEFAULT_VATI_TENANT)
    parser.add_argument("--officer-id", help="officer UUID (officer, contract)")
    args = parser.parse_args()
    workflow = case_lifecycle.load_workflow()
    ruleset = tax_risk.load_ruleset()
    token = jube_dev.login()
    officer_id = args.officer_id or "20000000-0000-4000-8000-000000000001"
    with jube_dev.admin_in_tenant(token, workflow.tenant_name, workflow.tenant_description):
        if args.command == "provision":
            result = provision(
                workflow,
                ruleset,
                jube_dev.request,
                token,
                args.vati_tenant_id,
                lambda key, name: jube_rules.store_api_key(key, name),
                jube_dev.read_env(),
            )
            write_local_config(result["config"])
            print(
                json.dumps(
                    {
                        "changed": result["changed"],
                        "statuses": len(result["config"]["status_guids"]),
                    },
                    indent=2,
                )
            )
            return
        role = find_named(
            jube_dev.request, token, "/api/RoleRegistry/", workflow.identities["officer"]["role"]
        )
        if role is None:
            raise SystemExit("Officer role missing; run provision first")
        name = workflow.identities["officer"]["user_prefix"] + str(uuid.UUID(officer_id))
        _, created = ensure_user(jube_dev.request, token, name, role["guid"])
        model = jube_rules.find_model(jube_dev.request, token, ruleset["jube_model"]["name"])
    if args.command == "officer":
        print(json.dumps({"officer_user": name, "created": created}))
        return
    if model is None:
        raise SystemExit("Risk model missing; run jube_rules.py apply first")
    print(json.dumps(contract(model["guid"], officer_id), indent=2))


if __name__ == "__main__":
    main()
