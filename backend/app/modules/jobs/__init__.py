"""岗位上传、确认、发布及岗位画像管理模块。

本模块拥有 JobDraft、Job、JobVersion 与岗位能力画像生命周期。跨模块调用只能通过
``jobs.public``：候选人分发读取可路由的冻结岗位版本，其他模块不得依赖 JobDraft。
"""
from .access_service import JobAccessService

__all__ = ["JobAccessService"]