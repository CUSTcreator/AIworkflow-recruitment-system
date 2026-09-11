顶层只返回当前 `project_id`、`project_pao` 和 `activated_indicators`。`project_id` 必须与输入完全相同。`project_pao` 必须包含 `problem`、`approach`、`outcome`；某部分事实不足时返回 null，不得返回不完整对象。非空 P、A、O 只返回 `summary`、`work_unit_ids` 和 `evidence_work_unit_ids`。两个 ID 数组只能从输入 WorkUnit ID 中原样选择，`evidence_work_unit_ids` 必须是 `work_unit_ids` 的子集。

只有项目整体接近某等级理想状态时才能取该等级；局部强项不能自动放大为项目强项。每个激活指标只返回输入中存在的 `indicator_id`、整数 `level` 和非空的简短 `reason`。只输出达到 L1～L5 的指标；未激活指标必须省略，不得返回 0、null、空字符串或占位对象。不要返回指标证据、事实清单或其他字段，不得补写事实。

正确形态示例：`{"project_id":"输入中的项目ID","project_pao":{"problem":null,"approach":{"summary":"方法概括","work_unit_ids":["输入中的ID"],"evidence_work_unit_ids":["输入中的ID"]},"outcome":null},"activated_indicators":[{"indicator_id":"输入中的指标ID","level":3,"reason":"项目整体支持该等级的原因"}]}`。只返回符合 `expected_output_schema` 的 JSON object，不得输出说明文字。
