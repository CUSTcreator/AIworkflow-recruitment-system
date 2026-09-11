# 后端事务边界全量审计

- 审计日期：2026-08-24
- 范围：`backend/app`（不含测试、迁移和 Seed 的业务结论）
- 原始扫描快照：[transaction-boundary-scan-2026-08-24.md](transaction-boundary-scan-2026-08-24.md)
- 可重复扫描脚本：[audit_transaction_boundaries.ps1](../../backend/scripts/audit_transaction_boundaries.ps1)

## 一、统一约定

1. 业务 Service、领域 Service 和读模型不得自行调用 `commit()`；只能写入当前 Session 并在必要时 `flush()`。
2. HTTP 写命令只能由 `CommandRunner` 提交；Worker 的步骤发布只能由 `StepRunner` 或 Worker 的明确事务边界提交。
3. `Application.status`、`Job.status` 必须经各自 ProcessService / 状态机变更；`Candidate`、`ResumeSubmission` 同理。
4. Workflow 入队必须与触发它的状态、阶段历史和审计事件处于同一数据库事务。
5. 对象存储删除、通知等不可回滚的外部副作用只能在 `after_commit` 执行；读取、可幂等的阶段产物写入须按 StepRunner 约束处理。

## 二、扫描操作与留痕方式

每次审计运行以下五类扫描，并将原始命中输出为带日期的 Markdown 快照：

| 编号 | 扫描对象 | 目的 |
|---|---|---|
| 1 | `commit / rollback / flush` | 确认唯一事务提交边界 |
| 2 | 四个领域对象的 `status =` | 防止绕开状态机 |
| 3 | `WorkflowQueue.enqueue` | 确认入队和领域发布原子化 |
| 4 | 对象存储、ExternalActivity、`after_commit` | 确认外部副作用时机 |
| 5 | `commit=False / commit=True` | 找出残留的旧式兼容接口 |

运行方式：

```powershell
pwsh -File backend/scripts/audit_transaction_boundaries.ps1 `
  -OutputPath docs/architecture/transaction-boundary-scan-YYYY-MM-DD.md
```

不把每次临时终端输出直接写进正文；原始快照记录“查到了什么”，本报告记录“为什么保留、为什么要改”。

## 三、本次结论

### A. 合法的提交边界：保留

| 位置 | 结论 |
|---|---|
| `infrastructure/command_runtime/command_runner.py` | HTTP 命令唯一提交、回滚与提交后回调边界。 |
| `infrastructure/workflow_runtime/step_runner.py` | Workflow 最小恢复步骤的提交、检查点和失败回滚边界。 |
| `workers/workflow_worker.py` | 任务领取、租约和 Worker 级恢复事务边界。 |
| `seeds/*` | 初始化/运维脚本，不属于在线业务事务。 |
| `modules/auth/service.py` | 登录成功仅更新时间戳的独立认证边界；保留为显式例外。 |

### B. 已修复的真实事务断点

| 原位置 | 修复后 |
|---|---|
| 硬筛通过后调用公开评分命令 | 改为同 Session 的 `ScoringRequestCommands.enqueue_in_transaction()`。 |
| Candidate 分发、简历重建调用公开评分命令 | 改为内部入队，不再中途提交。 |
| 批准一面后再单独创建题单任务 | 合并到同一个 `ApplicationCommandExecutor` 事务。 |

### C. 旧式提交分支：本轮已收口

普通业务 Service 的直接 `commit()` 已移除。少数 `commit` 参数暂时保留，只用于兼容已有内部调用，实际一律 `flush()`；后续可在不破坏调用方的前提下删除这些参数。

| 模块 | 位置 | 后续动作 |
|---|---|---|
| Application 删除 | `applications/commands/application_lifecycle_commands.py` | 已改为只 `flush`；彻底删除的对象存储清理全部交给外层 `after_commit`。 |
| Application 附件 | `applications/documents/document_service.py` | 已改为只 `flush`，保留上传失败的外层回滚补偿。 |
| 硬筛策略/目录 | `assessment/hard_screening/*service.py` | 已改为只 `flush`；默认目录移至应用启动初始化，GET 不再写库。 |
| Job 删除/编辑 | `jobs/lifecycle_service.py` | 已改为只 `flush`。 |
| 导入记录已读 | `jobs/management_read_models.py` | 已移除内部 `commit`；后续可将该写操作更名迁至命令 Service。 |
| 一面题单模板 | `interview_guides/service.py` | 移除 `commit` 参数，只由路由命令边界提交。 |
| 系统管理账户、组织、运维 | `system_admin/*_service.py` | 已移除内部 `commit`，保持 `_system_command` 为唯一入口。 |

### D. 状态机绕过点：本轮已修复

| 位置 | 问题 | 正确改法 |
|---|---|---|
| `assessment/commands/scoring_request_commands.py` 的旧版简历升级分支 | 直接把 `ResumeSubmission.status` 写为 `queued`。 | 已改为 Candidate Intake 的正式“新建重解析 Submission 并入队”公开契约；不再复活旧任务。 |
| `system_admin/operations_service.py` 的管理员重试 | 直接把 ResumeSubmission 写为 `queued`。 | 已按任务类型调用 Candidate Intake 重试命令，由简历状态机验证。 |
| `candidates/intake_process_service.py` 的 `activate_candidate()` | 对非 archived 状态直接归一为 `active`。 | 已改为调用 Candidate 生命周期状态机。 |

### E. 入队与外部副作用：本次复核结论

- 评分、硬筛、一二面、岗位画像、文件导入、候选人分发/重建的入队点已列入原始快照；后续每个入队点必须有对应的原子事务测试。
- `ApplicationLifecycleCommands.hard_delete()` 仍存在“自行提交后删除对象”的旧分支；正常管理端路径已用 `after_commit`，收口时应删除旧分支，不能直接删对象清理。
- Workflow 中的对象存储读写是阶段工件操作，继续遵循 StepRunner 的“事务外写工件、短事务发布引用”规则。

## 四、验收与自动防回归

完成 C、D 项改造后，CI 应执行：

1. 运行扫描脚本并与允许清单比对；业务模块新增 `commit()` 直接失败。
2. 检查 `Application / Job / Candidate / ResumeSubmission` 的直接状态写入只存在于状态机和指定 ProcessService。
3. 对每个 Workflow 入队点新增“入队失败则状态、审计、阶段历史均回滚”的测试。
4. 对带对象存储删除的命令新增“事务回滚不删文件、提交成功才删文件”的测试。