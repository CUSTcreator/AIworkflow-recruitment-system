"""Compatibility import; candidate resume parsing is owned by document_ingestion."""

from backend.app.modules.document_ingestion.public import CandidateBasicInfo, resolve_candidate_basic_info

__all__ = ["CandidateBasicInfo", "resolve_candidate_basic_info"]