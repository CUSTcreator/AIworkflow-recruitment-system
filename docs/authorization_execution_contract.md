# 授权执行架构

本文件定义用户发起的 HTTP 命令和查询如何经过认证、授权、数据范围、幂等和审计。它不替代异步 `workflow_runtime`；Workflow 的 Step 恢复仍由 StepRunner 负责。

```text
HTTP Router
  ↓ 认证：get_current_user
AuthorizationService
  ↓ 权限码 + 部门范围 + 面试分配
CommandRunner（仅写操作）
  ↓ 幂等 → 业务 handler → 审计 → 原子提交
领域 Service / 状态机
```

## 统一判断公式

所有已登录操作先按资源和行为分类，不能把“看得到”误解为“可以执行任何操作”。

```text
申请可见 = 账号有效 + 数据范围覆盖申请所属部门

申请读取、完整材料、附件、轨迹、纯失败恢复 = 申请可见
敏感申请动作 = 申请可见 + 对应职责权限 + 必要的负责人限制
其他有部门归属的业务动作 = 账号有效 + 对应职责权限 + 数据范围覆盖目标部门
系统管理 = isSystemAdmin + organization 数据范围
个人轻量状态 = 账号有效 + 仅当前用户自身（不读取或修改业务资源）
```

### 职责包与数据范围

数据范围和职责包是两个独立维度：`businessScope` 决定账号可访问的部门集合，
职责包只决定允许执行哪些业务动作。接口授权最终使用 `PERMISSION_DEFINITIONS`
中的原子权限码；职责包只是管理页面的可理解组合，不是接口绕过原子校验的凭据。
组织级原子权限还会声明自己的最低范围，部门账号即使获得所属职责包也不能执行
这些操作。混合职责包会保留其中适用于当前范围的原子权限。

当前管理端职责目录固定为以下七项：

| 职责包 | 原子权限 | 范围说明 |
| --- | --- | --- |
| 候选人材料 | `resume.upload`、`resume_submission.manage`、`application.create`、`candidate_document.upload`、`candidate_document.manage` | 使用账号数据范围 |
| 岗位与筛选配置 | `job_document.upload`、`job_document.confirm`、`job.edit`、`hard_screening.policy.manage` | 前两项仅全公司范围；后两项使用账号数据范围 |
| 部门招聘执行 | `screening.run`、`hard_screening.review`、`department_review.manage`、`first_interview.manage` | 使用账号数据范围，并继续校验负责人限制 |
| HR 二面管理 | `second_interview.manage` | 使用账号数据范围，并要求 HR 负责人 |
| 最终招聘决策 | `final_decision.manage` | 使用账号数据范围，并要求 HR 负责人 |
| 全局招聘配置 | `hard_screening.catalog.manage`、`interview_guide.manage` | 仅全公司范围 |
| 删除业务记录 | `job.delete`、`application.delete` | 使用账号数据范围，并继续执行资源删除规则 |

统计查询与候选资料查看相同，只按账号有效状态和数据范围过滤，不配置查看职责。
个人例外在管理端仍显示为职责包的“允许/禁止”两态，写入时展开为原子权限覆盖，
确保现有授权服务和审计链路继续使用同一数据格式。

“纯失败恢复”只能重放冻结输入或读取已有结果，且不得推进招聘主状态、提交人工结论、
变更负责人、创建新的业务决策或改变候选人结果。只要包含这些影响，就必须归为敏感
业务动作并声明职责权限。

阶段决策按接口统一授权，不把“通过、不通过、暂缓”等选项拆成新的原子权限。例如
`department_decision`、`hr_decision`、`final_decision` 分别复用
`department_review.manage`、`second_interview.manage`、`final_decision.manage`。
`reject`、`hold`、`manual_review`、`offer` 只是状态机业务动作；同名动作出现在不同
阶段时，必须先结合当前申请状态解析到对应的阶段接口，不能固定映射到某一个权限。
阶段接口还必须声明允许的申请状态，防止客户端绕过页面后调用其他阶段的决策接口。

少数敏感资源还有资源自身的限制。例如候选附件的上传职责只允许维护本人上传的文件；
具备候选人材料管理职责后，才可维护本部门范围内其他人上传的附件。这类限制必须由
资源 Service 在统一的申请可见性和动作职责检查之后继续校验，并有独立的拒绝用例。

简历导入等复合读模型可因为任一关联申请可见而进入列表，但这不代表该用户能读取
候选人的全部关联申请。任何嵌套的申请、岗位、数量、状态或跳转链接都必须逐条复用
`AuthorizationService.can_view_application()` 过滤；禁止把外层记录的“任一可见”结论
扩展成嵌套业务数据的“全部可见”结论。

`CommandSpec.authorization_mode="authenticated_self"` 仅允许用于当前账号自己的已读游标
等轻量状态。`CommandRunner` 强制其为 `system` 资源、`resource_id == user_id`、无业务
权限码、非幂等且审计豁免。它不能替代业务职责、数据范围、负责人或系统管理员校验。

## 新接口接入清单

页面动作必须按固定顺序生成：后端先读取 Application 状态、关联 Workflow 和目标业务
资源，产生当前状态下可能存在的候选动作；再使用数据库中的账号、角色、个人权限覆盖、
数据范围和负责人关系过滤；最后通过 `availableActions` 或 `recoveryActions` 返回最终
动作。前端请求只携带登录凭证，响应中的 `permissions` 仅用于通用界面提示，不能作为
接口授权凭据，也不能把本地职责包判断与后端动作 DTO 组合成第二套授权规则。实际写
接口必须重新加载目标资源并执行同一动作授权，防止客户端伪造按钮或绕过页面调用。

其中 `availableActions` 保存正常流程中的敏感命令动作；`recoveryActions` 保存异常恢复
动作及其标签、输入要求和恢复范围。恢复动作需要 Submission、Job 等明确目标时，后端
必须确认目标存在后才能下发。未知恢复码不得猜测成 V1/V2/V3 的任一重试动作。

新增或修改已登录接口时，先登记资源、目标部门、动作类别、权限码、负责人限制和测试
用例，再开始实现。检查顺序既要按所有 HTTP Router/CommandSpec 列表覆盖，也要按招聘
流程链路复核：简历导入、岗位配置、初筛、部门决策、技术一面、HR 二面、最终决策、
取消/删除和失败恢复。

| 操作类别 | 后端唯一授权入口 | 要求 |
| --- | --- | --- |
| 申请读取、材料、附件、轨迹 | `require_application_view` / `require_application_material_view` | 先加载申请并校验可见性 |
| 申请敏感动作或纯恢复 | `require_application_action` | 动作必须登记在 `APPLICATION_ACTION_REQUIREMENTS` |
| 简历处理记录 | `require_resume_submission_action` | 动作必须登记在 `RESUME_SUBMISSION_ACTION_REQUIREMENTS` |
| 岗位等有部门归属的业务操作 | `require_business_action` | 必须传入目标资源的 `department_id` |
| 系统管理 | `require_system_admin` | 不以普通业务权限替代管理员身份 |
| 当前用户个人游标/偏好 | `CommandRunner(authenticated_self)` | 仅可修改 `resource_id == user_id` 的轻量状态 |

列表、导出和统计必须在分页、计数、聚合之前应用 `ScopeService` 生成的数据范围；前端
仅根据后端结论展示或隐藏操作，永远不是授权边界。每个敏感动作至少测试：允许、职责
缺失拒绝、跨部门拒绝；一面/二面动作还必须测试非负责人拒绝。纯恢复还必须测试可见
用户允许且不可见用户拒绝。

### 权限矩阵回归入口

交付前在真实源码目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/ops/test-permission-matrix.ps1
```

该入口会枚举运行时注册的登录后 API，并要求每个方法都有测试侧矩阵记录；同时校验
`APPLICATION_ACTION_REQUIREMENTS` 和 `RESUME_SUBMISSION_ACTION_REQUIREMENTS` 的每个动作
都已登记。随后运行权限组合、真实 HTTP 越权、页面动作投影和简历恢复测试。新增路由或
授权动作而未补矩阵时，测试必须失败，不能以“暂时没有页面入口”为理由跳过。

## 模块职责

- `modules/auth/domain`：权限目录、访问上下文和纯授权规则。
- `modules/auth/services`：从数据库解析角色与用户覆盖权限，并执行授权、范围和字段脱敏。
- `modules/auth/http/guards.py`：资源无关的 FastAPI 授权依赖。
- `infrastructure/command_runtime`：同步写命令的授权、幂等、审计和事务边界。
- `modules/system_admin`：角色、账号、部门等系统管理业务；不是授权执行运行时。

职责包可声明 `required_business_scope="organization"`。岗位文档导入、全局硬筛条件库和
通用面试题单等尚未绑定单一部门或影响全公司的职责必须使用这一约束；角色和个人职责
例外保存时由后端拒绝部门范围账号的“允许”配置。岗位编辑和岗位级硬筛策略则继续使用
角色的数据范围，可配置给部门范围角色。

资源级授权不能只由全局 HTTP middleware 执行：middleware 尚未加载 Application，无法判断其部门和面试负责人。Application 资源必须由 CommandRunner 或对应 Service 加载后调用 `AuthorizationService.require_application_action()`。

## 渐进迁移约定

现有 `auth/authorization.py` 和各模块的手工校验暂时保留。新接口优先使用 `AuthorizationService` 和 `CommandRunner`；旧接口逐个迁移、权限矩阵测试通过后，再删除兼容函数。禁止在迁移期间同时执行两套互不一致的授权逻辑。
