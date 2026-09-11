from __future__ import annotations

import hashlib
import re


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def split_atoms(text: str) -> list[tuple[int, str]]:
    normalized = normalize_text(text)
    if not normalized:
        return []
    atoms: list[tuple[int, str]] = []
    for line_no, line in enumerate(normalized.splitlines(), start=1):
        parts = re.split(r"[。；;]\s*|(?<=，)\s*", line)
        for part in parts:
            cleaned = part.strip(" \t-•、，。；;")
            if cleaned:
                atoms.append((line_no, cleaned))
    return atoms


def contains_any(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)
