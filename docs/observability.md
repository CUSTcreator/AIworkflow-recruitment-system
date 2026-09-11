# 可观测性与任务时间线

## 三类数据及边界

| 数据流 | 存储位置 | 面向对象 | 禁止保存/展示的内容 |
| --- | --- | --- | --- |
| 技术运行日志 | Backend、Worker 的标准输出 JSON | 运维平台 | 简历全文、提示词、令牌、SQL 参数、模型回包正文 |
| 业务审计 | `audit_events` | 管理员审计 | 技术堆栈、供应商原始异常 |
| 执行时间线 | `workflow_execution_events` | 任务中心、招聘页面 | 原始错误信息和业务敏感文本 |

`request_id` 关联一次 HTTP 请求；`workflow_run_id` 关联异步流程；步骤和外部调用还通过 `external_request_id` 关联。前端只读取执行时间线中稳定的状态、错误类别和错误码。

## 运行与检索

1. 在 Backend、Worker 容器采集 stdout 中的 JSON 行；按照 `service`、`environment`、`request_id`、`workflow_run_id`、`step_name` 和 `event` 建立索引。
2. 设置 `DATABASE_SLOW_QUERY_MS`（默认 500）记录慢查询。事件只保存语句指纹，不保存 SQL 参数。
3. 业务页面通过 `GET /api/v1/tasks/workflows/{workflowRunId}/timeline` 获取不超过 200 条公开执行事件。
4. 数据库升级后，新的时间线从首次 Step 执行开始记录；历史 Workflow 不回填伪造事件。

## 告警建议

- `workflow_step_failed` 或 `database_query_failed`：立即告警。
- 5 分钟内同一 `workflow_type + step_name` 的 `workflow_step_deferred` 超过阈值：告警。
- `workflow_step_blocked`：创建 HR 待办，而不是自动重试。
- 过期租约、队列积压、外部调用延迟：按 5 分钟窗口观察趋势。

容器平台可使用 Loki/Grafana、ELK 或云日志产品采集 JSON stdout；本项目不把日志平台写死到业务代码中。