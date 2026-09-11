"""Public HTTP router for the candidate business module."""

from fastapi import APIRouter

from backend.app.modules.candidates import candidate_document_router, candidate_router, intake_router


router = APIRouter()
router.include_router(candidate_router.router)
router.include_router(intake_router.router)
router.include_router(candidate_document_router.router)
