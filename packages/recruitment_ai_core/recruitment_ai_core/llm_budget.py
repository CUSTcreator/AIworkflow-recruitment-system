"""LLM 批次预算工具。

批次边界必须在请求发出前确定：每个业务最小单元（如 JDUnit、项目）不可拆分，
但多个最小单元可以在同一个请求中合并。该模块不调用模型、不认识数据库，只负责
保守估算 token 并生成稳定批次，便于上层在重试时复用完全相同的批次计划。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable, Iterable


@dataclass(frozen=True, slots=True)
class LlmBudgetPolicy:
    """一次模型请求的安全预算。"""

    context_window_tokens: int = 32768
    max_input_ratio: float = 0.60
    output_reserve_ratio: float = 0.30
    safety_margin_tokens: int = 1024
    max_items: int | None = None

    def __post_init__(self) -> None:
        if self.context_window_tokens < 1:
            raise ValueError("llm_budget_context_window_invalid")
        if not 0 < self.max_input_ratio < 1 or not 0 < self.output_reserve_ratio < 1:
            raise ValueError("llm_budget_ratio_invalid")
        if self.safety_margin_tokens < 0:
            raise ValueError("llm_budget_safety_margin_invalid")
        if self.max_items is not None and self.max_items < 1:
            raise ValueError("llm_budget_max_items_invalid")


@dataclass(frozen=True, slots=True)
class LlmBatch:
    """已经确定的不可变批次；items 内每个元素都是不可拆的业务单元。"""

    batch_index: int
    items: tuple[Any, ...]
    estimated_input_tokens: int
    estimated_output_tokens: int

    @property
    def estimated_total_tokens(self) -> int:
        return self.estimated_input_tokens + self.estimated_output_tokens


def estimate_tokens(value: Any, *, chars_per_token: float = 1.8) -> int:
    """在没有供应商 tokenizer 时使用保守近似。

    JSON 字符串化后再估算，避免只计算正文而漏掉字段名、ID 和 Schema。生产环境可
    将该函数替换为当前模型的 tokenizer；调用合同不变。
    """
    if chars_per_token <= 0:
        raise ValueError("llm_budget_chars_per_token_invalid")
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return max(1, int((len(text) + chars_per_token - 1) / chars_per_token))


def pack_llm_batches(
    items: Iterable[Any],
    *,
    prompt_tokens: int,
    schema_tokens: int,
    policy: LlmBudgetPolicy,
    estimate_item_input: Callable[[Any], int] | None = None,
    estimate_item_output: Callable[[Any], int] | None = None,
) -> list[LlmBatch]:
    """按预算贪心装箱，并提前拒绝无法容纳的单个最小单元。

    批次上限取上下文窗口与输入/输出保留比例的较小安全值。相同输入、策略和顺序
    总是得到相同批次边界；重试时应复用此结果，不得重新随机分组。
    """
    if prompt_tokens < 0 or schema_tokens < 0:
        raise ValueError("llm_budget_static_tokens_invalid")
    values = list(items)
    input_fn = estimate_item_input or estimate_tokens
    output_fn = estimate_item_output or (lambda _item: 256)
    input_limit = int(policy.context_window_tokens * policy.max_input_ratio)
    output_limit = int(policy.context_window_tokens * policy.output_reserve_ratio)
    total_limit = policy.context_window_tokens - policy.safety_margin_tokens
    batches: list[LlmBatch] = []
    current: list[Any] = []
    current_input = prompt_tokens + schema_tokens
    current_output = 0

    def fits(item_input: int, item_output: int, count: int) -> bool:
        return (
            (policy.max_items is None or count <= policy.max_items)
            and current_input + item_input <= input_limit
            and current_output + item_output <= output_limit
            and current_input + item_input + current_output + item_output <= total_limit
        )

    for item in values:
        item_input = max(1, int(input_fn(item)))
        item_output = max(1, int(output_fn(item)))
        if not fits(item_input, item_output, len(current) + 1):
            if not current:
                raise ValueError("llm_budget_single_item_exceeds_budget")
            batches.append(LlmBatch(len(batches), tuple(current), current_input, current_output))
            current = []
            current_input = prompt_tokens + schema_tokens
            current_output = 0
            if not fits(item_input, item_output, 1):
                raise ValueError("llm_budget_single_item_exceeds_budget")
        current.append(item)
        current_input += item_input
        current_output += item_output
    if current:
        batches.append(LlmBatch(len(batches), tuple(current), current_input, current_output))
    return batches

