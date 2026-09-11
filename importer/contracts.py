from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FolderStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class ImportBatchRequest(BaseModel):
    folders: list[str] = Field(min_length=1, max_length=100)


class AcceptedFolderView(BaseModel):
    folder: str
    status: FolderStatus = FolderStatus.QUEUED


class RejectedFolderView(BaseModel):
    folder: str
    code: str
    message: str


class ImportBatchAcceptedView(BaseModel):
    requestId: str
    status: BatchStatus
    accepted: list[AcceptedFolderView] = Field(default_factory=list)
    rejected: list[RejectedFolderView] = Field(default_factory=list)


class ImportFolderResultView(BaseModel):
    folder: str
    status: FolderStatus
    candidateId: str | None = None
    uploadedFileCount: int = 0
    ignoredFileCount: int = 0
    archivedPath: str | None = None
    errorCode: str | None = None
    error: str | None = None


class ImportBatchSummaryView(BaseModel):
    requested: int
    accepted: int
    rejected: int
    queued: int
    processing: int
    completed: int
    failed: int


class ImportBatchStatusView(BaseModel):
    requestId: str
    status: BatchStatus
    submittedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    summary: ImportBatchSummaryView
    results: list[ImportFolderResultView] = Field(default_factory=list)
    rejected: list[RejectedFolderView] = Field(default_factory=list)
