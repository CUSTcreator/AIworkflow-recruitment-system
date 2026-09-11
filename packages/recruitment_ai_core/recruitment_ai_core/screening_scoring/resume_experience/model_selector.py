from __future__ import annotations

from typing import Any

from .preset_models import DEFAULT_MODEL_ID, DEFAULT_MODEL_VERSION, get_preset_model

GENERAL_KEYWORDS = (
    "采购", "供应商", "招标", "合同", "人力资源", "招聘", "薪酬", "绩效",
    "财务", "会计", "审计", "预算", "行政", "运营", "市场", "销售",
)
ENGINEERING_KEYWORDS = (
    "仪控", "仪表", "控制", "土木", "结构", "机械", "软件", "开发", "算法",
    "系统", "核安全", "安全分析", "设计", "研发", "测试", "仿真", "计算",
)


def recommend_preset_model(title: str, jd_text: str) -> dict[str, Any]:
    text = f"{title}\n{jd_text}".lower()
    general_hits = sum(keyword in text for keyword in GENERAL_KEYWORDS)
    engineering_hits = sum(keyword in text for keyword in ENGINEERING_KEYWORDS)
    # 2. 兼容旧岗位元数据中的模型标识；它只在岗位画像未携带时作为回退。
    model_id = (
        "general_professional_experience"
        if general_hits > engineering_hits
        else DEFAULT_MODEL_ID
    )
    return {
        "preset_model_id": model_id,
        "preset_model_version": DEFAULT_MODEL_VERSION,
        "selection_source": "job_primary_output_rule",
    }


def resolve_preset_model(
    *,
    job_profile: dict[str, Any] | None = None,
    job_metadata: dict[str, Any] | None = None,
    job_title: str = "",
    jd_text: str = "",
) -> dict[str, Any]:
    # 1. 优先读取已经随岗位版本冻结的预设经历模型，保证同一岗位评分口径稳定。
    profile = job_profile or {}
    metadata = job_metadata or {}
    # 2. 兼容旧岗位元数据中的模型标识；它只在岗位画像未携带时作为回退。
    model_id = (
        profile.get("preset_model_id")
        or metadata.get("preset_model_id")
    )
    version = (
        profile.get("preset_model_version")
        or metadata.get("preset_model_version")
    )
    # 3. 首次编译岗位时才根据职位名称和 JD 关键词推荐默认模型。
    if not model_id:
        recommended = recommend_preset_model(job_title, jd_text)
        model_id = recommended["preset_model_id"]
        version = recommended["preset_model_version"]
    # 4. 取得带版本号的指标目录，后续项目经历评估只能使用这份固定目录。
    return get_preset_model(str(model_id), str(version or DEFAULT_MODEL_VERSION))
