from __future__ import annotations

from .contracts import FactKind
from .text_normalizer import contains_any


OUTCOME_WORDS = ["指标", "提升", "上线", "部署", "压测", "评测", "测试", "验证", "用户反馈", "召回", "准确率", "耗时", "结果"]
ACTION_WORDS = ["实现", "开发", "负责", "设计", "封装", "接入", "优化", "构建", "完成", "落库", "联调", "编排", "调试"]
PRACTICE_WORDS = ["项目", "实习", "科研", "课程", "经历", "使用", "参与", "做过", "实践", "原型", "demo"]
SKILL_WORDS = ["熟悉", "掌握", "了解", "技能", "具备", "会使用", "学习"]
EDUCATION_WORDS = ["本科", "硕士", "研究生", "博士", "学历", "专业", "课程"]
CERT_WORDS = ["证书", "认证", "资格"]


def resolve_fact_kind(text: str, claimed_fact_kind: FactKind | None = None) -> FactKind:
    lowered = text.lower()
    if not text.strip():
        return "no_evidence"
    if contains_any(lowered, OUTCOME_WORDS):
        return "outcome_bullet"
    if contains_any(lowered, ACTION_WORDS):
        return "action_bullet"
    if contains_any(lowered, PRACTICE_WORDS):
        return "practice_context"
    if contains_any(lowered, SKILL_WORDS):
        return "skill_claim"
    if contains_any(lowered, EDUCATION_WORDS):
        return "education_entry"
    if contains_any(lowered, CERT_WORDS):
        return "credential_entry"
    if len(text) <= 24:
        return "experience_title"
    return claimed_fact_kind or "other_text"
