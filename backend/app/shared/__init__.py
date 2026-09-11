"""Cross-module contracts that do not contain recruitment business logic."""

from .errors import BusinessError, ExternalServiceError, ValidationIssue, retryable_decision
from .source_refs import SourceRef, SourceRefError, validate_source_ref

__all__ = [
    "BusinessError",
    "ExternalServiceError",
    "SourceRef",
    "retryable_decision",
    "SourceRefError",
    "ValidationIssue",
    "validate_source_ref",
]
