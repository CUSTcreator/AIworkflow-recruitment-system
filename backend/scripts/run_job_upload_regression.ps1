<#
岗位上传与管理的后端回归测试统一入口。

覆盖：Excel 解析规则、草稿辅助与状态规则、导入/确认/编辑/删除 API、数据库
原子性与幂等、岗位文档导入 Workflow、岗位画像 Workflow 的检查点恢复。

运行方式（仓库根目录）：
    powershell -ExecutionPolicy Bypass -File backend/scripts/run_job_upload_regression.ps1
#>
param(
    [string]$Python = "python"
)

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$env:PYTHONPATH = "$repositoryRoot\packages\recruitment_ai_core;$repositoryRoot"

& $Python -m pytest `
    "$repositoryRoot\backend\tests\test_job_document_processor.py" `
    "$repositoryRoot\backend\tests\test_job_draft_assistance.py" `
    "$repositoryRoot\backend\tests\test_job_document_ingestion.py" `
    "$repositoryRoot\backend\tests\test_job_deletion.py" `
    "$repositoryRoot\backend\tests\test_job_module_boundaries.py" `
    "$repositoryRoot\backend\tests\test_application_and_job_process_skeletons.py" `
    "$repositoryRoot\backend\tests\test_job_upload_automation.py" `
    "$repositoryRoot\backend\tests\test_job_upload_edge_cases.py" `
    -q

exit $LASTEXITCODE
