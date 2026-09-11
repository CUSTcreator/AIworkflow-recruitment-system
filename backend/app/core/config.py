from pydantic import BaseModel
import os


class Settings(BaseModel):
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./recruit_ai_dev.db",
    )
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    minio_bucket: str = os.getenv("MINIO_BUCKET", "recruit-ai")
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local")
    allow_storage_fallback: bool = os.getenv("ALLOW_STORAGE_FALLBACK", "1") == "1"
    local_object_store_dir: str = os.getenv("LOCAL_OBJECT_STORE_DIR", "local_object_store")
    minio_request_timeout_seconds: int = int(os.getenv("MINIO_REQUEST_TIMEOUT_SECONDS", "60"))
    auth_secret_key: str = os.getenv("AUTH_SECRET_KEY", "change-this-secret-before-production")
    access_token_ttl_minutes: int = int(os.getenv("ACCESS_TOKEN_TTL_MINUTES", "480"))
    department_manager_password: str = os.getenv("DEPARTMENT_MANAGER_PASSWORD", "Dept@123456")
    department_recruiter_password: str = os.getenv("DEPARTMENT_RECRUITER_PASSWORD", "Recruit@123456")
    hr_password: str = os.getenv("HR_PASSWORD", "Hr@123456")
    workflow_worker_poll_seconds: float = float(os.getenv("WORKFLOW_WORKER_POLL_SECONDS", "2"))
    workflow_worker_lease_seconds: int = int(os.getenv("WORKFLOW_WORKER_LEASE_SECONDS", "120"))
    workflow_worker_max_attempts: int = int(os.getenv("WORKFLOW_WORKER_MAX_ATTEMPTS", "3"))
    workflow_worker_concurrency: int = max(1, int(os.getenv("WORKFLOW_WORKER_CONCURRENCY", "4")))
    workflow_execution_mode: str = os.getenv("WORKFLOW_EXECUTION_MODE", "queued")
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")
    document_max_file_size: int = int(os.getenv("DOCUMENT_MAX_FILE_SIZE", str(20 * 1024 * 1024)))
    document_parse_timeout_seconds: int = int(os.getenv("DOCUMENT_PARSE_TIMEOUT_SECONDS", "180"))
    document_parser_backend: str = os.getenv("DOCUMENT_PARSER_BACKEND", "auto")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    observability_service_name: str = os.getenv("OBSERVABILITY_SERVICE_NAME", "recruit-ai-backend")
    metrics_enabled: bool = os.getenv("METRICS_ENABLED", "1") == "1"
    database_slow_query_ms: int = int(os.getenv("DATABASE_SLOW_QUERY_MS", "500"))

    def validate_runtime(self) -> None:
        if self.app_env.lower() != "production":
            return
        insecure_values = {
            "auth_secret_key": "change-this-secret-before-production",
            "minio_access_key": "minioadmin",
            "minio_secret_key": "minioadmin",
            "department_manager_password": "Dept@123456",
            "department_recruiter_password": "Recruit@123456",
            "hr_password": "Hr@123456",
        }
        invalid = [name for name, value in insecure_values.items() if getattr(self, name) == value]
        if invalid:
            raise RuntimeError("insecure_production_settings:" + ",".join(invalid))
        if self.allow_storage_fallback:
            raise RuntimeError("production_storage_fallback_must_be_disabled")


settings = Settings()
