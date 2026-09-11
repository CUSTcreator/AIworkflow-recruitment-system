"""V3 面评证据服务骨架。

职责只限于把已冻结的 ``InterviewParseInput`` 交给算法包，并返回可检查点化的
``InterviewEvidenceExtractionResult``。它不读取 ORM、不改 Application 状态，
也不发布 IPR/AAV；这些仍属于 Workflow 的冻结与发布步骤。

当前活跃 V2 Workflow 未接入本服务。待单次 LLM 提取、规范化和增量评分适配完成后，
V3 Workflow 才会注册并由本服务替换旧的“分类→绑定”两个活动。
"""
from __future__ import annotations

from recruitment_ai_core.interview_evaluation import (
    InterviewEvidenceExtractionResult,
    InterviewParseInput,
    extract_and_bind_interview_evidence,
)


class InterviewEvidenceService:
    """面评证据新合同的后端入口，保持与数据库和传输层解耦。"""

    def extract_and_bind(
        self, parse_input: InterviewParseInput
    ) -> InterviewEvidenceExtractionResult:
        """执行 V3 提取入口；返回值由调用方保存为 Workflow Artifact。"""
        return extract_and_bind_interview_evidence(parse_input)