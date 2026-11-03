"""Dependency-free checks for the Vati OpenAPI contract and its examples (TAX-018).

The schema validator implements a strict subset of JSON Schema 2020-12. Any keyword it
does not implement is reported as an error, so nothing in the contract is silently
unchecked. Run ``python3 integration/api/contract.py`` to lint the contract and validate
every example pair and event.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

API_DIR = Path(__file__).resolve().parent
SPEC_PATH = API_DIR / "openapi.json"
HTTP_EXAMPLES = API_DIR / "examples" / "http"
EVENT_EXAMPLES = API_DIR / "examples" / "events"

TENANT_HEADER = "x-vati-tenant-id"
IDEMPOTENCY_HEADER = "idempotency-key"
CORRELATION_HEADER = "x-correlation-id"
MUTATING_METHODS = {"post", "put", "patch", "delete"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
BASE_ERRORS = {"400", "401", "403", "404", "429", "503"}
MUTATION_ERRORS = {"409", "422"}

VALIDATION_KEYWORDS = {
    "$ref",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "enum",
    "const",
    "pattern",
    "format",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "items",
    "minItems",
    "maxItems",
    "anyOf",
}
ANNOTATION_KEYWORDS = {"description", "default", "title", "examples", "deprecated"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UTC_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


def load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


# --- schema validation -----------------------------------------------------------------


def resolve(root: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise KeyError(f"only local refs are supported: {ref}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _type_matches(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise ValueError(f"unsupported type {name!r}")


def _format_ok(value: str, fmt: str) -> bool:
    if fmt == "uuid":
        return bool(UUID_RE.match(value))
    if fmt == "date-time":
        return bool(UTC_DATETIME_RE.match(value))
    if fmt == "date":
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return len(value) == 10
    raise ValueError(f"unsupported format {fmt!r}")


def validate(value: Any, schema: Any, root: dict[str, Any], where: str = "$") -> list[str]:
    """Return human-readable errors; an empty list means the value conforms."""
    if not isinstance(schema, dict):
        return [f"{where}: schema must be an object"]
    unknown = set(schema) - VALIDATION_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        return [f"{where}: unsupported schema keyword(s) {sorted(unknown)}"]
    if "$ref" in schema:
        return validate(value, resolve(root, schema["$ref"]), root, where)
    errors: list[str] = []
    if "anyOf" in schema:
        results = [validate(value, option, root, where) for option in schema["anyOf"]]
        if any(not r for r in results):
            return []
        # Report the alternative whose type matched; a bare type mismatch explains nothing.
        type_only = f"{where}: expected "
        detailed = [r for r in results if not (len(r) == 1 and r[0].startswith(type_only))]
        if detailed:
            return min(detailed, key=len)
        return [f"{where}: does not match any allowed alternative"]
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_matches(value, n) for n in names):
            return [f"{where}: expected {'/'.join(names)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} not in allowed values")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: does not match pattern")
        if "format" in schema and not _format_ok(value, schema["format"]):
            errors.append(f"{where}: invalid {schema['format']}")
    if _type_matches(value, "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{where}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, item in enumerate(value):
                errors += validate(item, schema["items"], root, f"{where}[{i}]")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing required property {key!r}")
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors += validate(item, props[key], root, f"{where}.{key}")
            elif extra is False:
                errors.append(f"{where}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                errors += validate(item, extra, root, f"{where}.{key}")
    return errors


def lint_schema(schema: Any, root: dict[str, Any], where: str) -> list[str]:
    """Statically reject unsupported keywords, floats and unresolved references."""
    if not isinstance(schema, dict):
        return [f"{where}: schema must be an object"]
    errors: list[str] = []
    unknown = set(schema) - VALIDATION_KEYWORDS - ANNOTATION_KEYWORDS
    if unknown:
        errors.append(f"{where}: unsupported schema keyword(s) {sorted(unknown)}")
    if "$ref" in schema:
        try:
            resolve(root, schema["$ref"])
        except (KeyError, TypeError):
            errors.append(f"{where}: unresolved $ref {schema['$ref']}")
    types = schema.get("type", [])
    for name in types if isinstance(types, list) else [types]:
        if name == "number":
            errors.append(f"{where}: floating-point numbers are not allowed; use decimal strings")
        elif name not in {"null", "boolean", "integer", "string", "array", "object"}:
            errors.append(f"{where}: unknown type {name!r}")
    if "format" in schema and schema["format"] not in {"uuid", "date-time", "date"}:
        errors.append(f"{where}: unsupported format {schema['format']!r}")
    for key, sub in schema.get("properties", {}).items():
        errors += lint_schema(sub, root, f"{where}.{key}")
    for key in ("items", "additionalProperties"):
        if isinstance(schema.get(key), dict):
            errors += lint_schema(schema[key], root, f"{where}/{key}")
    for i, sub in enumerate(schema.get("anyOf", [])):
        errors += lint_schema(sub, root, f"{where}/anyOf/{i}")
    return errors


# --- contract rules --------------------------------------------------------------------


def _deref(spec: dict[str, Any], node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node:
        node = resolve(spec, node["$ref"])
    return node


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, item in spec.get("paths", {}).items()
        for method, op in item.items()
        if method in HTTP_METHODS
    ]


def lint_spec(spec: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(spec.get("openapi", "")).startswith("3.1."):
        errors.append("openapi must be 3.1.x")
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        errors += lint_schema(schema, spec, f"#/components/schemas/{name}")
    seen_ids: set[str] = set()
    for method, path, op in operations(spec):
        where = f"{method.upper()} {path}"
        if not path.startswith("/api/v1/"):
            errors.append(f"{where}: product routes must be under /api/v1/")
        op_id = op.get("operationId")
        if not op_id or op_id in seen_ids:
            errors.append(f"{where}: missing or duplicate operationId")
        seen_ids.add(op_id or "")
        try:
            params = [_deref(spec, p) for p in op.get("parameters", [])]
        except (KeyError, TypeError):
            errors.append(f"{where}: unresolved parameter $ref")
            continue
        by_name = {(p["in"], p["name"].lower()): p for p in params}
        for p in params:
            errors += lint_schema(p.get("schema"), spec, f"{where} parameter {p['name']}")
        tenant = by_name.get(("header", TENANT_HEADER))
        if not tenant or tenant.get("required") is not True:
            errors.append(f"{where}: must require the X-Vati-Tenant-ID header")
        for name in re.findall(r"{([^}]+)}", path):
            param = by_name.get(("path", name.lower()))
            if not param or param.get("required") is not True:
                errors.append(f"{where}: path parameter {name} must be declared and required")
        responses = op.get("responses", {})
        expected = BASE_ERRORS | (MUTATION_ERRORS if method in MUTATING_METHODS else set())
        missing = expected - set(responses)
        if missing:
            errors.append(f"{where}: missing error responses {sorted(missing)}")
        if method in MUTATING_METHODS:
            key = by_name.get(("header", IDEMPOTENCY_HEADER))
            if not key or key.get("required") is not True:
                errors.append(f"{where}: mutations must require Idempotency-Key")
            if not op.get("requestBody", {}).get("required"):
                errors.append(f"{where}: mutation request body must be required")
        for status, raw in responses.items():
            try:
                response = _deref(spec, raw)
                schema = response["content"]["application/json"]["schema"]
            except (KeyError, TypeError):
                errors.append(f"{where} {status}: needs an application/json schema")
                continue
            errors += lint_schema(schema, spec, f"{where} {status}")
            if CORRELATION_HEADER not in {h.lower() for h in response.get("headers", {})}:
                errors.append(f"{where} {status}: must declare X-Correlation-ID")
            if status[0] in "45" and schema.get("$ref") != "#/components/schemas/ErrorResponse":
                errors.append(f"{where} {status}: errors must use ErrorResponse")
    return errors


# --- request and example checks --------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str


def match_operation(
    spec: dict[str, Any], method: str, path: str
) -> tuple[str, dict[str, Any], dict[str, str]] | None:
    """Match a concrete path; template segments match empty values so they can be rejected."""
    segments = path.split("?", 1)[0].split("/")
    for op_method, template, op in operations(spec):
        if op_method != method.lower():
            continue
        parts = template.split("/")
        if len(parts) != len(segments):
            continue
        params: dict[str, str] = {}
        for expected, actual in zip(parts, segments):
            if expected.startswith("{") and expected.endswith("}"):
                params[expected[1:-1]] = actual
            elif expected != actual:
                break
        else:
            return template, op, params
    return None


def _violation_code(param: dict[str, Any]) -> str:
    if param["in"] == "header" and param["name"].lower() == TENANT_HEADER:
        return "missing_tenant_context"
    if param["in"] == "path" and param["name"] == "case_id":
        return "invalid_case_id"
    return "invalid_request"


def check_request(
    spec: dict[str, Any],
    method: str,
    path: str,
    headers: dict[str, str],
    body: Any = None,
    query: dict[str, str] | None = None,
) -> list[Violation]:
    """Apply the contract's request rules; the API implementation must reject the same input."""
    match = match_operation(spec, method, path)
    if match is None:
        return [Violation("not_found", f"no {method.upper()} route for {path}")]
    _, op, path_values = match
    lowered = {k.lower(): v for k, v in headers.items()}
    sources = {"header": lowered, "path": path_values, "query": query or {}}
    violations: list[Violation] = []
    for raw in op.get("parameters", []):
        param = _deref(spec, raw)
        values = sources[param["in"]]
        key = param["name"].lower() if param["in"] == "header" else param["name"]
        value = values.get(key)
        if value in (None, ""):
            if param.get("required"):
                violations.append(Violation(_violation_code(param), f"missing {param['name']}"))
            continue
        problems = validate(value, param["schema"], spec, param["name"])
        if problems:
            violations.append(Violation(_violation_code(param), "; ".join(problems)))
    request_body = op.get("requestBody")
    if request_body:
        if body is None:
            if request_body.get("required"):
                violations.append(Violation("invalid_request", "missing request body"))
        else:
            schema = request_body["content"]["application/json"]["schema"]
            problems = validate(body, schema, spec, "body")
            if problems:
                violations.append(Violation("invalid_request", "; ".join(problems)))
    elif body is not None:
        violations.append(Violation("invalid_request", "operation takes no request body"))
    return violations


def _data_items(body: Any) -> list[dict[str, Any]]:
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def check_example(spec: dict[str, Any], example: dict[str, Any]) -> list[str]:
    """Validate one request/response pair, including tenant and correlation consistency."""
    name = example.get("name", "<unnamed>")
    if example.get("synthetic") is not True:
        return [f"{name}: examples must be marked synthetic"]
    req, res = example["request"], example["response"]
    errors: list[str] = []
    violations = check_request(
        spec, req["method"], req["path"], req.get("headers", {}), req.get("body"), req.get("query")
    )
    status = str(res["status"])
    error_code = (res.get("body") or {}).get("error", {}).get("code")
    if example.get("request_conformant", True):
        errors += [f"{name}: request {v.code}: {v.detail}" for v in violations]
    elif not violations:
        errors.append(f"{name}: request was expected to be rejected but conforms")
    elif error_code not in {v.code for v in violations}:
        errors.append(f"{name}: error code {error_code!r} does not match request violation")
    match = match_operation(spec, req["method"], req["path"])
    if match is None:
        return errors + [f"{name}: no matching operation"]
    template, op, path_values = match
    if example.get("operation_id") != op["operationId"]:
        errors.append(f"{name}: operation_id does not match {req['method']} {template}")
    if status not in op["responses"]:
        return errors + [f"{name}: status {status} not declared"]
    response = _deref(spec, op["responses"][status])
    schema = response["content"]["application/json"]["schema"]
    errors += [f"{name}: response {e}" for e in validate(res.get("body"), schema, spec)]
    res_headers = {k.lower(): v for k, v in res.get("headers", {}).items()}
    for header, spec_header in response.get("headers", {}).items():
        value = res_headers.get(header.lower())
        spec_header = _deref(spec, spec_header)
        if value is None:
            if spec_header.get("required"):
                errors.append(f"{name}: response missing header {header}")
            continue
        errors += [f"{name}: {e}" for e in validate(value, spec_header["schema"], spec, header)]
    req_headers = {k.lower(): v for k, v in req.get("headers", {}).items()}
    correlation = res_headers.get(CORRELATION_HEADER)
    if CORRELATION_HEADER in req_headers and req_headers[CORRELATION_HEADER] != correlation:
        errors.append(f"{name}: response must echo the request correlation ID")
    if error_code and res["body"]["error"].get("correlation_id") != correlation:
        errors.append(f"{name}: error correlation_id must equal X-Correlation-ID")
    if status.startswith("2"):
        tenant = req_headers.get(TENANT_HEADER)
        case_id = path_values.get("case_id")
        for item in _data_items(res.get("body")):
            if item.get("tenant_id") != tenant:
                errors.append(f"{name}: response tenant_id differs from request tenant")
            if case_id and item.get("case_id") != case_id:
                errors.append(f"{name}: response case_id differs from path case_id")
    return errors


def check_event(spec: dict[str, Any], event: Any) -> list[str]:
    schema = spec["components"]["schemas"]["EventEnvelope"]
    return validate(event, schema, spec, "event")


def load_examples(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def check_all(spec: dict[str, Any] | None = None) -> list[str]:
    spec = copy.deepcopy(spec) if spec is not None else load_spec()
    errors = lint_spec(spec)
    for example in load_examples(HTTP_EXAMPLES):
        errors += check_example(spec, example)
    for event in load_examples(EVENT_EXAMPLES):
        errors += [f"{event.get('event_id')}: {e}" for e in check_event(spec, event)]
    return errors


def main() -> int:
    errors = check_all()
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1
    count = len(load_examples(HTTP_EXAMPLES))
    events = len(load_examples(EVENT_EXAMPLES))
    print(f"Vati OpenAPI contract valid: {count} HTTP examples, {events} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
