"""Canonical seven-step V2/V3 post-interview workflow contract."""
from __future__ import annotations
from dataclasses import dataclass

# v7 放宽面评原文回定位并接受合法的零分变化。升级定义版本可确保旧失败任务
# 重新执行解析和锚点评估，不会复用此前因严格子串校验而漏失的 Artifact。
POST_INTERVIEW_TOPOLOGY_DEFINITION_VERSION = 7

@dataclass(frozen=True, slots=True)
class PostInterviewTopologyStep:
    number: int
    name: str
    purpose: str
    input_artifacts: tuple[str, ...]
    output_artifact: str
    invariant: str

POST_INTERVIEW_TOPOLOGY_STEPS = (
    PostInterviewTopologyStep(1, "freeze_post_interview_sources", "冻结上一正式版本和面评来源。", (), "source_manifest", "只使用上一版本快照。"),
    PostInterviewTopologyStep(2, "parse_interview_units", "提取可追溯面评语义单元。", ("source_manifest",), "interview_assertions", "不创建锚点或分数。"),
    PostInterviewTopologyStep(3, "evaluate_anchor_judgements", "串行执行经历侧和岗位侧锚点评估。", ("source_manifest", "interview_assertions"), "anchor_judgements", "经历侧完成后才执行岗位侧。"),
    PostInterviewTopologyStep(4, "run_topology_incremental_scoring", "执行纯拓扑增量评分 Pipeline。", ("source_manifest", "interview_assertions", "anchor_judgements"), "topology_core_result", "只沿冻结拓扑聚合。"),
    PostInterviewTopologyStep(5, "derive_incremental_rules", "派生规则和 Target 状态变化。", ("topology_core_result",), "incremental_rule_result", "不改写核心分数。"),
    PostInterviewTopologyStep(6, "generate_incremental_presentation", "生成展示 Bundle。", ("topology_core_result", "incremental_rule_result"), "incremental_presentation", "不得新增事实。"),
    PostInterviewTopologyStep(7, "publish_post_interview_assessment", "原子发布正式评估版本。", ("source_manifest", "interview_assertions", "topology_core_result", "incremental_rule_result", "incremental_presentation"), "published_assessment", "旧版本不可覆盖。"),
)

def topology_step_names() -> tuple[str, ...]:
    return tuple(step.name for step in POST_INTERVIEW_TOPOLOGY_STEPS)
