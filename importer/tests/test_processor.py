from __future__ import annotations

import json
from pathlib import Path

from importer.config import ImporterConfig
from importer.processor import (
    CandidateFolderProcessor,
    FolderValidationError,
    STATE_FILE_NAME,
)


class FakeBackend:
    def __init__(self, *, fail_first_document: bool = False) -> None:
        self.resume_calls: list[str] = []
        self.document_calls: list[tuple[str, str]] = []
        self.resume_idempotency_keys: list[str] = []
        self.document_idempotency_keys: list[str] = []
        self.fail_first_document = fail_first_document

    def close(self) -> None:
        pass

    def upload_resume(self, path: Path, idempotency_key: str) -> dict:
        self.resume_calls.append(path.name)
        self.resume_idempotency_keys.append(idempotency_key)
        return {
            "candidate_id": "CAND_IMPORTED",
            "submission_id": "RSUB_IMPORTED",
            "workflow_run_id": "WF_IMPORTED",
        }

    def upload_candidate_document(
        self, *, candidate_id: str, path: Path, display_name: str,
        category: str, idempotency_key: str,
    ) -> dict:
        self.document_calls.append((path.name, category))
        self.document_idempotency_keys.append(idempotency_key)
        if self.fail_first_document:
            self.fail_first_document = False
            raise RuntimeError("temporary backend error")
        return {"documentId": f"CDL_{len(self.document_calls)}"}


def _config(workspace: Path) -> ImporterConfig:
    return ImporterConfig(
        workspace_dir=workspace,
        backend_base_url="http://backend/api/v1",
        resume_patterns=("*简历*.pdf",),
        category_patterns={
            "academic_transcript": ("*成绩单*.pdf",),
            "certificate": ("*证书*.pdf",),
        },
    )


def _pdf(path: Path, marker: str) -> None:
    path.write_bytes(b"%PDF-1.4\n" + marker.encode("utf-8"))


def test_imports_resume_and_documents_then_archives_folder(tmp_path: Path) -> None:
    folder = tmp_path / "张三"
    folder.mkdir()
    _pdf(folder / "张三简历.pdf", "resume")
    _pdf(folder / "本科成绩单.pdf", "transcript")
    _pdf(folder / "职业资格证书.pdf", "certificate")
    (folder / "说明.txt").write_text("ignored", encoding="utf-8")
    backend = FakeBackend()

    result = CandidateFolderProcessor(_config(tmp_path), backend).process_folder(folder)

    assert result.status == "completed"
    assert result.candidate_id == "CAND_IMPORTED"
    assert result.uploaded_file_count == 3
    assert result.ignored_file_count == 1
    assert backend.resume_calls == ["张三简历.pdf"]
    assert backend.document_calls == [
        ("本科成绩单.pdf", "academic_transcript"),
        ("职业资格证书.pdf", "certificate"),
    ]
    archived = Path(result.archived_path or "")
    assert archived.parent.name == "已上传"
    assert (archived / STATE_FILE_NAME).is_file()
    assert not folder.exists()


def test_retry_uses_saved_candidate_and_only_retries_pending_document(tmp_path: Path) -> None:
    folder = tmp_path / "李四"
    folder.mkdir()
    _pdf(folder / "李四简历.pdf", "resume")
    _pdf(folder / "补充材料.pdf", "other")
    backend = FakeBackend(fail_first_document=True)
    processor = CandidateFolderProcessor(_config(tmp_path), backend)

    failed = processor.process_folder(folder)
    completed = processor.process_folder(folder)

    assert failed.status == "failed"
    assert failed.candidate_id == "CAND_IMPORTED"
    assert completed.status == "completed"
    assert backend.resume_calls == ["李四简历.pdf"]
    assert backend.document_calls == [
        ("补充材料.pdf", "other"),
        ("补充材料.pdf", "other"),
    ]
    assert backend.document_idempotency_keys[0] == backend.document_idempotency_keys[1]


def test_removing_completed_state_starts_a_new_logical_import(tmp_path: Path) -> None:
    folder = tmp_path / "王五"
    folder.mkdir()
    _pdf(folder / "王五简历.pdf", "resume")
    backend = FakeBackend()
    processor = CandidateFolderProcessor(_config(tmp_path), backend)

    first = processor.process_folder(folder)
    first_archived = Path(first.archived_path or "")
    first_state = json.loads(
        (first_archived / STATE_FILE_NAME).read_text(encoding="utf-8")
    )
    (first_archived / STATE_FILE_NAME).unlink()
    first_archived.rename(folder)

    second = processor.process_folder(folder)
    second_archived = Path(second.archived_path or "")
    second_state = json.loads(
        (second_archived / STATE_FILE_NAME).read_text(encoding="utf-8")
    )

    assert first_state["importRunId"] != second_state["importRunId"]
    assert len(backend.resume_idempotency_keys) == 2
    assert backend.resume_idempotency_keys[0] != backend.resume_idempotency_keys[1]


def test_validate_folder_rejects_unsafe_and_incomplete_folders(tmp_path: Path) -> None:
    no_resume = tmp_path / "无简历"
    no_resume.mkdir()
    _pdf(no_resume / "材料.pdf", "document")
    processor = CandidateFolderProcessor(_config(tmp_path), FakeBackend())

    cases = [
        ("../outside", "folder_name_invalid"),
        ("不存在", "folder_not_found"),
        ("无简历", "resume_not_found"),
        ("已上传", "folder_reserved"),
    ]
    for folder_name, expected_code in cases:
        try:
            processor.validate_folder(folder_name)
        except FolderValidationError as error:
            assert error.code == expected_code
        else:
            raise AssertionError(f"expected validation error for {folder_name}")

    state = json.loads((no_resume / STATE_FILE_NAME).read_text(encoding="utf-8"))
    assert state["status"] == "failed"
    assert state["lastErrorCode"] == "resume_not_found"
