<#
.SYNOPSIS
  对后端事务边界、状态写入、任务入队与外部副作用执行可重复静态扫描。

.DESCRIPTION
  只读扫描 backend/app；不会修改源码或数据库。输出 Markdown，供
  docs/architecture/transaction-boundary-audit.md 的人工判定复核。
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\\..')).Path,
    [string]$OutputPath = ''
)

$backendApp = Join-Path $ProjectRoot 'backend/app'
if (-not (Test-Path -LiteralPath $backendApp)) {
    throw "未找到审计范围：$backendApp"
}

$rules = @(
    @{ Title = '1. 事务边界：commit / rollback / flush'; Pattern = '\.(commit\(|rollback\(|flush\()' },
    @{ Title = '2. 领域状态直接写入'; Pattern = '\b(application|app)\.status\s*=|\bjob\.status\s*=|\bsubmission\.status\s*=|\bcandidate\.status\s*=' },
    @{ Title = '3. Workflow 入队'; Pattern = '(self\.)?queue\.enqueue\(|WorkflowQueue\([^\)]*\)\.enqueue\(' },
    @{ Title = '4. 对象存储 / 外部活动 / after_commit'; Pattern = 'delete_object\(|put_json\(|put_bytes\(|materialize\(|read_json\(|ExternalActivity\(|after_commit\(' },
    @{ Title = '5. 旧式服务提交调用'; Pattern = 'commit=False|commit=True' }
)

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add('# 事务边界静态扫描快照')
$lines.Add('')
$lines.Add("- 生成时间：$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss K'))")
$lines.Add('- 扫描范围：`backend/app`')
$lines.Add('- 执行方式：`backend/scripts/audit_transaction_boundaries.ps1`')
$lines.Add('- 说明：结果是候选点清单；是否违规以人工审计报告为准。')

foreach ($rule in $rules) {
    $lines.Add('')
    $lines.Add("## $($rule.Title)")
    $lines.Add('')
    $lines.Add('```text')
    $output = & rg -n --glob '*.py' $rule.Pattern $backendApp 2>&1
    if ($LASTEXITCODE -eq 1) {
        $lines.Add('无命中。')
    } elseif ($LASTEXITCODE -ne 0) {
        throw "rg 扫描失败：$($rule.Title)`n$output"
    } else {
        foreach ($line in $output) { $lines.Add([string]$line) }
    }
    $lines.Add('```')
}

$markdown = $lines -join [Environment]::NewLine
if ($OutputPath) {
    $directory = Split-Path -Parent $OutputPath
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory | Out-Null
    }
    [System.IO.File]::WriteAllText($OutputPath, $markdown, [System.Text.UTF8Encoding]::new($false))
} else {
    $markdown
}