"""经历能力评估的稳定公开入口。

具体指标与聚合细节位于 resume_experience 子包；其他模块只应依赖本入口，
避免把预设能力模型的内部结构扩散到岗位匹配或面评模块。
"""

from .resume_experience.pipeline import assess_resume_experience

__all__ = ["assess_resume_experience"]
