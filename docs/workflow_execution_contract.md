# 工作流跨层执行契约

本项目的九个业务 Workflow 已经声明各自的业务步骤。本文只约定这些步骤在运行时如何可靠执行和如何把状态投影给用户；**不改变任何 Workflow 的业务顺序或业务判断**。

## 1. 信息链路

```text
外部活动事实 ExternalActivityResult
        ↓ 由 Step handler / StepRunner 归一化
步骤结果 StepOutcome
        ↓ StepRunner 持久化
WorkflowStepCheckpoint
        ↓ StepRunner 返回
RunPlanResult
        ↓ Worker 记录运行结论
WorkflowRun
        ↓ 任务读模型投影
WorkflowProgressDTO → API → 前端任务中心
```

外部层只报告成功、等待外部结果、可重试失败或永久失败；它不写数据库、不会自行循环重试，也不决定业务状态。

## 2. 每层唯一职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| `ExternalActivity` | 超时、幂等键传递、响应校验、外部错误分类和调用日志 | 数据库提交、业务状态、重试循环 |
| `StepRunner` | Step 租约、检查点、步骤级重试、退避、成功原子提交、`RunPlanResult` | 岗位/候选人业务规则 |
| Worker | 领取 `WorkflowRun`、心跳租约、崩溃接管、调用领域失败收尾 | 再做一次步骤级重试 |
| Workflow | 声明 `StepDefinition` 顺序和下游入队 | 手写循环重试、直接改检查点 |
| 领域 Service / 状态机 | Candidate、ResumeSubmission、Application、Interview 的业务状态 | 队列领取、外部调用重试 |
| 读模型与 API | 将技术状态投影为页面文案、重试提示、当前步骤 | 改变工作流或领域状态 |

## 3. 持久化边界

`WorkflowStepCheckpoint` 是一个步骤的执行账本，保存输入哈希、幂等键、外部请求标识、尝试次数、冻结的最大尝试次数、下次尝试时间、错误类别/错误码和 Artifact 引用。它不保存简历正文、LLM 大回包或正式领域数据。迁移前的历史行以 `max_attempts_snapshot=0` 表示“未冻结”，运行时会回退到已注册定义，不会被错误收紧为一次尝试。

正式业务产物（如 `ResumeProfile`、`ApplicationAssessmentVersion`）由对应 `persist_success` 在短事务中写入，并与该步骤的 `succeeded` 检查点一起提交。这样崩溃恢复时只会重新运行未成功的步骤。

## 4. 状态语义

- `pending` / `running`：等待或正在执行。
- `retry_wait`：StepRunner 已安排自动重试，Worker 不应再使用 `WorkflowRun.max_attempts` 重试整条流程。
- `waiting_external`：外部异步任务已提交，等待轮询结果。
- `blocked`：需要业务页面的人工作业，不属于技术失败。
- `failed`：当前步骤的自动尝试已结束；Worker 只执行一次领域状态收尾。
- `succeeded`：步骤产物和检查点已一起提交。

错误类别仅用于稳定投影：`external_transient`、`external_permanent`、`validation`、`business_rule`、`infrastructure`、`internal`。页面不得解析异常堆栈或供应商原始报错来判断动作。

## 5. 新增步骤的最低要求

新增业务 Step 时，只需声明 `StepDefinition`：输入快照函数、纯 handler、可选 `persist_success`、`StepPolicy`。handler 返回 `StepOutcome`，不得自行提交数据库或实现 while 重试。需要调用供应商时经 `ExternalActivity`，使用 `StepContext` 的幂等键和剩余超时预算。