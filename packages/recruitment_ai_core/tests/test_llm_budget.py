from recruitment_ai_core.llm_budget import LlmBudgetPolicy, pack_llm_batches


def test_keeps_minimum_units_intact_and_is_deterministic():
    items = [{"id": str(index), "text": "x" * 10} for index in range(5)]
    policy = LlmBudgetPolicy(
        context_window_tokens=100,
        max_input_ratio=0.6,
        output_reserve_ratio=0.3,
        safety_margin_tokens=5,
    )
    first = pack_llm_batches(
        items,
        prompt_tokens=5,
        schema_tokens=5,
        policy=policy,
        estimate_item_input=lambda _: 10,
        estimate_item_output=lambda _: 5,
    )
    second = pack_llm_batches(
        items,
        prompt_tokens=5,
        schema_tokens=5,
        policy=policy,
        estimate_item_input=lambda _: 10,
        estimate_item_output=lambda _: 5,
    )
    assert [[item["id"] for item in batch.items] for batch in first] == [[item["id"] for item in batch.items] for batch in second]
    assert sum(len(batch.items) for batch in first) == len(items)


def test_rejects_single_item_that_cannot_fit():
    policy = LlmBudgetPolicy(context_window_tokens=100, safety_margin_tokens=10)
    try:
        pack_llm_batches(
            [{"id": "too-large"}],
            prompt_tokens=20,
            schema_tokens=20,
            policy=policy,
            estimate_item_input=lambda _: 100,
            estimate_item_output=lambda _: 20,
        )
    except ValueError as error:
        assert str(error) == "llm_budget_single_item_exceeds_budget"
    else:
        raise AssertionError("expected single-item budget failure")

