$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $projectRoot "frontend")
npm.cmd run dev -- --host 127.0.0.1
