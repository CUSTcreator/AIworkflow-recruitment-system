# 候选人、岗位申请与简历导入的数据职责

## 聚合边界

- **Candidate** 是候选人主对象。一份简历上传成功就创建或复用它；简历处理页面的一行对应一个 Candidate。
- **Application** 是候选人投递到一个岗位后的招聘流程对象。招聘流程页面的一行对应一个 Application。
- **ResumeSubmission** 是一次简历导入或重建任务，不是候选人的主身份，也不驱动招聘流程主状态。
- **SourceDocument** 保存原始 PDF 及解析文本。
- **ResumeProfileRecord** 保存候选人简历结构化的版本化结果。
- **WorkflowRun** 是异步运行记录；它只描述一次后台任务，不承担业务对象状态。

## 字段来源

| 页面字段 | 唯一来源 | 说明 |
| --- | --- | --- |
| 候选人姓名、学历、专业、工作年限 | Candidate 及其 payload | 结构化成功后回填；上传初期允许是“待解析候选人”。 |
| 当前简历、当前处理阶段、结构化错误 | Candidate.payload.currentResumeSubmissionId 指向的 ResumeSubmission 与 SourceDocument | 简历处理页使用。 |
| 简历画像版本 | Candidate.payload.resumeProfileId / ResumeProfileRecord | 新版简历重建会生成新版本。 |
| 岗位名称、招聘主阶段、面试与评分结果 | Application 及 Application 关联的阶段产物 | 招聘流程页使用。 |
| 简历重建提示 | Candidate.payload 的状态投影到 Application.payload.resumeRebuildStatus | 仅作辅助提示，不改变 Application.status。 |

## 简历重建状态契约

`resumeRebuildStatus` 的合法值固定为：`idle`、`processing`、`review_required`、`failed`、`completed`。

重建开始、待确认、失败和完成时，重建服务必须同时更新 Candidate.payload 与该 Candidate 所有未删除 Application.payload；Application.status 保持原招聘阶段不变。

## 历史兼容

`ResumeSubmission.application_id` 只用于读取早期历史记录或审计，新的上传、路由、重建和读取流程不得将它作为 Candidate 与 Application 的业务关联。新的一对多关联使用 `ResumeSubmission.candidate_id` 与 `payload.createdApplicationIds`。