"""User-facing recovery action contract shared by business read models.

Workflow/Step/Activity records explain what happened.  A business page must not
translate those technical states into buttons on its own; the owning backend
module combines business state, persisted artifacts and the current user's
authorization, then returns this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field


class RecoveryActionView(BaseModel):
    """One action the current user can execute from the current business state."""

    model_config = ConfigDict(populate_by_name=True)

    action: str
    label: str
    requires_input: bool = Field(default=False, alias="requiresInput")
    warning: str = ""
    retry_scope: str | None = Field(default=None, alias="retryScope")


@dataclass(frozen=True, slots=True)
class RecoveryActionSpec:
    """Backend-owned presentation and retry boundary for a recovery command."""

    action: str
    label: str
    requires_input: bool = False
    warning: str = ""
    retry_scope: str | None = None

    def to_public_dict(self) -> dict[str, object]:
        return RecoveryActionView(
            action=self.action,
            label=self.label,
            requiresInput=self.requires_input,
            warning=self.warning,
            retryScope=self.retry_scope,
        ).model_dump(by_alias=True)


def public_recovery_actions(
    action_codes: Iterable[str],
    catalog: Mapping[str, RecoveryActionSpec],
) -> list[dict[str, object]]:
    """Resolve ordered action codes to the public DTO, removing duplicates.

    Unknown codes are rejected instead of being rendered with a guessed label.
    This keeps backend command support and frontend interaction text aligned.
    """

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for code in action_codes:
        if code in seen:
            continue
        spec = catalog.get(code)
        if spec is None:
            raise RuntimeError(f"recovery_action_spec_missing:{code}")
        seen.add(code)
        result.append(spec.to_public_dict())
    return result
