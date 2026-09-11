from __future__ import annotations

import threading
import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from importer.contracts import (
    AcceptedFolderView,
    BatchStatus,
    FolderStatus,
    ImportBatchAcceptedView,
    ImportBatchStatusView,
    ImportBatchSummaryView,
    ImportFolderResultView,
    RejectedFolderView,
)
from importer.event_log import log_event
from importer.processor import (
    CandidateFolderProcessor,
    FolderImportResult,
    FolderValidationError,
)


MAX_RETAINED_BATCHES = 200


@dataclass(slots=True)
class _FolderRecord:
    folder: str
    path: Path
    status: FolderStatus = FolderStatus.QUEUED
    candidate_id: str | None = None
    uploaded_file_count: int = 0
    ignored_file_count: int = 0
    archived_path: str | None = None
    error_code: str | None = None
    error: str | None = None


@dataclass(slots=True)
class _BatchRecord:
    request_id: str
    status: BatchStatus
    submitted_at: str
    folders: list[_FolderRecord] = field(default_factory=list)
    rejected: list[RejectedFolderView] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None


class ImportManager:
    def __init__(self, processor: CandidateFolderProcessor) -> None:
        self.processor = processor
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="candidate-import",
        )
        self._lock = threading.RLock()
        self._batches: OrderedDict[str, _BatchRecord] = OrderedDict()
        self._active_folders: set[str] = set()

    def submit(self, folder_names: list[str]) -> ImportBatchAcceptedView:
        with self._lock:
            request_id = uuid4().hex
            accepted: list[_FolderRecord] = []
            rejected: list[RejectedFolderView] = []
            seen: set[str] = set()

            for raw_name in folder_names:
                name = raw_name.strip()
                identity = name.casefold()
                if identity in seen:
                    rejection = self._rejection(
                        raw_name,
                        "duplicate_in_request",
                        "同一请求中不能重复提交文件夹",
                    )
                    rejected.append(rejection)
                    self._log_rejection(request_id, rejection)
                    continue
                seen.add(identity)
                if identity in self._active_folders:
                    rejection = self._rejection(
                        name,
                        "folder_already_queued",
                        "文件夹正在排队或处理中",
                    )
                    rejected.append(rejection)
                    self._log_rejection(request_id, rejection)
                    continue
                try:
                    path = self.processor.validate_folder(name)
                except FolderValidationError as error:
                    rejection = self._rejection(
                        name or raw_name,
                        error.code,
                        str(error),
                    )
                    rejected.append(rejection)
                    self._log_rejection(request_id, rejection)
                    continue
                except Exception as error:
                    rejection = self._rejection(
                        name or raw_name,
                        "folder_validation_failed",
                        str(error)[:2000],
                    )
                    rejected.append(rejection)
                    self._log_rejection(request_id, rejection)
                    continue
                accepted.append(_FolderRecord(folder=name, path=path))
                self._active_folders.add(identity)

            status = BatchStatus.QUEUED if accepted else BatchStatus.FAILED
            batch = _BatchRecord(
                request_id=request_id,
                status=status,
                submitted_at=self._now(),
                folders=accepted,
                rejected=rejected,
                finished_at=None if accepted else self._now(),
            )
            self._batches[request_id] = batch
            self._prune_batches()
            log_event(
                logging.INFO,
                "batch_submitted",
                requestId=request_id,
                requested=len(folder_names),
                accepted=len(accepted),
                rejected=len(rejected),
                status=batch.status.value,
            )
            if accepted:
                self._executor.submit(self._run_batch, request_id)
            return ImportBatchAcceptedView(
                requestId=request_id,
                status=batch.status,
                accepted=[AcceptedFolderView(folder=item.folder) for item in accepted],
                rejected=list(rejected),
            )

    def status(self, request_id: str) -> ImportBatchStatusView | None:
        with self._lock:
            batch = self._batches.get(request_id)
            return self._status_view(batch) if batch is not None else None

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_batch(self, request_id: str) -> None:
        with self._lock:
            batch = self._batches[request_id]
            batch.status = BatchStatus.RUNNING
            batch.started_at = self._now()
            log_event(
                logging.INFO,
                "batch_started",
                requestId=request_id,
                folderCount=len(batch.folders),
            )

        for folder in batch.folders:
            with self._lock:
                folder.status = FolderStatus.PROCESSING
            log_event(
                logging.INFO,
                "folder_started",
                requestId=request_id,
                folder=folder.folder,
            )
            try:
                result = self.processor.process_folder(
                    folder.path,
                    request_id=request_id,
                )
            except Exception as error:
                result = FolderImportResult(
                    folder=folder.folder,
                    status=FolderStatus.FAILED.value,
                    error_code="import_internal_error",
                    error=str(error)[:2000],
                )
            with self._lock:
                self._apply_result(folder, result)
                self._active_folders.discard(folder.folder.casefold())
                log_event(
                    logging.INFO if folder.status == FolderStatus.COMPLETED else logging.ERROR,
                    "folder_completed" if folder.status == FolderStatus.COMPLETED else "folder_failed",
                    requestId=request_id,
                    folder=folder.folder,
                    status=folder.status.value,
                    candidateId=folder.candidate_id,
                    uploadedFileCount=folder.uploaded_file_count,
                    ignoredFileCount=folder.ignored_file_count,
                    errorCode=folder.error_code,
                    error=folder.error,
                )

        with self._lock:
            batch.finished_at = self._now()
            completed = sum(item.status == FolderStatus.COMPLETED for item in batch.folders)
            failures = (
                sum(item.status == FolderStatus.FAILED for item in batch.folders)
                + len(batch.rejected)
            )
            if completed and failures:
                batch.status = BatchStatus.PARTIAL_FAILED
            elif failures:
                batch.status = BatchStatus.FAILED
            else:
                batch.status = BatchStatus.COMPLETED
            log_event(
                logging.INFO if batch.status == BatchStatus.COMPLETED else logging.WARNING,
                "batch_completed",
                requestId=request_id,
                status=batch.status.value,
                accepted=len(batch.folders),
                rejected=len(batch.rejected),
                completed=completed,
                failed=failures,
            )

    @staticmethod
    def _apply_result(folder: _FolderRecord, result: FolderImportResult) -> None:
        folder.status = FolderStatus(result.status)
        folder.candidate_id = result.candidate_id
        folder.uploaded_file_count = result.uploaded_file_count
        folder.ignored_file_count = result.ignored_file_count
        folder.archived_path = result.archived_path
        folder.error_code = result.error_code
        folder.error = result.error

    @staticmethod
    def _rejection(folder: str, code: str, message: str) -> RejectedFolderView:
        return RejectedFolderView(folder=folder, code=code, message=message)

    @staticmethod
    def _log_rejection(request_id: str, rejection: RejectedFolderView) -> None:
        log_event(
            logging.WARNING,
            "folder_rejected",
            requestId=request_id,
            folder=rejection.folder,
            errorCode=rejection.code,
            error=rejection.message,
        )

    def _status_view(self, batch: _BatchRecord) -> ImportBatchStatusView:
        counts = {status: 0 for status in FolderStatus}
        for item in batch.folders:
            counts[item.status] += 1
        return ImportBatchStatusView(
            requestId=batch.request_id,
            status=batch.status,
            submittedAt=batch.submitted_at,
            startedAt=batch.started_at,
            finishedAt=batch.finished_at,
            summary=ImportBatchSummaryView(
                requested=len(batch.folders) + len(batch.rejected),
                accepted=len(batch.folders),
                rejected=len(batch.rejected),
                queued=counts[FolderStatus.QUEUED],
                processing=counts[FolderStatus.PROCESSING],
                completed=counts[FolderStatus.COMPLETED],
                failed=counts[FolderStatus.FAILED],
            ),
            results=[
                ImportFolderResultView(
                    folder=item.folder,
                    status=item.status,
                    candidateId=item.candidate_id,
                    uploadedFileCount=item.uploaded_file_count,
                    ignoredFileCount=item.ignored_file_count,
                    archivedPath=item.archived_path,
                    errorCode=item.error_code,
                    error=item.error,
                )
                for item in batch.folders
            ],
            rejected=list(batch.rejected),
        )

    def _prune_batches(self) -> None:
        if len(self._batches) <= MAX_RETAINED_BATCHES:
            return
        for request_id, batch in list(self._batches.items()):
            if len(self._batches) <= MAX_RETAINED_BATCHES:
                break
            if batch.finished_at is not None:
                self._batches.pop(request_id, None)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()
