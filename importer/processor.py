from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from importer.config import ImporterConfig
from importer.event_log import log_event


UPLOADED_DIR_NAME = "已上传"
STATE_FILE_NAME = ".import-state.json"


class FolderValidationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class BackendClient(Protocol):
    def upload_resume(self, path: Path, idempotency_key: str) -> dict[str, Any]: ...

    def upload_candidate_document(
        self, *, candidate_id: str, path: Path, display_name: str,
        category: str, idempotency_key: str,
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class FolderImportResult:
    folder: str
    status: str
    candidate_id: str | None = None
    uploaded_file_count: int = 0
    ignored_file_count: int = 0
    archived_path: str | None = None
    error_code: str | None = None
    error: str | None = None


class CandidateFolderProcessor:
    def __init__(self, config: ImporterConfig, backend: BackendClient) -> None:
        self.config = config
        self.backend = backend
        self.uploaded_dir = config.workspace_dir / UPLOADED_DIR_NAME
        self.uploaded_dir.mkdir(parents=True, exist_ok=True)

    def candidate_folders(self) -> list[Path]:
        return sorted(
            (
                path for path in self.config.workspace_dir.iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and path.name != UPLOADED_DIR_NAME
                and not path.name.startswith(".")
            ),
            key=lambda path: path.name.casefold(),
        )

    def process_all(self, folders: list[Path] | None = None) -> list[FolderImportResult]:
        return [
            self.process_folder(folder)
            for folder in (folders or self.candidate_folders())
        ]

    def validate_folder(self, folder_name: str) -> Path:
        if not folder_name:
            raise FolderValidationError("folder_name_required", "文件夹名称不能为空")
        if len(folder_name) > 255:
            raise FolderValidationError(
                "folder_name_too_long",
                "文件夹名称不能超过 255 个字符",
            )
        if folder_name in {".", ".."} or "/" in folder_name or "\\" in folder_name:
            raise FolderValidationError(
                "folder_name_invalid",
                "只允许传入工作目录的直接子文件夹名称",
            )
        if folder_name == UPLOADED_DIR_NAME or folder_name.startswith("."):
            raise FolderValidationError("folder_reserved", "该文件夹名称由 Importer 保留")

        folder = self.config.workspace_dir / folder_name
        if not folder.exists():
            raise FolderValidationError("folder_not_found", "候选人文件夹不存在")
        if folder.is_symlink():
            raise FolderValidationError(
                "folder_symlink_not_allowed",
                "不允许导入符号链接文件夹",
            )
        if not folder.is_dir():
            raise FolderValidationError("folder_not_directory", "指定名称不是文件夹")
        try:
            pdf_files, _ = self._inspect_files(folder)
            self._find_resume(pdf_files)
        except FolderValidationError as error:
            self._record_validation_failure(folder, error)
            raise
        return folder

    def process_folder(
        self,
        folder: Path,
        *,
        request_id: str | None = None,
    ) -> FolderImportResult:
        state: dict[str, Any] = {}
        try:
            state = self._read_state(folder)
            if state.get("status") == "completed":
                archived = self._archive(folder)
                log_event(
                    logging.INFO,
                    "folder_archived_from_completed_state",
                    requestId=request_id,
                    folder=folder.name,
                    archivedPath=str(archived),
                )
                return FolderImportResult(
                    folder=folder.name,
                    status="completed",
                    candidate_id=state.get("candidateId"),
                    uploaded_file_count=1 + len(state.get("uploadedFiles") or {}),
                    archived_path=str(archived),
                )

            pdf_files, ignored_count = self._inspect_files(folder)
            resume = self._find_resume(pdf_files)
            attachments = [path for path in pdf_files if path != resume]
            log_event(
                logging.INFO,
                "folder_inspected",
                requestId=request_id,
                folder=folder.name,
                resumeFile=resume.name,
                attachmentCount=len(attachments),
                ignoredFileCount=ignored_count,
            )
            resume_sha = self._sha256(resume)

            previous_resume_sha = state.get("resumeSha256")
            if previous_resume_sha and previous_resume_sha != resume_sha:
                raise RuntimeError("简历已上传后发生变化，请人工确认该候选人文件夹")

            import_run_id = str(state.get("importRunId") or uuid4().hex)
            state.update(
                {
                    "version": 1,
                    "importRunId": import_run_id,
                    "folderName": folder.name,
                    "status": "processing",
                    "resumeFilename": resume.name,
                    "resumeSha256": resume_sha,
                    "updatedAt": self._now(),
                    "lastErrorCode": None,
                    "lastError": None,
                }
            )
            state.setdefault("uploadedFiles", {})
            self._write_state(folder, state)

            candidate_id = str(state.get("candidateId") or "")
            if not candidate_id:
                log_event(
                    logging.INFO,
                    "resume_upload_started",
                    requestId=request_id,
                    folder=folder.name,
                    file=resume.name,
                )
                response = self.backend.upload_resume(
                    resume,
                    self._idempotency_key(
                        "resume", import_run_id, resume.name, resume_sha
                    ),
                )
                candidate_id = str(response.get("candidate_id") or "")
                if not candidate_id:
                    raise RuntimeError("简历上传响应缺少 candidate_id")
                state["candidateId"] = candidate_id
                state["resumeSubmissionId"] = response.get("submission_id")
                state["resumeWorkflowRunId"] = response.get("workflow_run_id")
                state["updatedAt"] = self._now()
                self._write_state(folder, state)
                log_event(
                    logging.INFO,
                    "resume_upload_completed",
                    requestId=request_id,
                    folder=folder.name,
                    file=resume.name,
                    candidateId=candidate_id,
                    resumeSubmissionId=state["resumeSubmissionId"],
                )
            else:
                log_event(
                    logging.INFO,
                    "resume_upload_skipped",
                    requestId=request_id,
                    folder=folder.name,
                    file=resume.name,
                    candidateId=candidate_id,
                    reason="saved_state",
                )

            uploaded_files: dict[str, Any] = state["uploadedFiles"]
            for attachment in attachments:
                file_sha = self._sha256(attachment)
                prior = uploaded_files.get(attachment.name)
                if isinstance(prior, dict) and prior.get("sha256") == file_sha:
                    log_event(
                        logging.INFO,
                        "document_upload_skipped",
                        requestId=request_id,
                        folder=folder.name,
                        file=attachment.name,
                        candidateId=candidate_id,
                        reason="saved_state",
                    )
                    continue
                category = self._category(attachment.name)
                log_event(
                    logging.INFO,
                    "document_upload_started",
                    requestId=request_id,
                    folder=folder.name,
                    file=attachment.name,
                    candidateId=candidate_id,
                    category=category,
                )
                response = self.backend.upload_candidate_document(
                    candidate_id=candidate_id,
                    path=attachment,
                    display_name=attachment.stem,
                    category=category,
                    idempotency_key=self._idempotency_key(
                        "document",
                        import_run_id,
                        candidate_id,
                        attachment.name,
                        file_sha,
                    ),
                )
                uploaded_files[attachment.name] = {
                    "sha256": file_sha,
                    "documentId": response.get("documentId"),
                    "category": category,
                }
                state["updatedAt"] = self._now()
                self._write_state(folder, state)
                log_event(
                    logging.INFO,
                    "document_upload_completed",
                    requestId=request_id,
                    folder=folder.name,
                    file=attachment.name,
                    candidateId=candidate_id,
                    category=category,
                    documentId=response.get("documentId"),
                )

            state["status"] = "completed"
            state["completedAt"] = self._now()
            state["updatedAt"] = state["completedAt"]
            self._write_state(folder, state)
            archived = self._archive(folder)
            log_event(
                logging.INFO,
                "folder_archived",
                requestId=request_id,
                folder=folder.name,
                candidateId=candidate_id,
                uploadedFileCount=1 + len(uploaded_files),
                ignoredFileCount=ignored_count,
                archivedPath=str(archived),
            )
            return FolderImportResult(
                folder=folder.name,
                status="completed",
                candidate_id=candidate_id,
                uploaded_file_count=1 + len(uploaded_files),
                ignored_file_count=ignored_count,
                archived_path=str(archived),
            )
        except Exception as error:
            error_code = (
                error.code
                if isinstance(error, FolderValidationError)
                else "import_failed"
            )
            log_event(
                logging.ERROR,
                "folder_processing_error",
                requestId=request_id,
                folder=folder.name,
                candidateId=state.get("candidateId"),
                errorCode=error_code,
                error=str(error)[:2000],
            )
            if not folder.is_dir():
                return FolderImportResult(
                    folder=folder.name,
                    status="failed",
                    error_code=error_code,
                    error=str(error),
                )
            state.update(
                {
                    "version": 1,
                    "folderName": folder.name,
                    "status": "failed",
                    "lastErrorCode": error_code,
                    "lastError": str(error)[:2000],
                    "updatedAt": self._now(),
                }
            )
            self._write_state(folder, state)
            return FolderImportResult(
                folder=folder.name,
                status="failed",
                candidate_id=state.get("candidateId"),
                uploaded_file_count=(1 if state.get("candidateId") else 0)
                + len(state.get("uploadedFiles") or {}),
                error_code=error_code,
                error=str(error),
            )

    def _inspect_files(self, folder: Path) -> tuple[list[Path], int]:
        files = sorted(
            (path for path in folder.iterdir() if path.is_file() and path.name != STATE_FILE_NAME),
            key=lambda path: path.name.casefold(),
        )
        pdf_files = [path for path in files if path.suffix.casefold() == ".pdf"]
        if not pdf_files:
            raise FolderValidationError("pdf_not_found", "候选人文件夹中没有 PDF 文件")
        for path in pdf_files:
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise FolderValidationError(
                        "pdf_invalid",
                        f"文件不是有效 PDF：{path.name}",
                    )
        return pdf_files, len(files) - len(pdf_files)

    def _find_resume(self, pdf_files: list[Path]) -> Path:
        matches = [
            path for path in pdf_files
            if self._matches(path.name, self.config.resume_patterns)
        ]
        if not matches:
            raise FolderValidationError(
                "resume_not_found",
                "没有找到符合配置规则的简历文件",
            )
        if len(matches) > 1:
            raise FolderValidationError(
                "multiple_resumes",
                "匹配到多份简历：" + "、".join(path.name for path in matches),
            )
        return matches[0]

    def _category(self, filename: str) -> str:
        for category, patterns in self.config.category_patterns.items():
            if self._matches(filename, patterns):
                return category
        return "other"

    @staticmethod
    def _matches(filename: str, patterns: tuple[str, ...]) -> bool:
        normalized = filename.casefold()
        return any(
            fnmatch.fnmatchcase(normalized, pattern.casefold())
            for pattern in patterns
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _idempotency_key(action: str, *parts: str) -> str:
        identity = "\0".join((action, *parts)).encode("utf-8")
        return f"importer:{action}:{hashlib.sha256(identity).hexdigest()}"

    def _archive(self, folder: Path) -> Path:
        target = self.uploaded_dir / folder.name
        if target.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            target = self.uploaded_dir / f"{folder.name}-{stamp}"
        return Path(shutil.move(str(folder), str(target)))

    def _record_validation_failure(
        self,
        folder: Path,
        error: FolderValidationError,
    ) -> None:
        try:
            state = self._read_state(folder)
        except RuntimeError:
            state = {}
        state.update(
            {
                "version": 1,
                "folderName": folder.name,
                "status": "failed",
                "lastErrorCode": error.code,
                "lastError": str(error)[:2000],
                "updatedAt": self._now(),
            }
        )
        self._write_state(folder, state)

    @staticmethod
    def _read_state(folder: Path) -> dict[str, Any]:
        path = folder / STATE_FILE_NAME
        if not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("导入状态文件损坏，请人工检查") from error
        if not isinstance(value, dict):
            raise RuntimeError("导入状态文件格式无效")
        return value

    @staticmethod
    def _write_state(folder: Path, state: dict[str, Any]) -> None:
        path = folder / STATE_FILE_NAME
        temporary = folder / f"{STATE_FILE_NAME}.tmp"
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()


def result_dict(result: FolderImportResult) -> dict[str, Any]:
    return asdict(result)
