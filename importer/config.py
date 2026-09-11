from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_CATEGORIES = {
    "academic_transcript",
    "certificate",
    "portfolio",
    "psychological_assessment",
    "other",
}


@dataclass(frozen=True, slots=True)
class ImporterConfig:
    workspace_dir: Path
    backend_base_url: str
    resume_patterns: tuple[str, ...]
    category_patterns: dict[str, tuple[str, ...]]
    request_timeout_seconds: float = 60.0


def load_config(path: str | Path | None = None) -> ImporterConfig:
    config_path = Path(
        path or os.getenv("IMPORTER_CONFIG_PATH", "config/importer.yml")
    )
    if not config_path.is_file():
        raise RuntimeError(f"importer_config_not_found:{config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RuntimeError("importer_config_must_be_mapping")

    workspace_value = str(raw.get("workspace_dir") or "").strip()
    backend = _mapping(raw.get("backend"), "backend")
    backend_url = str(backend.get("base_url") or "").strip().rstrip("/")
    rules = _mapping(raw.get("file_rules"), "file_rules")
    resume_patterns = _patterns(rules.get("resume"), "file_rules.resume")
    categories = _mapping(rules.get("categories") or {}, "file_rules.categories")

    if not workspace_value:
        raise RuntimeError("importer_workspace_dir_required")
    if not backend_url.startswith(("http://", "https://")):
        raise RuntimeError("importer_backend_base_url_invalid")
    if not resume_patterns:
        raise RuntimeError("importer_resume_patterns_required")

    unknown = set(categories) - SUPPORTED_CATEGORIES
    if unknown:
        raise RuntimeError(
            "importer_unknown_categories:" + ",".join(sorted(unknown))
        )
    category_patterns = {
        category: _patterns(patterns, f"file_rules.categories.{category}")
        for category, patterns in categories.items()
        if category != "other"
    }
    timeout = float(raw.get("request_timeout_seconds") or 60)
    if timeout <= 0:
        raise RuntimeError("importer_request_timeout_must_be_positive")

    workspace = Path(workspace_value).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    return ImporterConfig(
        workspace_dir=workspace.resolve(),
        backend_base_url=backend_url,
        resume_patterns=resume_patterns,
        category_patterns=category_patterns,
        request_timeout_seconds=timeout,
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"importer_{name.replace('.', '_')}_must_be_mapping")
    return value


def _patterns(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise RuntimeError(f"importer_{name.replace('.', '_')}_must_be_string_list")
    return tuple(item.strip() for item in value if item.strip())

