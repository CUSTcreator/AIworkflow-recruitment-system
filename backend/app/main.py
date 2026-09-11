from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend.app.api.error_handlers import register_error_handlers
from backend.app.bootstrap.model_gateway import configure_model_infrastructure
from backend.app.api.v1.router import api_router
from backend.app.core.config import settings
from backend.app.infrastructure.observability import configure_logging
from backend.app.infrastructure.observability.metrics import metrics
from backend.app.infrastructure.observability.workflow_queue_metrics import observe_workflow_queue
from backend.app.infrastructure.observability.logging import get_logger, log_event
from backend.app.infrastructure.observability.middleware import RequestObservabilityMiddleware
from backend.app.db.init_db import init_db
from backend.app.db.session import SessionLocal
from backend.app.db.session import engine
from backend.app.seeds.seed_accounts import seed_accounts
from backend.app.seeds.import_university_rankings import import_university_rankings
from backend.app.modules.assessment.hard_screening.hard_screening_catalog_service import HardScreeningCatalogService


configure_logging()
configure_model_infrastructure()


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate_runtime()
    init_db()
    with SessionLocal() as db:
        seed_accounts(db)
        import_university_rankings(db)
        # 固定目录属于部署初始化，不允许由 GET 请求在运行期隐式写入。
        HardScreeningCatalogService(db).ensure_defaults()
        db.commit()
    yield


app = FastAPI(
    title="Recruit AI System Backend",
    version="0.1.0",
    lifespan=lifespan,
)
register_error_handlers(app)

app.add_middleware(RequestObservabilityMiddleware)
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint() -> Response:
    try:
        with SessionLocal() as db:
            observe_workflow_queue(db)
    except Exception as error:
        # 监控端点不能因一次采样失败而不可用；错误仍写入结构化日志。
        log_event(get_logger(__name__), 30, "workflow_queue_metrics_sample_failed", error_type=type(error).__name__)
    return Response(metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

app.include_router(api_router)

# CORS 在所有用户中间件之后注册，保证它位于请求观测中间件之外。保持 FastAPI
# app 对象本身不变，测试与生命周期代码仍可访问 app.router。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[item.strip() for item in settings.cors_origins.split(",") if item.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

