"""Model router (TAX-089, ADR-031). Agents use ``get_model(role)`` only.

Provider adapters (``ai.models.providers``) and credentials (``ai.models.config``)
are internal to this package: graphs and tools must not import them.
"""

from ai.models.router import CompletionTask, ModelOutput, RoutedModel, get_model

__all__ = ["CompletionTask", "ModelOutput", "RoutedModel", "get_model"]
