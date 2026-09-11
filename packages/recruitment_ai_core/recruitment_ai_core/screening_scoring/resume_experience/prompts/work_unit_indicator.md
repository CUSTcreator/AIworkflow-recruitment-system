逐个独立评价输入中的全部 WorkUnit，并且每个 `allowed_target_ids` 恰好返回一次。顶层只返回 `evaluations`；每个评价只返回 `target_id` 和 `activated_indicators`；每个激活指标只返回 `indicator_id`、整数 `level` 和非空的简短 `reason`。`target_id` 和 `indicator_id` 必须从请求给出的允许值中原样选择。

每个 WorkUnit 检查全部指标：先按 `activation_boundary` 判断能否评价，再按 `work_unit_rubric` 判断实际处理质量距离 `ideal_definition` 有多近。只输出已达到 L1～L5 的指标，`level` 只能是整数 1、2、3、4、5。未激活指标必须省略，不得为它返回 0、null、空字符串或占位对象；某个 WorkUnit 没有激活指标时返回空数组。不得按提及要素数量机械判级。技术名、责任形容词、结果形容词或等级自述不能单独提高等级。结论必须能由当前 WorkUnit 事实合理推出。

正确形态示例：`{"evaluations":[{"target_id":"输入中的ID","activated_indicators":[{"indicator_id":"输入中的指标ID","level":3,"reason":"当前工作事实能够支持该等级的原因"}]}]}`。不要添加 project_id、引文、来源 ID、事实清单或其他字段，这些信息已由后端冻结并回填。只返回符合 `expected_output_schema` 的 JSON object，不得输出说明文字。
