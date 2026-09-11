"""真实简历的本地结构回归诊断，不向外部服务发送任何内容。

直接复用生产的本地 PDF 文本块提取、确定性大纲构建和结构校验规则。
本脚本只输出计数与错误码，不输出简历正文，也不写入数据库。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.modules.document_ingestion.parsing.recovery_service import (
    LocalPdfRecoveryService,
)
from recruitment_ai_core.resume_structuring.pipeline import (
    _attach_block_ids,
    _layout_project_title_hints,
)
from recruitment_ai_core.resume_structuring.validator import validate_resume_structure
from recruitment_ai_core.screening_scoring.resume_structurer import build_resume_ir


def diagnose(path: Path) -> dict[str, object]:
    """以生产本地解析与确定性结构规则评估一份简历。"""
    text, pages, blocks = LocalPdfRecoveryService().extract(path.read_bytes())
    hints = _layout_project_title_hints(blocks)
    resume_ir = build_resume_ir(
        candidate_id=f"LOCAL_REGRESSION_{path.stem[:20]}",
        resume_text=text,
        project_title_line_numbers=hints,
    )
    _attach_block_ids(resume_ir, blocks)
    validation = validate_resume_structure(resume_ir, blocks)
    return {
        "file": path.name,
        "parse": {
            "status": "passed",
            "provider": "local_pymupdf",
            "pageCount": pages,
            "blockCount": len(blocks),
            "textCharacters": len(text),
        },
        "structure": {
            "status": "passed" if validation.accepted else "review_required",
            "validationCodes": [item["code"] for item in validation.errors],
            "experienceUnitCount": len(resume_ir.experience_units),
            "sourceBulletCount": len(resume_ir.source_bullets),
            "perExperienceBulletCounts": [
                len(item.source_bullet_ids) for item in resume_ir.experience_units
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="本地简历结构回归诊断")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.paths:
        try:
            print(json.dumps(diagnose(path), ensure_ascii=False), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "file": path.name,
                        "status": "failed",
                        "error": f"{type(exc).__name__}:{str(exc)[:300]}",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
