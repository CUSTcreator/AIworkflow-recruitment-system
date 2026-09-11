# 工作流步骤级恢复约定

本文档是招聘系统持久化工作流的阅读入口。可执行规则以
`backend/app/shared/workflows` 中的契约、`workflow_step_checkpoints` 表和
`StepRunner` 实现为准。

## 责任边界

- `WorkflowRun`：一次完整业务任务的队列、租约和最终状态。
- `WorkflowStepCheckpoint`：该任务中单个可恢复步骤的当前状态。
- 领域表：保存正式业务产物，例如 `ResumeProfileRecord`、`InterviewParseResultRecord`、`ApplicationAssessmentVersion`。
- `WorkflowArtifact`：仅在需要保存外部原始回包或大对象引用时使用。

检查点只保存产物 ID、对象存储 URI 和版本号，禁止保存完整简历、原始模型回包、密钥或令牌。

## 状态

`WorkflowRun` 使用 `pending / running / completed / failed / cancelled`。`pending` 配合
`available_at` 同时表达新任务和等待恢复的任务。

`WorkflowStepCheckpoint` 使用：

```text
pending → running → succeeded
                 ├→ retry_wait → running
                 ├→ waiting_external → running
                 ├→ failed
                 └→ cancelled
```

“人工确认简历结构”是业务对象的成功产物，因此 WorkflowRun 应为 `completed`，不把它错误标记为技术阻塞。

## StepRunner 的执行规则

1. 用冻结输入计算 `input_hash`；同一 WorkflowRun 内输入变化必须新建 WorkflowRun，不能覆写旧检查点。
2. 在短事务内创建或领取检查点，并固定 `idempotency_key`。
3. 在数据库事务外调用 LLM、PDF 解析或对象存储。
4. 在短事务内同时写正式领域产物和 `succeeded` 检查点。
5. 临时错误写 `retry_wait`，并将 WorkflowRun 按 `available_at` 放回队列。
6. 外部异步服务写 `waiting_external`，恢复时只查询原 `external_job_id`，不得重复提交。
7. 参数、Schema、数据缺失等永久错误写 `failed` 并触发 Workflow 的失败收尾。

## 幂等与重试

幂等键由 `workflow_run_id + step_name + input_hash` 稳定派生。所有供应商适配器必须把该键传给外部服务。

默认外部 Step：单次 60 秒、最多 3 次、退避 5/20/60 秒、总截止 10 分钟。PDF 解析等较慢服务通过 `StepPolicy` 覆盖；本地轻量规则不进行步骤级重试。

现有 `WorkflowRun.attempt_count` 仅保留为 Worker 领取诊断；步骤是否耗尽重试次数以 `WorkflowStepCheckpoint.attempt_count` 为准。

## 七条 Workflow 的目标步骤

| Workflow | 步骤 |
|---|---|
| 岗位导入 | 解析文件 → 岗位结构化 → 发布岗位画像 |
| 简历导入 | 提取文本 → 简历结构化 → 发布简历画像 → 岗位分发 → 原子入队 V1 |
| 硬筛 | 冻结输入 → 硬筛并发布 |
| 初筛 V1 | 冻结来源 → 核心评分 → 规则派生 → LLM 展示加工 → 发布 V1 |
| 一面题单 | 冻结 V1/模板 → 题单规划 → 规则派生 → LLM 展示加工 → 发布题单 |
| 一面后 V2 | 冻结来源 → 面评解析 → 增量评分 → 规则派生 → LLM 展示加工 → 发布 V2 |
| 二面后 V3 | 冻结来源 → 面评解析 → 增量评分 → 规则派生 → LLM 展示加工 → 发布 V3 |

迁移期间，尚未声明步骤计划的历史 Workflow 继续使用旧 handler；不得将运行中的旧任务直接切换到新步骤定义。