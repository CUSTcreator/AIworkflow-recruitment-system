"""一面题单规则的后端兼容入口。

纯规则已迁移至 ``recruitment_ai_core.first_interview_planning.rule_derivation``。
该文件只保留旧导入路径，Workflow 不应在这里新增任何业务判断、数据库访问或 LLM 调用。
"""
from recruitment_ai_core.first_interview_planning.rule_derivation import (
    FirstInterviewRuleDerivationService,
)

__all__ = ["FirstInterviewRuleDerivationService"]