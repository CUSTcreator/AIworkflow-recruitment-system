<#
.SYNOPSIS
在当前 D 盘 Compose 项目的 Backend 容器内执行向前 Alembic 迁移。

.DESCRIPTION
迁移只允许 upgrade head，不提供 downgrade。容器的 Compose 来源目录必须与本脚本
推导出的真实项目目录一致，防止旧容器或工作树构建物修改开发数据库。
#>
[CmdletBinding()]
param(
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
if (-not $Confirm) {
    throw 'This migration changes the development database. Re-run with -Confirm.'
}

$Preflight = & (Join-Path $PSScriptRoot 'preflight.ps1') -RequireDocker
$ProjectRoot = $Preflight.ProjectRoot
$Container = 'recruit-system-v10-backend-1'
$BackendContainer = (& docker inspect $Container | ConvertFrom-Json)[0]
$ComposeRoot = [string]$BackendContainer.Config.Labels.'com.docker.compose.project.working_dir'
if ($LASTEXITCODE -ne 0 -or $ComposeRoot -ne $ProjectRoot) {
    throw "Migration refused: backend container source is '$ComposeRoot'; expected '$ProjectRoot'."
}

& docker compose --project-directory $ProjectRoot --env-file (Join-Path $ProjectRoot '.env') -f (Join-Path $ProjectRoot 'docker-compose.yml') exec -T backend python -m alembic -c backend/alembic.ini upgrade head
exit $LASTEXITCODE
