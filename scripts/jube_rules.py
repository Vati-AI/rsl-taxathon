"""Evaluate, apply and verify the versioned synthetic tax-risk rules (TAX-035).

evaluate  deterministic results for the five TAX-024 scenarios (no Jube needed)
apply     create/update the Jube model, request XPaths and activation rules from the ruleset;
          --approve-local also approves them as the local Administrator (landlord self-approval,
          local synthetic stack only; a shared deployment needs a separate human checker)
verify    invoke the live Jube model per scenario and compare activations with the evaluator

Uses the isolated stack from scripts/jube_dev.py. Never prints secrets; never creates cases.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration" / "jube"))

import case_lifecycle  # noqa: E402
import tax_risk  # noqa: E402

_spec = importlib.util.spec_from_file_location("jube_dev", ROOT / "scripts" / "jube_dev.py")
assert _spec and _spec.loader
jube_dev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jube_dev)

Request = Callable[..., tuple[int, Any]]


def model_body(ruleset: dict[str, Any]) -> dict[str, Any]:
    model = ruleset["jube_model"]
    return {
        "name": model["name"],
        "entryName": model["entry_name"],
        "entryXPath": "$." + model["entry_field"],
        "entryPayloadLocationTypeId": 0,
        "referenceDateName": model["reference_date_name"],
        "referenceDateXPath": "$." + model["reference_date_field"],
        "referenceDatePayloadLocationTypeId": 1,
        "enableCache": False,
        "cacheTtlInterval": "d",  # interval fields are validated even when the feature is off
        "cacheTtlIntervalValue": 1,
        "maxActivationWatcherInterval": "h",
        "maxActivationWatcherValue": 1,
        "maxActivationWatcherThreshold": 100,
        "activationWatcherSample": 0,
        "maxResponseElevationInterval": "d",
        "maxResponseElevationValue": 1,
        "enableTtlCounter": False,
        "enableSanctionCache": False,
        "enableRdbmsArchive": True,
        "enableActivationArchive": True,
        "enableActivationWatcher": False,
        "enableResponseElevationLimit": False,
        "enableSampling": False,
        "enableImplicitAsync": False,
        "maxResponseElevation": 0,
        "active": True,
    }


def xpath_body(model_id: int, feature: dict[str, Any]) -> dict[str, Any]:
    return {
        "entityAnalysisModelId": model_id,
        "name": feature["jube_name"],
        "dataTypeId": tax_risk.FEATURE_TYPES[feature["type"]],
        "xPath": "$." + feature["jube_name"],
        "defaultValue": "",
        "enableOverride": False,
        "cache": False,
        "reportTable": False,
        "responsePayload": True,
        "searchKey": False,
        "active": True,
    }


def rule_body(
    ruleset: dict[str, Any], model_id: int, rule: dict[str, Any], priority: int
) -> dict[str, Any]:
    return {
        "entityAnalysisModelId": model_id,
        "name": tax_risk.jube_rule_name(rule),
        "priority": priority,
        "ruleScriptTypeId": 2,  # coder script, as in Jube's seeded rules
        "coderRuleScript": tax_risk.jube_script(ruleset, rule),
        "builderRuleScript": "Return False",
        "enableCaseWorkflow": False,  # case creation belongs to TAX-034's adapter
        "enableResponseElevation": False,
        "enableNotification": False,
        "enableTtlCounter": False,
        "enableOverride": False,
        "enableForce": False,
        "activationSample": 1,
        "visible": True,
        "responsePayload": True,
        "reportTable": False,
        "enableReprocessing": False,
        "active": True,
    }


def ok(status: int, body: Any, what: str) -> Any:
    if status not in (200, 201, 204):
        detail = json.dumps(body)[:500] if isinstance(body, (dict, list)) else ""
        raise SystemExit(f"{what} failed with HTTP {status} {detail}")
    return body


def find_model(request: Request, token: str, name: str) -> dict[str, Any] | None:
    models = ok(*request("GET", "/api/EntityAnalysisModel/", token=token), "list models") or []
    return next((m for m in models if m.get("name") == name), None)


def apply(ruleset: dict[str, Any], request: Request, token: str) -> dict[str, Any]:
    """Idempotent: unchanged entities are left alone, changed ones are updated (new Jube version)."""
    summary: dict[str, list[str]] = {"created": [], "updated": [], "unchanged": []}
    model = find_model(request, token, ruleset["jube_model"]["name"])
    if model is None:
        ok(
            *request("POST", "/api/EntityAnalysisModel/", model_body(ruleset), token),
            "create model",
        )
        model = find_model(request, token, ruleset["jube_model"]["name"])
        if model is None:
            raise SystemExit("model was not readable after creation")
        summary["created"].append("model")
    else:
        summary["unchanged"].append("model")
    model_id = int(model["id"])

    route = f"/api/EntityAnalysisModelRequestXPath/ByEntityAnalysisModelId/{model_id}"
    existing = {x["name"]: x for x in ok(*request("GET", route, token=token), "list xpaths") or []}
    for feature in ruleset["features"]:
        body = xpath_body(model_id, feature)
        current = existing.get(feature["jube_name"])
        if current is None:
            ok(*request("POST", "/api/EntityAnalysisModelRequestXPath/", body, token), "xpath")
            summary["created"].append(feature["jube_name"])
        elif (current["dataTypeId"], current["xPath"]) != (body["dataTypeId"], body["xPath"]):
            body.update(id=current["id"])
            ok(*request("PUT", "/api/EntityAnalysisModelRequestXPath/", body, token), "xpath")
            summary["updated"].append(feature["jube_name"])
        else:
            summary["unchanged"].append(feature["jube_name"])

    gateway = ruleset["jube_model"]["gateway"]
    route = f"/api/EntityAnalysisModelGatewayRule/ByEntityAnalysisModelId/{model_id}"
    gateways = ok(*request("GET", route, token=token), "list gateway rules") or []
    if not any(g.get("name") == gateway["name"] for g in gateways):
        body = {
            "entityAnalysisModelId": model_id,
            "name": gateway["name"],
            "description": gateway["description"],
            "priority": 0,
            "ruleScriptTypeId": 2,
            "coderRuleScript": gateway["coder_rule_script"],
            "builderRuleScript": "Return False",
            "maxResponseElevation": 0,
            "gatewaySample": 1,
            "responsePayload": False,
            "active": True,
        }
        ok(*request("POST", "/api/EntityAnalysisModelGatewayRule/", body, token), "gateway rule")
        summary["created"].append(gateway["name"])
    else:
        summary["unchanged"].append(gateway["name"])

    route = f"/api/EntityAnalysisModelActivationRule/ByEntityAnalysisModelId/{model_id}"
    rules = ok(*request("GET", route, token=token), "list rules") or []
    for priority, rule in enumerate(ruleset["rules"]):
        body = rule_body(ruleset, model_id, rule, priority)
        prefix = rule["rule_id"] + " v"
        current = next((r for r in rules if str(r.get("name", "")).startswith(prefix)), None)
        if current is None:
            ok(*request("POST", "/api/EntityAnalysisModelActivationRule/", body, token), "rule")
            summary["created"].append(body["name"])
        elif (current["name"], current["coderRuleScript"]) != (
            body["name"],
            body["coderRuleScript"],
        ):
            body.update(id=current["id"], guid=current.get("guid"))
            ok(*request("PUT", "/api/EntityAnalysisModelActivationRule/", body, token), "rule")
            summary["updated"].append(body["name"])
        else:
            summary["unchanged"].append(body["name"])
    return {"model_id": model_id, "model_guid": model["guid"], **summary}


# Lives in the non-landlord Vati Jube tenant (TAX-034). Landlord-tenant users bypass every Jube
# permission, which is why the first TAX-035 identity (LEGACY_*) is retired by apply.
INVOKER_ROLE = "Vati risk invoke (local)"
INVOKER_USER = "vati-risk-invoker"
API_KEY_ENV = "JUBE_RISK_INVOKER_API_KEY"
LEGACY_INVOKER_USER = "vati-tax-risk-invoker"


def find_named(request: Request, token: str, route: str, name: str) -> dict[str, Any] | None:
    items = ok(*request("GET", route, token=token), f"list {route}") or []
    return next((item for item in items if item.get("name") == name), None)


def provision_invoker(
    request: Request, token: str, model_guid: str, store: Callable[[str], None]
) -> list[str]:
    """Dedicated invoke identity: a role with no permissions, granted only this model.

    Jube admits invocations only from users holding a live API key whose role is granted the
    model. The key is shown once by Jube; it goes straight to the ignored .local/jube.env.
    """
    done = []
    role = find_named(request, token, "/api/RoleRegistry/", INVOKER_ROLE)
    if role is None:
        body = {
            "name": INVOKER_ROLE,
            "active": True,
            "description": "TAX-035 synthetic invoke only",
        }
        role = ok(*request("POST", "/api/RoleRegistry/", body, token), "create role")
        done.append("role")
    user = find_named(request, token, "/api/UserRegistry/", INVOKER_USER)
    if user is None:
        body = {
            "name": INVOKER_USER,
            "roleRegistryGuid": role["guid"],
            "email": "invoker@example.invalid",
            "active": True,
        }
        ok(*request("POST", "/api/UserRegistry/", body, token), "create user")
        user = find_named(request, token, "/api/UserRegistry/", INVOKER_USER)
        if user is None:
            raise SystemExit("invoke user was not readable after creation")
        done.append("user")
    grants_route = f"/api/EntityAnalysisModelRole/ByEntityAnalysisModelGuid/{model_guid}"
    grants = ok(*request("GET", grants_route, token=token), "list grants") or []
    if not any(g.get("roleRegistryGuid") == role["guid"] for g in grants):
        body = {"entityAnalysisModelGuid": model_guid, "roleRegistryGuid": role["guid"]}
        ok(*request("POST", "/api/EntityAnalysisModelRole/", body, token), "grant model")
        done.append("model_grant")
    if not jube_dev.read_env().get(API_KEY_ENV):
        # Keys not in the env file are unrecoverable; revoke them rather than leave them live.
        keys_route = f"/api/UserRegistryApiKey/ByUserRegistryId/{user['id']}"
        for old in ok(*request("GET", keys_route, token=token), "list API keys") or []:
            ok(*request("DELETE", f"/api/UserRegistryApiKey/{old['id']}", token=token), "revoke")
            done.append("revoked_orphan_api_key")
        body = {"userRegistryId": user["id"], "name": "tax-035-local", "description": "synthetic"}
        created = ok(*request("POST", "/api/UserRegistryApiKey/", body, token), "create API key")
        # Jube returns the clear key once, in the create response's apiKeyDisplay.
        key = (created or {}).get("apiKeyDisplay")
        if not isinstance(key, str) or len(key) < 20:
            raise SystemExit("Jube did not return a usable API key")
        store(key)
        done.append("api_key")
    return done


def store_api_key(key: str, name: str = API_KEY_ENV) -> None:
    env_file = jube_dev.ENV_FILE
    with env_file.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={key}\n")
    env_file.chmod(0o600)


def retire_landlord_invoker(request: Request, token: str) -> list[str]:
    """Revoke and deactivate the first TAX-035 invoker, created in Jube's landlord tenant.

    Jube grants every permission in every tenant to users whose role belongs to the landlord
    tenant (Jube docs Concepts/MultiTenancy), so that key could administer the installation.
    Run while Administrator is still in the landlord tenant, where that user lives.
    """
    done: list[str] = []
    user = find_named(request, token, "/api/UserRegistry/", LEGACY_INVOKER_USER)
    if user is None:
        return done
    keys_route = f"/api/UserRegistryApiKey/ByUserRegistryId/{user['id']}"
    for old in ok(*request("GET", keys_route, token=token), "list legacy API keys") or []:
        ok(*request("DELETE", f"/api/UserRegistryApiKey/{old['id']}", token=token), "revoke")
        done.append("revoked_landlord_api_key")
    if user.get("active"):
        ok(*request("PUT", "/api/UserRegistry/", {**user, "active": False}, token), "deactivate")
        done.append("deactivated_landlord_user")
    return done


def api_key_request(method: str, path: str, body: Any = None, token: str | None = None):
    """Like jube_dev.request, but authenticates with the invoke user's X-API-KEY."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        jube_dev.base_url() + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Content-Type": "application/json", "X-API-KEY": token or ""},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as error:
        raw, status = error.read(), error.code
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, None


def approve_pending(request: Request, token: str, model_id: int) -> list[str]:
    """Approve pending entities in dependency order (retries let prerequisites land first)."""
    approved: list[str] = []
    for _ in range(5):
        route = f"/api/EntityApproval/Pending?modelId={model_id}"
        pending = ok(*request("GET", route, token=token), "list pending") or []
        if not pending:
            break
        for item in pending:
            decision = {
                "kind": item["kind"],
                "entityId": item["entityId"],
                "version": item["version"],
                "note": "TAX-035 local synthetic stack: ruleset reviewed in Git PR",
            }
            status, _ = request("POST", "/api/EntityApproval/Approve", decision, token)
            if status in (200, 204):
                approved.append(f"{item.get('name')} v{item['version']}")
    return approved


def request_sync(request: Request, token: str) -> str:
    """Ask Jube's engine to load the current tenant's approved models (UI "Synchronise").

    The engine only schedules tenants that have a synchronisation schedule row, so a newly
    created tenant's model is never loaded (invoke answers 404) until one is requested.
    """
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    body = {"scheduleDate": when}
    ok(*request("POST", "/api/EntityAnalysisModelSynchronisationSchedule/", body, token), "sync")
    return when


def activation_names(body: Any) -> set[str]:
    """Matched activation rule names from a Jube invoke response."""
    found: set[str] = set()
    if not isinstance(body, dict):
        return found
    activation = body.get("activation") or body.get("Activation") or {}
    if isinstance(activation, dict):
        found.update(str(name) for name in activation)
    elif isinstance(activation, list):
        for item in activation:
            name = item.get("name") or item.get("Name") if isinstance(item, dict) else item
            if name:
                found.add(str(name))
    return found


def verify(
    ruleset: dict[str, Any], request: Request, token: str, guid: str, wait_s: int = 120
) -> dict[str, Any]:
    records = json.loads(tax_risk.RECORDS_PATH.read_text(encoding="utf-8"))
    expected = tax_risk.evaluate_all(ruleset, records)
    # Jube keys activations by rule name with spaces replaced by underscores.
    names = {r["rule_id"]: tax_risk.jube_rule_name(r).replace(" ", "_") for r in ruleset["rules"]}
    deadline = time.monotonic() + wait_s
    while True:
        rows, mismatches = [], 0
        for record in records:
            payload = tax_risk.jube_payload(ruleset, record)
            status, body = request(
                "POST", f"/api/Invoke/EntityAnalysisModel/{guid}", payload, token
            )
            fired = activation_names(body) if status == 200 else set()
            want = {
                names[r["rule_id"]] for r in expected[record["case_id"]] if r["outcome"] == "signal"
            }
            mismatches += int(status != 200 or (fired & set(names.values())) != want)
            rows.append(
                {
                    "case_id": record["case_id"],
                    "http_status": status,
                    "jube_activations": sorted(fired & set(names.values())),
                    "expected_signals": sorted(want),
                }
            )
        if not mismatches or time.monotonic() > deadline:
            return {"model_guid": guid, "match": mismatches == 0, "scenarios": rows}
        time.sleep(10)  # approval publishes at the next synchronisation cycle


def summary_table(results: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case_id,
            "signals": [
                f"{r['code']} ({r['rule_id']} v{r['rule_version']})"
                for r in rows
                if r["outcome"] == "signal"
            ],
            "cleared": [r["code"] for r in rows if r["outcome"] == "cleared"],
            "no_signal": [
                r["rule_id"] for r in rows if r["outcome"] in ("no_signal", "not_applicable")
            ],
        }
        for case_id, rows in results.items()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("command", choices=["evaluate", "apply", "verify"])
    parser.add_argument("--full", action="store_true", help="evaluate: print every rule result")
    parser.add_argument("--approve-local", action="store_true", help="apply: self-approve locally")
    args = parser.parse_args()
    ruleset = tax_risk.load_ruleset()
    if args.command == "evaluate":
        results = tax_risk.evaluate_all(ruleset)
        print(json.dumps(results if args.full else summary_table(results), indent=2))
        return
    token = jube_dev.login()
    workflow = case_lifecycle.load_workflow()
    retired = retire_landlord_invoker(jube_dev.request, token) if args.command == "apply" else []
    with jube_dev.admin_in_tenant(token, workflow.tenant_name, workflow.tenant_description) as tid:
        if args.command == "apply":
            result = apply(ruleset, jube_dev.request, token)
            if args.approve_local:
                result["approved"] = approve_pending(jube_dev.request, token, result["model_id"])
            result["invoker"] = retired + provision_invoker(
                jube_dev.request, token, result["model_guid"], store_api_key
            )
            result["jube_tenant_id"] = tid
            result["sync_requested"] = request_sync(jube_dev.request, token)
            print(json.dumps(result, indent=2))
            return
        model = find_model(jube_dev.request, token, ruleset["jube_model"]["name"])
    api_key = jube_dev.read_env().get(API_KEY_ENV)
    if model is None or not api_key:
        raise SystemExit("Model or invoke API key missing; run apply --approve-local first")
    result = verify(ruleset, api_key_request, api_key, model["guid"])
    print(json.dumps(result, indent=2))
    if not result["match"]:
        raise SystemExit("Jube activations differ from the deterministic evaluator")


if __name__ == "__main__":
    main()
