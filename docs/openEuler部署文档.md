# 招聘系统 openEuler 部署与交付文档

## 1. 适用范围

本文档针对当前项目的 Docker Compose 部署方式，适用于将项目部署到 openEuler 服务器。

当前 Compose 包含：

| 服务 | 作用 | 容器端口 |
| --- | --- | --- |
| frontend | React 前端，由 Nginx 提供静态文件 | 80 |
| backend | FastAPI 后端 API | 8000 |
| worker | 异步招聘流程 Worker | 无对外端口 |
| importer | 上游候选人文件夹导入 API | 8010 |
| postgres | PostgreSQL 数据库 | 5432 |
| minio | 简历和文档对象存储 | 9000、9001 |

PostgreSQL 和 MinIO 使用 Docker 命名卷 postgres_data、minio_data 持久化。删除容器不会删除数据卷，除非显式执行带 -v 的删除命令。

## 2. 部署前提

### 2.1 系统和硬件

- openEuler 22.03 LTS 或更高版本。
- 推荐 x86_64 架构。
- 当前导出的镜像通常是 linux/amd64。如果服务器是 aarch64，不能直接加载 amd64 镜像，需要在服务器上重新构建或制作多架构镜像。
- 建议至少 4 个 CPU、8 GB 内存和 30 GB 可用磁盘空间。简历文件、数据库和对象存储会持续占用磁盘。

### 2.2 Docker

需要 Docker Engine 和 Docker Compose V2 插件，不使用 Docker Desktop。

如果公司内部软件源已经提供 Docker，优先使用内部软件源：

~~~bash
sudo dnf install -y docker docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
~~~

重新登录后验证：

~~~bash
docker --version
docker compose version
sudo systemctl status docker --no-pager
docker run --rm hello-world
~~~

如果系统提示找不到 docker-compose-plugin，按照公司软件源文档安装 Compose V2 插件。如果只能使用 sudo docker，后续命令前统一加 sudo。

## 3. 最终交付文件

建议交付以下 3 个文件：

~~~text
recruit-system-V1.0-source.tar.gz
recruit-system-v10-images.tar
SHA256SUMS
~~~

| 文件 | 作用 |
| --- | --- |
| `recruit-system-V1.0-source.tar.gz` | Compose、源码、配置模板、Dockerfile 和数据库迁移 |
| `recruit-system-v10-images.tar` | Backend、Worker、Frontend、Importer、PostgreSQL 和 MinIO 镜像 |
| `SHA256SUMS` | 检查 U 盘拷贝后文件是否损坏 |

源码包不应包含 `.git`、测试目录、真实 `.env`、`config/llm.local.json`、简历与个人材料、本地数据库、日志、缓存和构建产物。开发机上应另行保留包含 Git 历史和测试的完整备份。

## 4. Windows 生成交付包

在真实项目根目录的 PowerShell 中执行：

~~~powershell
cd "D:\For studying-or-working\Develop-Project\recruit-system-V1.0"
~~~

### 4.1 生成源码包

~~~powershell
tar -czf ..\recruit-system-V1.0-source.tar.gz `
  --exclude=.git `
  --exclude=.env `
  --exclude=config/llm.local.json `
  --exclude=backend/tests `
  --exclude=importer/tests `
  --exclude=frontend/node_modules `
  --exclude=frontend/dist `
  --exclude=import-data `
  --exclude=.pytest_cache `
  --exclude='*/__pycache__' `
  --exclude='*/__pycache__/*' `
  --exclude='*.db' `
  --exclude='*.log' `
  --exclude='*.tar' `
  --exclude='*.tar.gz' `
  .
~~~

检查是否误入敏感或本地文件：

~~~powershell
tar -tzf ..\recruit-system-V1.0-source.tar.gz |
  Select-String -Pattern '(^|/)\.env$|llm\.local\.json$|\.db$|\.log$|import-data|(^|/)\.git/'
~~~

正常情况下该命令不应输出任何匹配项。`.env.example` 和 `config/llm.local.example.json` 必须保留在包中。

### 4.2 生成离线镜像包

先确保 Docker Desktop 已启动且使用 Linux Engine：

~~~powershell
docker info
docker image ls
~~~

离线前端镜像在构建时就会固化 `VITE_API_BASE_URL`。如果甲方正式服务器地址已确定，应先将开发机 `.env` 中的 `VITE_API_BASE_URL` 设为浏览器可访问的正式 Backend 地址，再构建最终镜像。例如：

~~~dotenv
VITE_API_BASE_URL=http://192.168.1.20:8000/api/v1
~~~

在 Windows 真实项目目录中构建最终镜像：

~~~powershell
& .\scripts\ops\compose.ps1 -ComposeArgs @(
  'build', 'backend', 'worker', 'frontend', 'importer'
)
~~~

如正式 IP 或域名尚未确定，甲方需在 openEuler 上确定地址后重新构建 `frontend`。仅执行 `docker load` 和重启容器不能修改已固化的前端 API 地址。

镜像应包含 `recruit-system-v10-backend:latest`、`recruit-system-v10-worker:latest`、`recruit-system-v10-frontend:latest`、`recruit-system-v10-importer:latest`、`postgres:16-alpine` 和 `minio/minio:latest`。

~~~powershell
docker save -o ..\recruit-system-v10-images.tar `
  recruit-system-v10-backend:latest `
  recruit-system-v10-worker:latest `
  recruit-system-v10-frontend:latest `
  recruit-system-v10-importer:latest `
  postgres:16-alpine `
  minio/minio:latest
~~~

如提示无法连接 `dockerDesktopLinuxEngine`，说明 Docker Desktop 未启动或 Docker context 错误。启动 Docker Desktop，并在 `docker info` 成功后重新导出。

### 4.3 生成校验文件

~~~powershell
Get-FileHash `
  ..\recruit-system-V1.0-source.tar.gz, `
  ..\recruit-system-v10-images.tar `
  -Algorithm SHA256 |
ForEach-Object {
  "{0}  {1}" -f $_.Hash.ToLower(), (Split-Path $_.Path -Leaf)
} | Set-Content ..\SHA256SUMS -Encoding ascii
~~~

将上述 3 个文件一起复制到 U 盘。

## 5. 将交付包复制到服务器

不要长期直接从 U 盘运行，先复制到服务器本地磁盘。例如：

~~~bash
sudo mkdir -p /opt/recruit-system/delivery
sudo cp /media/usb/recruit-system-V1.0-source.tar.gz /opt/recruit-system/delivery/
sudo cp /media/usb/recruit-system-v10-images.tar /opt/recruit-system/delivery/
sudo cp /media/usb/SHA256SUMS /opt/recruit-system/delivery/
sudo chown -R "$USER":"$USER" /opt/recruit-system
cd /opt/recruit-system/delivery
sha256sum -c SHA256SUMS
mkdir -p /opt/recruit-system/recruit-system-V1.0
tar -xzf recruit-system-V1.0-source.tar.gz -C /opt/recruit-system/recruit-system-V1.0
cd /opt/recruit-system/recruit-system-V1.0
~~~

两个校验项都显示 `OK` 后再部署。解压后必须能看到 `docker-compose.yml`、`.env.example`、`backend/`、`frontend/`、`importer/`、`config/` 和 `docker/`。

## 6. 配置生产环境

交付后的配置入口如下。除这些位置外，正常部署不需要修改源代码：

| 文件或位置 | 是否必须配置 | 用途 |
| --- | --- | --- |
| `.env` | 是 | 数据库、MinIO、系统密钥、访问地址及 Importer 宿主机目录和凭据 |
| `config/llm.local.json` | 是 | 大模型、MinerU 和视觉解析服务；由 `config/llm.local.example.json` 复制生成 |
| `config/importer.yml` | 仅命名规则变化时 | 调整简历及其他材料的文件名匹配规则；Docker 部署时不要修改 `/import-data` |
| `docker-compose.yml` | 通常否 | 只有端口、挂载方式或部署拓扑发生变化时才修改 |
| 防火墙或反向代理配置 | 按公司网络要求 | 控制 `5173`、`8000`、`8010` 的访问范围，其中 `8010` 应只允许上游系统访问 |

`config/llm.local.json` 是标准 JSON 文件，不支持注释；各字段用途参照本节和示例文件填写。

~~~bash
cd /opt/recruit-system/recruit-system-V1.0
cp .env.example .env
cp config/llm.local.example.json config/llm.local.json
chmod 600 .env config/llm.local.json
vi .env
vi config/llm.local.json
~~~

.env 至少需要配置：

| 配置项 | 说明 |
| --- | --- |
| APP_ENV | 生产环境设为 `production` |
| POSTGRES_DB | 数据库名称，通常为 recruit_ai |
| POSTGRES_USER | 数据库用户名 |
| POSTGRES_PASSWORD | 数据库密码，建议使用 URL 安全字符 |
| MINIO_ROOT_USER | MinIO 管理用户名 |
| MINIO_ROOT_PASSWORD | MinIO 管理密码，至少 8 个字符 |
| MINIO_BUCKET | 对象存储桶名称，通常为 recruit-ai |
| AUTH_SECRET_KEY | 登录令牌签名密钥，使用随机长字符串 |
| DEPARTMENT_MANAGER_PASSWORD | 部门经理初始密码 |
| DEPARTMENT_RECRUITER_PASSWORD | 部门招聘人员初始密码 |
| HR_PASSWORD | HR 初始密码 |
| CORS_ORIGINS | 允许访问后端的前端地址 |
| VITE_API_BASE_URL | 前端构建时使用的后端 API 地址 |
| PIP_INDEX_URL | 后端镜像构建时使用的 Python 包源，可配置为公司内部源 |
| NPM_REGISTRY | 前端镜像构建时使用的 npm 源，可配置为公司内部源 |
| IMPORTER_WORKSPACE_DIR | 上游投放候选人文件夹的宿主机目录 |
| IMPORTER_USERNAME | Importer 调用招聘系统使用的账号，默认 hr |
| IMPORTER_PASSWORD | 上述招聘系统账号的密码 |
| IMPORTER_API_KEY | 上游调用 Importer API 时携带的随机密钥 |

假设服务器 IP 是 192.168.1.20：

~~~dotenv
APP_ENV=production
CORS_ORIGINS=http://192.168.1.20:5173
VITE_API_BASE_URL=http://192.168.1.20:8000/api/v1
DOCUMENT_PARSER_BACKEND=auto
LOG_LEVEL=INFO
# 如公司网络不能访问公共镜像，改成公司内部的 Python/npm 镜像
# PIP_INDEX_URL=https://pypi.example.com/simple
# NPM_REGISTRY=https://npm.example.com/
IMPORTER_WORKSPACE_DIR=/data/recruit-candidate-import
IMPORTER_USERNAME=hr
IMPORTER_PASSWORD=请填写招聘系统账号密码
IMPORTER_API_KEY=请填写独立的长随机密钥
~~~

VITE_API_BASE_URL 在前端镜像构建阶段写入静态文件。修改后必须重新构建 frontend，仅重启容器不会生效。CORS_ORIGINS 必须包含用户实际打开页面的地址。

`config/llm.local.json` 是标准 JSON 文件，不支持注释。如需使用大模型，将顶层 `enabled` 设为 `true`，并填写大模型、MinerU 或视觉解析服务的真实地址与密钥。

创建上游候选人文件夹工作目录，并确保 Docker 可以读写：

~~~bash
sudo mkdir -p /data/recruit-candidate-import
sudo chown -R "$USER":"$USER" /data/recruit-candidate-import
chmod 750 /data/recruit-candidate-import
~~~

候选人文件夹直接放在该目录下。Importer 会自动创建并管理“已上传”目录，文件名匹配规则位于 `config/importer.yml`。

## 7. 启动服务

### 7.1 在线构建

服务器能够访问 Docker Hub、PyPI 和 npm 镜像源时：

~~~bash
cd /opt/recruit-system/recruit-system-V1.0
docker compose -p recruit-system-v10 up -d --build --force-recreate
~~~

backend 和 worker 启动入口会执行数据库版本检查和应用初始化。不要在宿主机直接执行 Alembic 迁移。

### 7.2 离线镜像

如果使用交付的离线镜像包：

~~~bash
cd /opt/recruit-system/recruit-system-V1.0
docker load -i /opt/recruit-system/delivery/recruit-system-v10-images.tar
docker image ls | grep -E 'recruit-system-v10|postgres|minio'
docker compose -p recruit-system-v10 up -d --no-build
~~~

固定使用项目名 recruit-system-v10，使 Compose 能找到导出的应用镜像。若镜像不存在，--no-build 会报错，应改用在线构建或重新导出镜像。

openEuler 上不要执行项目中的 scripts/ops/compose.ps1，该脚本仅用于 Windows PowerShell。Linux 直接使用 docker compose。

## 8. 验证部署

~~~bash
docker compose -p recruit-system-v10 ps
curl -f http://127.0.0.1:8000/health
curl -f http://127.0.0.1:8000/ready
curl -f http://127.0.0.1:8010/health
curl -I http://127.0.0.1:5173/
~~~

预期后端返回：

~~~text
{"status":"ok"}
{"status":"ready"}
{"status":"ready"}
HTTP/1.1 200 OK
~~~

从其他电脑访问时，将 127.0.0.1 换成服务器 IP，例如 http://192.168.1.20:5173/。

## 9. Importer 配置与验证

### 9.1 目录约定

`.env` 中的 `IMPORTER_WORKSPACE_DIR` 是 openEuler 宿主机上游投放候选人文件夹的目录。建议创建专用目录：

~~~bash
sudo mkdir -p /data/recruit-candidate-import
sudo chown -R "$USER":"$USER" /data/recruit-candidate-import
chmod 750 /data/recruit-candidate-import
~~~

Importer 将宿主机目录挂载到容器内 `/import-data`，因此 `config/importer.yml` 中的 `workspace_dir: /import-data` 通常不修改。工作目录结构例如：

~~~text
/data/recruit-candidate-import/
├── 候选人001/
│   ├── 张三简历.pdf
│   ├── 本科成绩单.pdf
│   └── 资格证书.pdf
└── 已上传/
~~~

上游必须先完整写入某个候选人文件夹，再调用 API 传入该文件夹名。Importer 只处理请求中明确列出的工作目录直接子文件夹。

### 9.2 提交导入

~~~bash
curl -X POST http://127.0.0.1:8010/api/v1/imports \
  -H 'X-Importer-Api-Key: 请填写IMPORTER_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"folders":["候选人001"]}'
~~~

POST 是异步提交接口。正常情况会立即返回 HTTP `202`、`requestId` 和 `status: queued`。`queued` 只表示文件夹已进入队列，不表示导入已完成。

### 9.3 查询最终结果

将 POST 响应中的 `requestId` 替换到下方地址：

~~~bash
curl http://127.0.0.1:8010/api/v1/imports/REQUEST_ID \
  -H 'X-Importer-Api-Key: 请填写IMPORTER_API_KEY'
~~~

| 批次状态 | 含义 |
| --- | --- |
| `queued` | 已入队，尚未开始 |
| `running` | 正在处理 |
| `completed` | 所有接受的文件夹均导入成功 |
| `partial_failed` | 部分成功，部分失败或被拒绝 |
| `failed` | 未成功导入任何文件夹 |

上游程序应保存 `requestId`，并通过 GET 接口获取逐文件夹的最终结果。批次状态保存在 Importer 进程内，容器重启后旧 `requestId` 可能无法查询。

### 9.4 断点续传和重新导入

Importer 会在候选人文件夹中写入 `.import-state.json`，记录 `importRunId`、简历和附件 SHA-256、`candidateId`、已上传文件 ID、处理时间及错误信息。

- 普通失败重试时必须保留该文件。Importer 会沿用原 `importRunId`，只继续未完成的文件。
- 成功后候选人文件夹连同状态文件被移入“已上传”。
- 只有在管理员已删除系统内候选人，并明确要将原文件夹作为全新任务重新导入时，才将文件夹移回工作目录并删除 `.import-state.json`。下次提交会生成新 `importRunId`。

不应在任务处理中或普通重试时删除状态文件。

## 10. 防火墙和端口

当前 Compose 映射：

| 端口 | 用途 | 建议 |
| --- | --- | --- |
| 5173 | 前端页面 | 按办公网范围开放 |
| 8000 | 后端 API | 按办公网范围或前端服务器开放 |
| 8010 | 候选人导入 API | 仅向上游程序所在网段开放 |
| 5432 | PostgreSQL | 不建议对外开放 |
| 19000 | MinIO API | 不建议对外开放 |
| 19001 | MinIO 控制台 | 仅管理员临时访问 |

使用 firewalld 时，可只开放前端和后端：

~~~bash
sudo firewall-cmd --permanent --add-port=5173/tcp
sudo firewall-cmd --permanent --add-port=8000/tcp
sudo firewall-cmd --reload
~~~

生产环境建议把数据库和 MinIO 的 ports 绑定到 127.0.0.1，或删除这些 ports，使它们只在 Compose 内部网络可访问。

## 11. 日常运维

查看日志：

~~~bash
docker compose -p recruit-system-v10 logs --tail=200 backend worker
docker compose -p recruit-system-v10 logs -f backend worker importer
~~~

重启服务：

~~~bash
docker compose -p recruit-system-v10 restart backend worker importer frontend
~~~

停止服务但保留数据：

~~~bash
docker compose -p recruit-system-v10 stop
~~~

删除容器但保留数据卷：

~~~bash
docker compose -p recruit-system-v10 down
~~~

除非确认要清空数据库和简历文件，否则禁止执行：

~~~bash
docker compose -p recruit-system-v10 down -v
~~~

### 11.1 更新离线镜像

只把新 tar 包复制到服务器不会更新运行中的容器。收到新镜像包后执行：

~~~bash
docker load -i /path/to/recruit-system-v10-images.tar
cd /opt/recruit-system/recruit-system-V1.0
docker compose -p recruit-system-v10 up -d --no-build --force-recreate
docker compose -p recruit-system-v10 ps
~~~

## 12. 数据备份

备份 PostgreSQL：

~~~bash
mkdir -p backups
docker compose -p recruit-system-v10 exec -T postgres \
  sh -lc 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "backups/recruit_ai_$(date +%Y%m%d_%H%M%S).sql"
~~~

MinIO 中保存的是简历等对象文件，需要同时制定对象存储备份方案。数据库备份不能替代 MinIO 文件备份。

PostgreSQL 和 MinIO 必须成对备份和恢复：数据库保存文件元数据及 MinIO Key，MinIO 保存真实文件，只恢复其中一个不能完整恢复系统。

## 13. 常见问题

### 前端能打开但请求后端失败

检查 VITE_API_BASE_URL 是否使用服务器 IP，而不是 127.0.0.1，然后重新构建：

~~~bash
docker compose -p recruit-system-v10 up -d --build frontend
~~~

同时检查 CORS_ORIGINS 是否包含前端实际访问地址。

### backend 一直不健康

~~~bash
docker compose -p recruit-system-v10 logs --tail=200 backend
~~~

重点检查 PostgreSQL 状态、.env 数据库变量、config/llm.local.json 是否存在，以及 8000 端口是否被占用。

### worker 没有处理任务

~~~bash
docker compose -p recruit-system-v10 ps worker
docker compose -p recruit-system-v10 logs --tail=200 worker
~~~

确认 Worker 处于 Up 状态，并检查数据库连接、模型服务配置和 WORKFLOW_WORKER_CONCURRENCY。

### Importer 提交后一直看到 `queued`

POST 只负责提交异步任务。应使用响应中的 `requestId` 调用 GET 状态接口，查看最终的 `completed`、`partial_failed` 或 `failed`。同时可查看：

~~~bash
docker compose -p recruit-system-v10 logs --tail=200 importer
~~~

### SELinux 导致配置挂载失败

如果出现 bind mount 或权限拒绝，先查看 SELinux 审计日志，并按公司容器安全规范给项目目录设置正确标签。不要直接关闭 SELinux。必要时可将配置文件挂载调整为带 Z 的只读挂载后重新创建容器。

### 服务器是 aarch64

~~~bash
uname -m
docker image inspect recruit-system-v10-backend \
  --format '{{.Architecture}}/{{.Os}}'
~~~

服务器和镜像架构不一致时，应在服务器上重新构建或使用支持 linux/arm64 的多架构镜像。

## 14. 交付检查清单

- [ ] 源码包未包含 `.env`、`config/llm.local.json`、本地数据库、日志和候选人个人材料。
- [ ] `SHA256SUMS` 在 openEuler 上校验通过。
- [ ] Docker Engine 和 Compose V2 已安装。
- [ ] 服务器架构与镜像架构一致。
- [ ] .env 和 config/llm.local.json 已使用公司配置，未保留开发机真实密钥。
- [ ] VITE_API_BASE_URL 使用服务器可访问地址，并已重新构建前端。
- [ ] CORS_ORIGINS 与实际访问来源一致。
- [ ] Importer 工作目录已创建，容器具有读写权限。
- [ ] 数据库和 MinIO 数据卷已纳入备份计划。
- [ ] 仅按办公网范围开放必要端口。
- [ ] docker compose ps 状态正常。
- [ ] /health、/ready 和前端首页检查通过。
- [ ] 已完成一次简历上传或 Importer 导入，并通过 GET 接口查询到最终批次状态。
- [ ] 已验证候选人材料挂在 Candidate 下并可正常查看。
