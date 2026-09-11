<#
.SYNOPSIS
Validate the canonical project directory and required local configuration.

.DESCRIPTION
Derive the project root from this script so Docker builds and migrations cannot
accidentally use a Codex worktree. Other ops scripts call this check first.
#>
[CmdletBinding()]
param(
    [switch]$RequireDocker
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$GitDirectory = Join-Path $ProjectRoot '.git'
$ComposeFile = Join-Path $ProjectRoot 'docker-compose.yml'
$EnvFile = Join-Path $ProjectRoot '.env'

if (-not (Test-Path -LiteralPath $GitDirectory -PathType Container)) {
    throw "Refusing to run: $ProjectRoot is not the canonical project directory (.git must be a directory)."
}
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) {
    throw "Refusing to run: docker-compose.yml was not found at $ProjectRoot."
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Refusing to run: .env is missing from the canonical project directory."
}
if ($ProjectRoot -match '[\\/]\.codex[\\/]worktrees[\\/]') {
    throw "Refusing to run: the project root cannot be a Codex worktree: $ProjectRoot"
}

if ($RequireDocker) {
    & docker version --format '{{.Server.Version}}' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Refusing to run: Docker Desktop is not running or is inaccessible.'
    }
}

[pscustomobject]@{
    ProjectRoot = $ProjectRoot
    ComposeFile = $ComposeFile
    EnvFile     = $EnvFile
}
