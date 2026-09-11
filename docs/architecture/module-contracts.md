# 三个业务模块公开契约

## 边界与依赖方向

```text
jobs.public
   ↓
candidates（读取可分发岗位版本、订阅岗位画像就绪事件）

candidates.public + jobs.public
   ↓
applications（创建申请、切换采用的简历版本）

applications
   × 不直接修改 Candidate / Job 的业务状态
```

## Candidate 简历管理

- 拥有：`Candidate`、`ResumeSubmission`、`SourceDocument`、`ResumeProfileRecord`。
- 对外：`get_published_resume_snapshot()`、`get_candidate_resume_rebuild_view()`、`resume_waiting_routing_for_job_profile()`。
- `PublishedResumeSnapshot` 只包含候选人、简历提交、简历画像、源文件的冻结 ID 和哈希。
- `CandidateResumeRebuildView` 仅供 Application 读模型显示辅助提示，不得改变招聘主状态。

## Job 岗位管理

- 拥有：`JobDraft`、`Job`、`JobVersionRecord`、`JobRequirementProfileRecord`。
- 对外：`list_open_job_routing_versions()`、`list_routable_job_versions()`、`get_routable_job_version()`、`JobProfileReadyEvent`。
- `list_open_job_routing_versions()` 是候选人分发唯一允许读取的岗位快照：它同时表达“可立即分发”与“等待画像”，不泄漏 ORM 实体。
- 可路由岗位必须同时满足：`Job.status=open`、未删除、`JobVersion.profile_status=ready`、存在精确绑定该版本与哈希的岗位画像。

## Application 招聘主流程

- 拥有：`Application.status` 及硬筛、评分、面试、Offer 主流程。
- 对外：`create_application_from_routing()`、`apply_candidate_resume_rebuild()`。
- 两个写命令均不提交事务；调用方负责将对象写入、审计事件和工作流入队放在同一短事务中。
- 简历重建只能更新 `adopted_resume_submission_id`、`source_resume_profile_id` 并调度重评分；不得改变 `Application.status`。
- 自动分发与人工选岗都必须调用同一创建命令；Application 在内部完成幂等判断及 V1 入队，Candidate 不得重复入队。

## 已接入的交叉链路

- 岗位画像 `ready`：Job 构造 `JobProfileReadyEvent`，调用 Candidate 订阅函数，只唤醒等待中的分发任务。当前为同一短事务内的直接投递；事件载荷已稳定，未来替换 Outbox 不改变订阅接口。
- 自动/人工岗位分发：Candidate 冻结 `JobVersion + JobRequirementProfile` 的 ID，调用 Application 创建命令；Application 创建或复用申请并入队 V1。
- 简历重建完成：Candidate 更新自身当前简历指针，调用 Application 重建命令；Application 切换所有附属申请采用的简历版本并入队重评分。

## 禁止事项

- 跨模块不得传递 ORM 实体作为公开契约输入。
- 跨模块不得直接写对方的 `status` 字段。
- 不得以可变的“当前 Job/当前简历”替代命令中的冻结版本 ID。
- 不得让技术运行失败伪装成 `review_required` 业务状态。