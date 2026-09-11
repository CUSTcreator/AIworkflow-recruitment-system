# 候选人文件夹导入器

Importer 是独立的批量导入服务。它扫描宿主机目录中的候选人文件夹，通过招聘系统 Backend API 上传文件，不直接连接 PostgreSQL 或 MinIO。

## 运行方式

推荐随完整系统启动：

```powershell
cd "D:\For studying-or-working\Develop-Project\recruit-system-V1.0"
.\scripts\ops\compose.ps1 up -d --build importer
.\scripts\ops\compose.ps1 ps
```

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8010/health
```

应返回 `{"status":"ready"}`。Importer 依赖 Backend；Backend 未健康时，Importer 不会正常处理上传任务。

## 目录约定

工作目录的直接子目录均视为候选人文件夹：

```text
import-data/
  张三/
    张三简历.pdf
    本科成绩单.pdf
    职业资格证书.pdf
  已上传/
```

上游程序应在候选人文件夹写入完成后，再调用导入接口并传入本批文件夹名称。成功后 Importer 移动整个候选人文件夹；失败时保留原目录并写入 `.import-state.json`，供后续断点续传。

文件名匹配规则位于 `config/importer.yml`：每个候选人文件夹必须恰好有一份匹配简历规则的 PDF；其余 PDF 按资料分类上传，未匹配的 PDF 使用 `other` 类型；非 PDF 文件会被忽略。

## 配置

在根目录 `.env` 中配置宿主机目录和凭据：

```env
IMPORTER_WORKSPACE_DIR=./import-data
IMPORTER_USERNAME=hr
IMPORTER_PASSWORD=招聘系统账号密码
IMPORTER_API_KEY=随机生成的长密钥
```

容器内工作目录固定为 `/import-data`，Backend 地址和文件名规则位于 `config/importer.yml`。Importer 登录 Backend 的账号需要具备候选人简历和候选人资料上传职责；请不要使用个人账号作为生产 Importer 账号。

`IMPORTER_API_KEY` 保护 Importer 的 HTTP 接口。上游请求的 `X-Importer-Api-Key` 必须与该值一致，密钥不会写入业务日志。

## API

### 提交导入批次

```bash
curl -X POST http://127.0.0.1:8010/api/v1/imports \
  -H "X-Importer-Api-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"folders":["张三","候选人002"]}'
```

一次请求至少 1 个、最多 100 个文件夹。接口返回 `202` 和 `requestId`；完全没有接受项时可能返回 `200`，并在 `rejected` 中说明原因。

### 查询批次状态

```bash
curl http://127.0.0.1:8010/api/v1/imports/{requestId} \
  -H "X-Importer-Api-Key: your-api-key"
```

批次状态包括 `queued`、`running`、`completed`、`partial_failed` 和 `failed`；单个文件夹状态包括 `queued`、`processing`、`completed` 和 `failed`。

Importer 单线程依次上传文件夹。已有任务运行时，新请求会排队；同一文件夹正在排队或处理时重复提交会被拒绝。批次状态最多保留最近 200 批，容器重启后旧 `requestId` 可能无法查询。仍保留在工作目录中的失败文件夹可以重新提交，状态文件和 Backend 幂等键会避免重复上传成功内容。

## 日志与排查

```powershell
.\scripts\ops\compose.ps1 logs --follow importer
```

日志为单行 JSON，记录批次、文件夹、文件名、状态和错误码，不记录 API Key、密码或文件内容。错误排查顺序：

1. 检查 `http://127.0.0.1:8010/health`。
2. 检查 `IMPORTER_API_KEY` 是否一致。
3. 检查 `IMPORTER_WORKSPACE_DIR` 对应目录是否已挂载。
4. 检查候选人文件夹是否恰好有一份简历 PDF。
5. 检查 Backend 健康状态和 Importer 账号权限。
6. 查看失败文件夹中的 `.import-state.json` 和 Importer 日志。

Importer 不直接修改数据库。业务数据、对象存储和招聘流程状态均由 Backend 负责。
