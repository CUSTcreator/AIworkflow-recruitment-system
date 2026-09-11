<#
.SYNOPSIS
显示当前开发数据库的 Alembic revision、业务表数量和 Backend 容器来源。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Preflight = & (Join-Path $PSScriptRoot 'preflight.ps1') -RequireDocker
$ProjectRoot = $Preflight.ProjectRoot
$Compose = @('--project-directory', $ProjectRoot, '--env-file', (Join-Path $ProjectRoot '.env'), '-f', (Join-Path $ProjectRoot 'docker-compose.yml'))

Write-Output "项目目录: $ProjectRoot"
Write-Output 'Backend Compose 来源:'
# 不向 docker inspect 传 Go template，避免 Windows PowerShell 5.1 的原生命令
# 参数兼容模式丢失内部引号。结构化 JSON 读取同一标签且不依赖命令行转义。
$BackendContainer = (& docker inspect recruit-system-v10-backend-1 | ConvertFrom-Json)[0]
Write-Output $BackendContainer.Config.Labels.'com.docker.compose.project.working_dir'
Write-Output 'Alembic revision:'
& docker compose @Compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select version_num from alembic_version"'
Write-Output 'public 业务表数量:'
& docker compose @Compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select count(*) from information_schema.tables where table_schema = ''public'' and table_type = ''BASE TABLE''"'
