<#
.SYNOPSIS
只读检查开发数据库中已废弃的历史权限。

.DESCRIPTION
此脚本只执行 SELECT/聚合查询，不修改数据库。它必须通过真实项目的 Compose
配置连接开发库，供执行权限目录迁移前后确认影响范围。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Preflight = & (Join-Path $PSScriptRoot 'preflight.ps1') -RequireDocker
$ProjectRoot = $Preflight.ProjectRoot
$Compose = @(
    '--project-directory', $ProjectRoot,
    '--env-file', (Join-Path $ProjectRoot '.env'),
    '-f', (Join-Path $ProjectRoot 'docker-compose.yml')
)
$RoleQuery = @'
SELECT role_id, business_scope,
       COALESCE(string_agg(key, ',' ORDER BY key), '') AS legacy_permissions
FROM role_definitions,
     LATERAL jsonb_object_keys(COALESCE(permissions::jsonb, '{}'::jsonb)) AS permission_keys(key)
WHERE key IN ('analytics.view', 'candidate.advance', 'candidate.reject',
              'hard_screening.manage', 'candidate.view')
GROUP BY role_id, business_scope
ORDER BY role_id;
'@
$OverrideQuery = @'
SELECT account.user_id, account.business_scope, override.permission_code, override.effect
FROM user_permission_overrides AS override
JOIN users AS account ON account.user_id = override.user_id
WHERE override.permission_code IN ('analytics.view', 'candidate.advance',
                                   'candidate.reject', 'hard_screening.manage',
                                   'candidate.view')
ORDER BY account.user_id, override.permission_code;
'@

Write-Output '角色中的待清理历史权限：'
& docker compose @Compose exec -T postgres sh -lc "psql -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -P pager=off -F ' | ' -Atc `"$($RoleQuery.Trim())`""
if ($LASTEXITCODE -ne 0) { throw "角色权限只读查询失败，退出码：$LASTEXITCODE" }

Write-Output '账号例外中的待清理历史权限：'
& docker compose @Compose exec -T postgres sh -lc "psql -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -P pager=off -F ' | ' -Atc `"$($OverrideQuery.Trim())`""
if ($LASTEXITCODE -ne 0) { throw "账号权限覆盖只读查询失败，退出码：$LASTEXITCODE" }
