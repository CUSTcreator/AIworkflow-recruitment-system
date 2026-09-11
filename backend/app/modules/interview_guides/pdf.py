from __future__ import annotations

import fitz


PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT = 48
RIGHT = 48
TOP = 46
BOTTOM = 42


def build_interview_guide_pdf(*, guide: dict, candidate_name: str, job_title: str) -> bytes:
    document = fitz.open()
    page = None
    y = TOP

    def new_page():
        nonlocal page, y
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        y = TOP
        return page

    def ensure(lines: int = 1, line_height: int = 18):
        nonlocal page
        if page is None or y + lines * line_height > PAGE_HEIGHT - BOTTOM:
            new_page()

    def write(text: str, *, size: float = 11, bold: bool = False, gap: int = 18):
        nonlocal y
        wrapped = _wrap(text, 46 if size <= 11 else 34)
        ensure(len(wrapped), gap)
        font = "china-s"
        for line in wrapped:
            page.insert_text((LEFT, y), line, fontname=font, fontsize=size, color=(0.08, 0.12, 0.2))
            y += gap
        if bold:
            y += 1

    new_page()
    write("候选人一面执行题单", size=18, gap=26)
    if guide.get("isDraft"):
        write("草稿 · 仅供预览", size=10, gap=16)
    write(f"候选人：{candidate_name}    岗位：{job_title}", size=10, gap=16)
    version = guide.get("commonTemplateVersion")
    version_text = f"通用题单版本：V{version}" if version else "通用题单版本：未记录"
    write(f"面试官：________________    日期：________________    {version_text}", size=10, gap=16)
    y += 8
    sections = guide.get("sections") or _sections_from_questions(guide.get("questions") or [])
    for section in sections:
        title = "技术题单" if section.get("sectionType") == "technical" else "通用题单"
        ensure(3)
        y += 8
        write(title, size=15, gap=22)
        for index, question in enumerate(section.get("questions") or [], start=1):
            ensure(8)
            text = str(question.get("finalText") or question.get("mainQuestion") or question.get("question") or "").strip()
            write(f"{index}. {text}", size=11, gap=18)
            points = question.get("evaluationPoints") or question.get("expectedEvidence") or []
            if points:
                write("考察要点：" + "；".join(str(item) for item in points), size=9, gap=15)
            write("结论：□ 明显不足  □ 基本符合  □ 良好  □ 突出", size=9, gap=15)
            write("记录：____________________________________________________________", size=9, gap=16)
            write("      ____________________________________________________________", size=9, gap=16)
            y += 5
    for page_number, current_page in enumerate(document, start=1):
        current_page.insert_text(
            (PAGE_WIDTH / 2 - 22, PAGE_HEIGHT - 22),
            f"第 {page_number} 页",
            fontname="china-s",
            fontsize=8,
            color=(0.45, 0.48, 0.55),
        )
        current_page.insert_text(
            (LEFT, PAGE_HEIGHT - 22),
            "内部招聘资料，请妥善保管",
            fontname="china-s",
            fontsize=8,
            color=(0.45, 0.48, 0.55),
        )
    return document.tobytes(garbage=4, deflate=True)


def _wrap(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    current = ""
    width = 0.0
    for character in text:
        weight = 0.55 if ord(character) < 128 else 1.0
        if current and width + weight > limit:
            lines.append(current)
            current = character
            width = weight
        else:
            current += character
            width += weight
    if current:
        lines.append(current)
    return lines or [""]


def _sections_from_questions(questions: list[dict]) -> list[dict]:
    technical = [item for item in questions if item.get("sectionType") != "common"]
    common = [item for item in questions if item.get("sectionType") == "common"]
    return [
        {"sectionType": "technical", "questions": technical},
        {"sectionType": "common", "questions": common},
    ]
