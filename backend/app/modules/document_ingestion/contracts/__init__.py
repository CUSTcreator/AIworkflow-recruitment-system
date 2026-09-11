"""文档导入阶段的跨边界数据合同。

本包只放数据库/Workflow 之间需要持久化或传递的 DTO；纯文本解析、结构化算法的
内部临时变量仍属于 recruitment_ai_core，不能反向依赖 ORM 实体。
"""

from .resume_intake_contracts import (
    DocumentParseResult,
    ResumeMetadata,
    ResumeProfileSchema,
    ResumePublishPrepared,
    ResumeSourceSnapshot,
    ResumeStructureResult,
)

__all__ = [
    "DocumentParseResult",
    "ResumeMetadata",
    "ResumeProfileSchema",
    "ResumePublishPrepared",
    "ResumeSourceSnapshot",
    "ResumeStructureResult",
]
