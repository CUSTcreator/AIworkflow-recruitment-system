# 简历导入与结构化数据合同

## 目标

简历导入链路不再把跨阶段业务事实塞入泛化的 `payload`。每个持久化结果必须有明确归属、字段名和合同版本；`payload` 只在迁移期读取历史记录，不能作为新写路径。

## 对象边界

| 对象 | 职责 | 正式字段 |
| --- | --- | --- |
| `SourceDocument` | 原始上传文件，不随重新解析而改写 | 文件对象引用、MIME、SHA-256 |
| `Candidate` | 候选人聚合根及当前版本指针 | `current_resume_submission_id`、`current_resume_profile_id` |
| `ResumeSubmission` | 一次上传、重新上传或重新解析任务 | `parsed_text`、`parse_result_json`、结构化结果引用、结构化基础信息、复核上下文、岗位分发结果 |
| `ResumeProfileRecord` | 已发布、不可变的结构化简历版本 | `profile_schema_version`、`profile_json` |
| `WorkflowArtifact` / 检查点 | 工作流恢复所需的临时或阶段性产物 | artifact 类型、schema 版本、内容引用、校验摘要 |

## 合同和版本

Python 合同定义位于 `backend/app/modules/document_ingestion/contracts/resume_intake_contracts.py`。边界处必须先校验，再持久化：

1. `ResumeSourceSnapshot`：冻结本次任务使用的 Submission、文件和输入版本。
2. `DocumentParseResult`：解析文本和文档块引用；数据库摘要不重复保存大文本。
3. `ResumeStructureResult`：结构化产物引用、哈希、复核状态和活动执行轨迹。
4. `ResumeProfileSchema`：正式 `ResumeProfileRecord.profile_json` 的顶层字段合同。

结构化 Activity 的 `input_data` 还必须包含 `contractVersion` 与实际局部输入。`ActivityRunner` 因此能依据输入摘要判断旧检查点能否复用，而不是只按活动名称跳过。

## 迁移纪律

迁移 `074_resume_intake_contracts` 与 `075_resume_structure_metadata` 先增加具名列并回填历史数据。读模型按“新字段优先、旧 `payload` 仅回退”的顺序读取。确认线上任务和历史数据已完成迁移、且不存在旧读路径后，才可以在后续独立迁移中删除遗留列；不能与本次写路径切换合并执行。
