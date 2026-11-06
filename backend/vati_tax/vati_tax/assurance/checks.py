"""The one place the assurance step reaches into ``ai/`` (TAX-121, ADR-042).

Like ``portal/presubmission.py`` for TAX-044, this adapter imports the frozen TAX-118
checks from the read-only ``ai/`` mount on the bench path: ``assess_taxpayer``
(ai/evaluation/assurance_pipeline.py, TAX-112-116 tools) and the TAX-116 rule register.
They are stdlib-only; nothing is installed for them. An ImportError means the mount is
missing and the step records ``not_run`` (``checks_not_installed``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CHECKS_MODULE = "ai.evaluation.assurance_pipeline"


def load() -> tuple[Callable[..., dict[str, Any]], str]:
    """``assess_taxpayer`` and the rule-register version it routes with."""
    from ai.evaluation.assurance_pipeline import assess_taxpayer
    from ai.workflows.rule_register import load_rule_register

    return assess_taxpayer, load_rule_register().version
