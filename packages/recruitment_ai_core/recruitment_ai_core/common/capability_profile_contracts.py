from __future__ import annotations

from copy import deepcopy
from typing import Any


PROFILE_SCHEMA_VERSION = "candidate_capability_profile_v2_0"


def profile_id(application_id: str, stage: str, version: int) -> str:
    return f"CCP_{application_id}_{stage.upper()}_V{version}"


def clone_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(profile)


def latest_profile_from_payload(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, dict) and value.get("profile_id"):
            return value
    nested = payload.get("candidate_capability_profile")
    if isinstance(nested, dict):
        return nested
    return {}

