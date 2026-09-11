from fastapi import APIRouter

from backend.app.modules.applications import router as applications
from backend.app.modules.candidates import router as candidates
from backend.app.modules.analytics import router as analytics
from backend.app.modules.assessment import router as assessment
from backend.app.modules.auth import router as auth
from backend.app.modules.document_ingestion import router as document_ingestion
from backend.app.modules.interview_guides import router as interview_guides
from backend.app.modules.interviews import router as interviews
from backend.app.modules.jobs import router as recruitment_management
from backend.app.modules.system_admin import router as system_admin
from backend.app.modules.tasks import router as tasks


api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(analytics.router)
api_router.include_router(applications.router)
api_router.include_router(assessment.router)
api_router.include_router(interviews.router)
api_router.include_router(document_ingestion.router)
api_router.include_router(recruitment_management.router)
api_router.include_router(candidates.router)
api_router.include_router(tasks.router)
api_router.include_router(system_admin.router)
api_router.include_router(interview_guides.router)

