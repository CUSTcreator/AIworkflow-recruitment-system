param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDir
)

$workspace = Split-Path -Parent $PSScriptRoot
$stdoutLog = Join-Path $workspace ($OutputDir + ".stdout.log")
$stderrLog = Join-Path $workspace ($OutputDir + ".stderr.log")
$process = Start-Process `
    -FilePath "D:\Anaconda3\python.exe" `
    -ArgumentList @(
        "scripts\run_ai_application_full_flow_validation.py",
        "--output-dir",
        $OutputDir
    ) `
    -WorkingDirectory $workspace `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -UseNewEnvironment `
    -PassThru

Write-Output "PID=$($process.Id)"
