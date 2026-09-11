from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ApplicationDocumentView(BaseModel):
    documentId: str
    scope: Literal["candidate", "application", "job"]
    displayName: str
    filename: str
    primary: bool = False
    category: Literal[
        "resume",
        "standard_resume",
        "psychological_assessment",
        "academic_transcript",
        "certificate",
        "portfolio",
        "other",
        "job",
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
    deleteImpactCount: int | None = None


class ApplicationDocumentPermissions(BaseModel):
    canUpload: bool = False


class ApplicationDocumentsView(BaseModel):
    applicationId: str
    candidateId: str | None = None
    documents: list[ApplicationDocumentView] = Field(default_factory=list)
    permissions: ApplicationDocumentPermissions


class ApplicationDocumentPatch(BaseModel):
    displayName: str = Field(min_length=1, max_length=128)


class ApplicationDocumentImages(BaseModel):
    pages: list[str] = Field(default_factory=list)

