<#
.SYNOPSIS
在临时内存 PostgreSQL 中验证当前 Alembic head 的初始化与幂等升级合同。

.DESCRIPTION
此脚本不会引用开发 Compose 文件、5432 端口或 postgres_data 卷。数据库由
docker-compose.migration-contract.yml 中的 tmpfs 提供，容器退出后数据自然消失。
清理阶段刻意只调用 `docker compose down --remove-orphans`，禁止删除任何卷。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Preflight = & (Join-Path $PSScriptRoot 'preflight.ps1') -RequireDocker
$ProjectRoot = $Preflight.ProjectRoot
$ComposeFile = Join-Path $ProjectRoot 'docker-compose.migration-contract.yml'

if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "Migration contract compose file was not found: $ComposeFile"
}

# Keep the test project separate from the development stack and never expose a host port.
$ComposeBase = @(
    'compose',
    '--project-name', 'recruit-migration-contract',
    '--project-directory', $ProjectRoot,
    '-f', $ComposeFile
)
$MigrationExitCode = $null
$CleanupExitCode = 0

try {
    & docker @ComposeBase up --build --abort-on-container-exit --exit-code-from migration
    $MigrationExitCode = $LASTEXITCODE
    if ($MigrationExitCode -ne 0) {
        throw "Migration contract failed; migration container exit code: $MigrationExitCode"
    }
}
finally {
    # Do not add -v/--volumes here: this test must never delete a Docker volume.
    & docker @ComposeBase down --remove-orphans
    $CleanupExitCode = $LASTEXITCODE
}

if ($CleanupExitCode -ne 0) {
    throw "Migration contract containers stopped, but network cleanup failed; exit code: $CleanupExitCode"
}

Write-Host 'Migration contract passed: initialization, repeat upgrade, and core-table checks completed.'
