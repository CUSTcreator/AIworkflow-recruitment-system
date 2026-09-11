"""文档导入工作流步骤计划。"""
from .job_document_import_workflow import build_job_document_import_spec
from .resume_document_import_workflow import build_resume_document_import_spec
__all__=["build_job_document_import_spec","build_resume_document_import_spec"]