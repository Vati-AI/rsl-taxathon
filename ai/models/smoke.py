"""Router smoke (TAX-089). Offline by default; ``--live`` calls configured hosted roles.

    python -m ai.models.smoke          # every role through the mock chain, no network
    python -m ai.models.smoke --live   # one tiny synthetic prompt per configured hosted role

``--live`` forces ``VATI_MODEL_PROVIDER=hosted`` for this run, reads keys from the
environment or the ignored ``.env`` and skips roles without a key or model ID
(or set to ``none``). It prints, per
role, the observed model ID, latency, token use and rate-limit headers, never a
key. A live role that does not answer at level 0 makes the run fail. Only the
synthetic payload below is sent.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from typing import cast

from ai.models.config import HOSTED_PROVIDER, ROLES, load_settings
from ai.models.router import CompletionTask, get_model

SMOKE_PROMPT_VERSION = "tax089-smoke-v1"
SYNTHETIC_PAYLOAD = {
    "synthetic": True,
    "case_id": "SYN-SMOKE-001",
    "note": "Synthetic router smoke test. No taxpayer data.",
}


def _validate(output: object) -> str | None:
    if isinstance(output, dict) and output.get("status") == "ok":
        return None
    return "expected_status_ok"


def smoke_task() -> CompletionTask:
    return CompletionTask(
        name="router_smoke",
        prompt_version=SMOKE_PROMPT_VERSION,
        instructions='Reply with the JSON object {"status": "ok"}.',
        payload=SYNTHETIC_PAYLOAD,
        template=lambda _payload: {"status": "ok"},
        validate=_validate,
        max_output_tokens=256,
    )


def run(live: bool) -> int:
    settings = load_settings()
    # Offline runs are always mock (no network); live runs always use the hosted chain.
    settings = dataclasses.replace(settings, provider_mode="hosted" if live else "mock")
    report: dict[str, object] = {"live": live, "settings": settings.public_view(), "roles": {}}
    roles = report["roles"]
    assert isinstance(roles, dict)
    failed = False
    for role in ROLES:
        provider = HOSTED_PROVIDER[role]
        if live and not (settings.key_for(provider) and settings.model_ids.get(role)):
            roles[role] = {"skipped": f"{provider} key or {role} model ID not configured"}
            continue
        result = get_model(role, settings=settings).complete(smoke_task())
        p = result.provenance
        roles[role] = {
            "provider": p["provider"],
            "observed_model_id": p["model_id"],
            "fallback_level": p["fallback_level"],
            "latency_ms": p["latency_ms"],
            "tokens": p["tokens"],
            "rate_limit": p["rate_limit"],
            "validation": p["validation"],
            "attempts": [
                {"name": a["name"], "outcome": a["outcome"]}
                for a in cast(list[dict[str, object]], p["attempts"])
            ],
        }
        if live and p["fallback_level"] != 0:
            failed = True
        if not live and p["validation"] != {"status": "passed", "reason": None}:
            failed = True
    print(json.dumps(report, indent=2, sort_keys=True))
    if live and all("skipped" in v for v in roles.values()):
        print("Live smoke skipped: no hosted role is configured.", file=sys.stderr)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--live", action="store_true", help="call configured hosted roles")
    args = parser.parse_args(argv)
    return run(args.live)


if __name__ == "__main__":
    raise SystemExit(main())
