"""Document ingestion 对外稳定入口：跨模块调用必须经由此处的显式惰性导出。"""
from __future__ import annotations

from importlib import import_module

_LAZY_EXPORTS = {
    "JobDocumentService": ("backend.app.modules.document_ingestion.services", "JobDocumentService"),
    "ResumeDocumentService": ("backend.app.modules.document_ingestion.services", "ResumeDocumentService"),
    "CandidateBasicInfo": ("backend.app.modules.document_ingestion.readModel.candidate_intake_read_model", "CandidateBasicInfo"),
    "resolve_candidate_basic_info": ("backend.app.modules.document_ingestion.readModel.candidate_intake_read_model", "resolve_candidate_basic_info"),
    "ResumeProfileSchema": ("backend.app.modules.document_ingestion.contracts.resume_intake_contracts", "ResumeProfileSchema"),
}

def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = list(_LAZY_EXPORTS)