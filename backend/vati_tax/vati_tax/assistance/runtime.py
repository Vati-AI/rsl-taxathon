"""The taxpayer assistant runtime for the Frappe service (TAX-100; ADR-031).

The only module in ``vati_tax.assistance`` that reaches into ``ai`` (the boundary
test in ``portal/test_presubmission.py`` lists it). The runtime is named by the
``vati_taxpayer_assistant`` hook (``"module:factory"``), defaulting to the TAX-098
assistant, and imported by name. When it cannot be imported on this deployment (no
LangGraph, or the ``ai`` package is not on the path) the AI operations answer
``503 upstream_unavailable``; nothing falls back to invented data.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from typing import Any

import frappe

HOOK = "vati_taxpayer_assistant"
DEFAULT = "ai.agents.taxpayer_assistant:make_assistant"

Runtime = Callable[[Mapping[str, Any]], Any]


def entry() -> str:
    hooks = frappe.get_hooks(HOOK) or []
    return str(hooks[-1]) if hooks else DEFAULT


def load() -> Runtime | None:
    module_name, _, attr = entry().partition(":")
    try:
        factory = getattr(importlib.import_module(module_name), attr)
    except (ImportError, AttributeError):
        frappe.logger("vati_tax.assistance").error(
            {"event": "assistant_runtime_unavailable", "entry": entry()}
        )
        return None
    return factory if callable(factory) else None
