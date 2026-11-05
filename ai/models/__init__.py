"""Model router (TAX-089, ADR-031). Agents use ``get_model(role)`` only.

Provider adapters (``ai.models.providers``) and credentials (``ai.models.config``)
are internal to this package: graphs and tools must not import them. The pure
contract projections in ``ai.models.projection`` (TAX-097) may be used anywhere.
"""

from ai.models.projection import template_provenance, to_contract_provenance, to_disclosure
from ai.models.router import CompletionTask, ModelOutput, RoutedModel, get_model

__all__ = [
    "CompletionTask",
    "ModelOutput",
    "RoutedModel",
    "get_model",
    "template_provenance",
    "to_contract_provenance",
    "to_disclosure",
]
