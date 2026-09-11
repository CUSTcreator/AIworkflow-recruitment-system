from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateCurrentResumeView(BaseModel):
    submissionId: str
    filename: str
    pdfUrl: str
    uploadedAt: str
    status: str


class CandidateDocumentView(BaseModel):
    documentId: str
    scope: Literal["candidate"]
    displayName: str
    filename: str
    primary: bool = False
    category: Literal[
        "psychological_assessment",
        "academic_transcript",
        "certificate",
        "portfolio",
        "other",
    ]
    sourceStage: str
    note: str | None = None
    status: str
    imagesUrl: str
    canRename: bool = False
    canDelete: bool = False
    uploadedAt: str
    uploadedBy: str
    uploadedByName: str


class CandidateDocumentPermissions(BaseModel):
    canUpload: bool = False


class CandidateDocumentsView(BaseModel):
    candidateId: str
    activeApplicationCount: int = 0
    currentResume: CandidateCurrentResumeView | None = None
    documents: list[CandidateDocumentView] = Field(default_factory=list)
    permissions: CandidateDocumentPermissions


class CandidateDocumentPatch(BaseModel):
    displayName: str = Field(min_length=1, max_length=128)


class CandidateDocumentImages(BaseModel):
    pages: list[str] = Field(default_factory=list)

