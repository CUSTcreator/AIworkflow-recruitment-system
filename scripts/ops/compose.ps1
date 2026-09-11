<#
.SYNOPSIS
Run Docker Compose from the canonical project directory.

.DESCRIPTION
Always bind the Compose file, .env and project directory to the canonical source
tree, even when invoked from a Codex worktree. `down -v` is intentionally blocked
because it deletes PostgreSQL/MinIO volumes.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, ValueFromRemainingArguments)]
    [string[]]$ComposeArgs
)

$ErrorActionPreference = 'Stop'
$Preflight = & (Join-Path $PSScriptRoot 'preflight.ps1') -RequireDocker
$ProjectRoot = $Preflight.ProjectRoot

if ($ComposeArgs -contains 'down' -and ($ComposeArgs -contains '-v' -or $ComposeArgs -contains '--volumes')) {
    throw 'Refusing docker compose down -v because it deletes PostgreSQL/MinIO volumes.'
}

& docker compose `
    --project-directory $ProjectRoot `
    --env-file (Join-Path $ProjectRoot '.env') `
    -f (Join-Path $ProjectRoot 'docker-compose.yml') `
    @ComposeArgs
exit $LASTEXITCODE
