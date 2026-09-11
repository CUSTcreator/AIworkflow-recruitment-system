"""同步写命令的通用幂等账本。

本类不提交事务；CommandRunner 或调用方必须将它和正式业务修改、审计事件一起提交。
这样网络重试只会复用已完成响应，不会重复推进状态机或重复入队。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models.entities import IdempotencyKey, User
from backend.app.shared.errors import BusinessError


class IdempotencyGuard:
    """封装 IdempotencyKey 的请求指纹、重放检查和成功响应记录。"""

    # ``idempotency_keys.idempotency_key`` 是 varchar(128)。该限制属于命令运行时
    # 合同，而非某个 Router 的偶然约定：所有 HTTP 写命令都必须在进入数据库前满足它。
    MAX_KEY_LENGTH = 128

    @classmethod
    def compatibility_key(
        cls, provided: str | None, *, user_id: str, action: str, resource_id: str, body: dict,
    ) -> str:
        """统一生成 HTTP 写命令的幂等键。

        新客户端应传入自己的键；旧客户端才使用压缩后的兼容键。Router 禁止自行
        拼接 ``legacy:...``，这样数据库上限、哈希算法和重放语义只有一个来源。
        """
        if provided:
            return cls.storage_key(provided)
        identity = {
            "user_id": user_id,
            "action": action,
            "resource_id": resource_id,
            "body": body,
        }
        encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return f"legacy:v1:{hashlib.sha256(encoded).hexdigest()}"
    def __init__(self, db: Session) -> None:
        self.db = db

    @classmethod
    def storage_key(cls, key: str) -> str:
        """把客户端或兼容层提供的幂等键收敛为可安全持久化的稳定键。

        正常长度的键保持原样，避免改变现有客户端的重放语义；超长键改为带
        ``sha256`` 前缀的摘要。同一个原始键始终得到同一个存储键，因此重试仍会
        命中同一条幂等账本，而不会因为数据库字段上限让整条业务事务回滚。
        """
        if not key:
            raise BusinessError("idempotency_key_missing", "缺少 Idempotency-Key 请求头")
        if len(key) <= cls.MAX_KEY_LENGTH:
            return key
        return f"sha256:{hashlib.sha256(key.encode('utf-8')).hexdigest()}"
    @staticmethod
    def request_hash(
        *,
        action: str,
        resource_id: str,
        body: dict[str, Any],
        resource_key: str = "resource_id",
        extra: dict[str, Any] | None = None,
    ) -> str:
        """为同一用户的一次逻辑命令生成稳定指纹。

        ``resource_key`` 让 Application 迁移保留旧 ``application_id`` 指纹，避免
        已经发出的 Idempotency-Key 在发布新运行时后被误判为冲突。
        """
        identity = {"action": action, resource_key: resource_id, "body": body}
        # 少数兼容接口把工作流类型等信息视为幂等语义的一部分。它们仍由同一
        # Guard 生成指纹，只是显式声明额外维度，不能各自重新实现哈希逻辑。
        identity.update(extra or {})
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def replay_or_none(
        self,
        *,
        user: User,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any] | None:
        """返回已完成响应；同 key 不同请求体则抛 409。"""
        idempotency_key = self.storage_key(idempotency_key)
        existing = self.db.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user.user_id,
                IdempotencyKey.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise BusinessError(
                "idempotency_key_conflict",
                "同一个 Idempotency-Key 不能匹配不同请求体重复使用",
                status_code=409,
            )
        return dict(existing.response_json or {})

    def remember_success(
        self,
        *,
        user: User,
        idempotency_key: str,
        request_hash: str,
        response: dict[str, Any],
    ) -> None:
        """把成功响应加入当前事务；调用方必须在同一事务内提交。"""
        idempotency_key = self.storage_key(idempotency_key)
        # 命令 handler 可能返回 Pydantic DTO；账本只能保存 JSON object。
        raw_response = (
            response.model_dump(mode="json")
            if hasattr(response, "model_dump")
            else response
        )
        serializable_response = json.loads(
            json.dumps(raw_response, ensure_ascii=False, default=str)
        )
        if not isinstance(serializable_response, dict):
            raise TypeError("idempotency_response_must_be_json_object")
        self.db.add(
            IdempotencyKey(
                user_id=user.user_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="completed",
                response_json=serializable_response,
            )
        )