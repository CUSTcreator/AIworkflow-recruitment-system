# 后端业务模块职责

本目录按业务能力划分。HTTP URL 中出现 `/applications/{id}` 只表示资源归属，具体 Router 应放在实现该业务能力的模块中。

| 模块 | 负责范围 | 跨模块入口 |
| --- | --- | --- |
| `auth` | 登录、当前用户、角色与权限判断 | `auth/public.py` |
| `system_admin` | 账号、角色、部门和系统运维 | 无，管理员 HTTP 接口 |
| `document_ingestion` | JD 与简历文件上传、解析、结构化和导入工作流 | `document_ingestion/public.py` |
| `candidates` | Candidate 聚合、档案、简历处理与重建 | `candidates/public.py` |
| `jobs` | 岗位、JD 版本和岗位生命周期 | `jobs/public.py` |
| `applications` | 某 Candidate 对某 Job 的申请、招聘主状态、列表、时间线和申请附件 | `applications/public.py` |
| `assessment` | 硬筛、初筛评分、评分结果、证据和初筛审核读模型 | `assessment/public.py` |
| `interviews` | 一面/二面的计划、执行、面评及后续评分 | `interviews/public.py` |
| `interview_guides` | 面试题单模板、岗位绑定和题单导出 | 无，模板服务与 HTTP 接口 |
| `tasks` | 当前用户待办的只读聚合查询 | 无 |
| `analytics` | 招聘统计与分析看板的只读查询 | 无 |

## 依赖规则

1. 模块间只能导入对方 `public.py` 中声明的能力，不直接引用其内部 `commands`、`queries`、`workflows` 或具体 Service。
2. `router.py` 只做参数校验、权限校验和调用公开服务，不承载业务规则。
3. `WorkflowRun` 是跨模块后台任务运行时记录；具体任务处理器必须归其业务模块。
4. `Application` 是岗位申请生命周期的聚合根；`Candidate` 是候选人及其简历版本的聚合根。
5. 评估和面试页面可以使用 `/applications/{application_id}/...` URL，因为页面资源属于某份申请；这不要求实现代码放进 `applications` 模块。