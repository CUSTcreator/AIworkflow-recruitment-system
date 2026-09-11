from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from importer.app import create_app
from importer.backend_client import RecruitmentBackendClient
from importer.tests.test_processor import FakeBackend, _config, _pdf


def _wait_for_batch(client: TestClient, request_id: str, headers: dict[str, str]) -> dict:
    deadline = time.monotonic() + 5
    body: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/imports/{request_id}", headers=headers)
        assert response.status_code == 200
        body = response.json()
        if body["status"] not in {"queued", "running"}:
            return body
        time.sleep(0.01)
    raise AssertionError(f"import batch did not finish: {body}")


def test_importer_api_reports_accepted_rejected_and_completed_folders(
    tmp_path: Path,
    capsys,
) -> None:
    folder = tmp_path / "王五"
    folder.mkdir()
    _pdf(folder / "王五简历.pdf", "resume")
    _pdf(folder / "获奖证书.pdf", "certificate")
    backend = FakeBackend()
    application = create_app(
        config=_config(tmp_path),
        backend=backend,  # type: ignore[arg-type]
        api_key="test-importer-key",
    )

    with TestClient(application) as client:
        payload = {"folders": ["王五", "不存在", "../越界目录"]}
        unauthorized = client.post("/api/v1/imports", json=payload)
        assert unauthorized.status_code == 401

        headers = {"X-Importer-Api-Key": "test-importer-key"}
        accepted = client.post("/api/v1/imports", headers=headers, json=payload)
        assert accepted.status_code == 202, accepted.text
        accepted_body = accepted.json()
        assert [item["folder"] for item in accepted_body["accepted"]] == ["王五"]
        assert [item["code"] for item in accepted_body["rejected"]] == [
            "folder_not_found",
            "folder_name_invalid",
        ]

        body = _wait_for_batch(client, accepted_body["requestId"], headers)
        assert body["status"] == "partial_failed"
        assert body["summary"] == {
            "requested": 3,
            "accepted": 1,
            "rejected": 2,
            "queued": 0,
            "processing": 0,
            "completed": 1,
            "failed": 0,
        }
        assert body["results"][0]["status"] == "completed"
        assert body["results"][0]["candidateId"] == "CAND_IMPORTED"

        missing = client.get("/api/v1/imports/unknown-request", headers=headers)
        assert missing.status_code == 404

    log_output = capsys.readouterr().out
    for event in (
        "folder_rejected",
        "batch_submitted",
        "batch_started",
        "folder_started",
        "folder_inspected",
        "resume_upload_completed",
        "document_upload_completed",
        "folder_archived",
        "folder_completed",
        "batch_completed",
        "batch_status_not_found",
    ):
        assert f'"event": "{event}"' in log_output
    assert "test-importer-key" not in log_output


def test_importer_queues_new_request_while_another_batch_is_running(tmp_path: Path) -> None:
    first = tmp_path / "甲"
    second = tmp_path / "乙"
    first.mkdir()
    second.mkdir()
    _pdf(first / "甲简历.pdf", "resume")
    _pdf(second / "乙简历.pdf", "resume")
    started = threading.Event()
    release = threading.Event()

    class BlockingBackend(FakeBackend):
        def upload_resume(self, path: Path, idempotency_key: str) -> dict:
            if not started.is_set():
                started.set()
                assert release.wait(timeout=5)
            return super().upload_resume(path, idempotency_key)

    application = create_app(
        config=_config(tmp_path),
        backend=BlockingBackend(),  # type: ignore[arg-type]
        api_key="test-importer-key",
    )
    headers = {"X-Importer-Api-Key": "test-importer-key"}

    with TestClient(application) as client:
        first_response = client.post("/api/v1/imports", headers=headers, json={"folders": ["甲"]})
        assert first_response.status_code == 202
        assert started.wait(timeout=2)

        second_response = client.post("/api/v1/imports", headers=headers, json={"folders": ["乙"]})
        assert second_response.status_code == 202
        assert second_response.json()["accepted"][0]["status"] == "queued"

        duplicate = client.post("/api/v1/imports", headers=headers, json={"folders": ["乙", "乙"]})
        assert duplicate.status_code == 200
        assert [item["code"] for item in duplicate.json()["rejected"]] == [
            "folder_already_queued",
            "duplicate_in_request",
        ]

        release.set()
        first_body = _wait_for_batch(client, first_response.json()["requestId"], headers)
        second_body = _wait_for_batch(client, second_response.json()["requestId"], headers)
        assert first_body["status"] == "completed"
        assert second_body["status"] == "completed"


def test_backend_client_sends_existing_backend_contract(
    tmp_path: Path, monkeypatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/auth/login"):
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={"accessToken": "backend-token", "tokenType": "bearer", "user": {}},
            )
        assert request.headers["Authorization"] == "Bearer backend-token"
        assert request.headers["Idempotency-Key"].startswith("importer:")
        body = request.content
        if request.url.path.endswith("/candidate-intakes/uploads"):
            assert b'filename="candidate-resume.pdf"' in body
            return httpx.Response(
                202,
                json={
                    "candidate_id": "CAND_HTTP",
                    "submission_id": "RSUB_HTTP",
                    "workflow_run_id": "WF_HTTP",
                },
            )
        assert request.url.path.endswith("/candidates/CAND_HTTP/documents")
        assert b'name="category"' in body and b"certificate" in body
        assert b'name="sourceStage"' in body and b"import" in body
        return httpx.Response(201, json={"documentId": "CDL_HTTP"})

    monkeypatch.setenv("IMPORTER_USERNAME", "importer-user")
    monkeypatch.setenv("IMPORTER_PASSWORD", "importer-password")
    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = RecruitmentBackendClient(
        "http://backend:8000/api/v1",
        10,
        client=transport_client,
    )
    resume = tmp_path / "candidate-resume.pdf"
    document = tmp_path / "candidate-certificate.pdf"
    _pdf(resume, "resume")
    _pdf(document, "certificate")

    resume_result = client.upload_resume(resume, "importer:resume:test")
    document_result = client.upload_candidate_document(
        candidate_id=resume_result["candidate_id"],
        path=document,
        display_name="候选人证书",
        category="certificate",
        idempotency_key="importer:document:test",
    )
    client.close()

    assert document_result["documentId"] == "CDL_HTTP"
    assert [request.url.path for request in requests] == [
        "/api/v1/auth/login",
        "/api/v1/candidate-intakes/uploads",
        "/api/v1/candidates/CAND_HTTP/documents",
    ]
