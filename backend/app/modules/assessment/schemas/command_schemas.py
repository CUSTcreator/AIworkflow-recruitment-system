from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ScoringWorkflowRunRequest(BaseModel):
    """Input contract for assessment workflow submission."""

    model_config = ConfigDict(extra="forbid")

    generation_config: dict[str, Any] | None = None
    scoring_config: dict[str, Any] | None = None
    llm_config: dict[str, Any] | None = None
