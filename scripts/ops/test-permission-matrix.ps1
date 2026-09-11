<#
.SYNOPSIS
Run the complete permission-matrix regression suite.

.DESCRIPTION
The suite combines pure authorization combinations, real HTTP endpoints,
resume recovery authorization, and page-action projection checks. It also
fails when a protected route or authorization-catalog action is not registered
in the test matrix.
#>
param(
    [string]$Python = "D:\Anaconda3\python.exe"
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& (Join-Path $PSScriptRoot "preflight.ps1")
$env:PYTHONPATH = "$repositoryRoot\packages\recruitment_ai_core;$repositoryRoot"

$tests = @(
    "$repositoryRoot\backend\tests\auth\test_application_api_permission_matrix.py",
    "$repositoryRoot\backend\tests\auth\test_flow_permission_matrix.py",
    "$repositoryRoot\backend\tests\auth\test_authorization_policy.py",
    "$repositoryRoot\backend\tests\auth\test_resume_recovery_authorization.py"
)
& $Python -m pytest @tests -q

exit $LASTEXITCODE
