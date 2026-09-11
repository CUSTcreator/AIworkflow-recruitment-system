# 招聘评估系统

招聘评估系统用于管理岗位、候选人资料和完整招聘流程。系统由 Backend、Frontend、Worker、Importer、PostgreSQL 和 MinIO 组成。

## 组件

| 组件 | 作用 |
| --- | --- |
| Backend | 核心 API、权限、招聘状态机和业务工作流 |
| Frontend | 浏览器端招聘工作台 |
| Worker | 执行异步评分、解析和岗位画像工作流 |
| Importer | 扫描外部候选人文件夹并调用 Backend 导入简历 |
| PostgreSQL | 业务数据库 |
| MinIO | 简历、岗位文件等对象存储 |

## 环境要求

- Windows 和 Docker Desktop（Linux Engine）
- 可用端口：`5173`、`8000`、`8010`、`5432`、`19000`、`19001`
- 已配置的 `.env`
- `config/llm.local.json`

## 首次配置

在项目根目录执行：

```powershell
Copy-Item .env.example .env
```

至少填写数据库、MinIO、登录密钥、预置账号和 Importer 配置。生产环境必须替换模板中的密码和密钥，不要把 `.env` 提交到 Git。

`IMPORTER_WORKSPACE_DIR` 是宿主机投放候选人文件夹的目录；`IMPORTER_API_KEY` 只用于保护 Importer 的 HTTP 接口。

## 启动与停止

所有 Docker 操作必须在本项目真实目录执行，并使用 `scripts/ops` 脚本：

```powershell
cd "D:\For studying-or-working\Develop-Project\recruit-system-V1.0"
.\scripts\ops\preflight.ps1 -RequireDocker
.\scripts\ops\compose.ps1 up -d --build
.\scripts\ops\compose.ps1 ps
```

停止服务：

```powershell
.\scripts\ops\compose.ps1 stop
```

不要使用 `docker compose down -v`，它会删除 PostgreSQL 和 MinIO 数据卷。

## 访问地址

- 前端：http://127.0.0.1:5173
- Backend API：http://127.0.0.1:8000/api/v1
- Backend 健康检查：http://127.0.0.1:8000/ready
- Importer：http://127.0.0.1:8010
- MinIO 控制台：http://127.0.0.1:19001

## 基本使用流程

1. 使用 HR 账号登录。
2. 在系统管理中确认部门、角色、数据范围和职责包。
3. 在岗位管理中导入并确认岗位 JD。
4. 在岗位详情中维护岗位信息和岗位级硬筛规则。
5. 导入或分发候选人简历。
6. 按初筛、部门审核、一面、二面和最终决策顺序处理申请。
7. 在异常状态下查看后端返回的恢复动作并重试或修复。

## 权限概要

权限由数据范围和职责包共同决定：

- 普通读取：账号有效，并且数据属于本部门或全公司范围。
- 敏感业务操作：数据可见，并拥有对应职责包展开的原子权限；必要时还检查负责人。
- 纯失败恢复：对可见数据开放，但仍受当前状态约束。
- 系统管理：需要全公司范围的系统管理员身份。

岗位 JD 导入、确认创建、全局硬筛条件库和通用面试题单目前要求全公司范围。详细约定见 [docs/authorization_execution_contract.md](docs/authorization_execution_contract.md)。

## 数据库迁移

数据库使用 Alembic。迁移只能通过真实项目目录下的运维脚本执行：

```powershell
.\scripts\ops\db-status.ps1
.\scripts\ops\db-upgrade.ps1 -Confirm
```

不要在隔离工作树或主机环境直接执行 Alembic。

## 常用排查

```powershell
.\scripts\ops\compose.ps1 logs --tail 200 backend
.\scripts\ops\compose.ps1 logs --tail 200 worker
.\scripts\ops\compose.ps1 logs --tail 200 importer
Invoke-WebRequest http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:8010/health
```

- `401`：登录账号、密码或 Importer API Key 无效。
- `403`：数据范围、职责包或负责人条件不满足。
- `404`：目标资源或 Importer 批次不存在。
- `409`：当前状态不允许该操作。
- 工作流不继续：检查 `worker` 容器日志。
- 文件无法读取：检查 MinIO 和对象存储配置。

## 交付检查

- [ ] `.env` 已填写，且没有提交真实密钥
- [ ] Backend 为 `healthy`
- [ ] Worker 和 Importer 正常运行
- [ ] Frontend 可访问
- [ ] `/ready` 和 `/health` 返回 200
- [ ] 可以登录并查看岗位、候选人
- [ ] 可以完成一次岗位或候选人导入
- [ ] 已验证本部门和全公司数据范围
- [ ] 已验证异常任务的查看和恢复

更多开发说明见 [frontend/README.md](frontend/README.md)、[importer/README.md](importer/README.md) 和 `docs/` 目录。
