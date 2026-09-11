"""Public HTTP router for the document-ingestion business module."""

from fastapi import APIRouter

from backend.app.modules.document_ingestion import job_router, resume_router


router = APIRouter()
router.include_router(job_router.router)
router.include_router(resume_router.router)
