from __future__ import annotations

import hashlib
import re
import unicodedata


def normalize_jd_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    lines = [
        re.sub(r"[ \t\u3000]+", " ", line).strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def jd_content_sha256(value: str) -> str:
    return hashlib.sha256(normalize_jd_text(value).encode("utf-8")).hexdigest()


def normalize_job_title(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or "")).casefold()


def job_identity_key(
    *,
    department_id: str,
    title: str,
    jd_sha256: str | None = None,
    external_job_id: str | None = None,
) -> str:
    if external_job_id:
        source = f"external:{external_job_id.strip().casefold()}"
    else:
        # JD正文属于岗位版本，不能参与岗位本身的唯一身份计算。
        source = f"logical:{department_id}:{normalize_job_title(title)}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def job_position_identity_key(*, department_id: str, title: str) -> str:
    source = f"position:{department_id}:{normalize_job_title(title)}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def job_requisition_identity_key(
    *,
    position_id: str,
    requisition_id: str,
    external_job_id: str | None = None,
) -> str:
    source = (
        f"external:{external_job_id.strip().casefold()}"
        if external_job_id
        else f"requisition:{position_id}:{requisition_id}"
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()

