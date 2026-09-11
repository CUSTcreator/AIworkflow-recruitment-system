from pydantic import BaseModel, Field


class ApplicationView(BaseModel):
    applicationId: str
    candidateId: str
    jobId: str
    status: str
    department: str = ""
    currentOwner: str = ""
    submittedAt: str = ""
    updatedAt: str = ""
    dueAt: str = ""
    overdue: bool = False
    resumeSubmissionId: str | None = None


class CandidateView(BaseModel):
    candidateId: str
    displayName: str
    anonymizedCode: str = ""
    currentTitle: str = ""
    yearsOfExperience: str = ""
    education: str = ""
    tags: list[str] = Field(default_factory=list)
    resumePdfUrl: str | None = None
    resumePdfFilename: str | None = None


class JobView(BaseModel):
    jobId: str
    title: str
    department: str = ""
    owner: str = ""
    headcount: int = 0
    openHeadcount: int = 0
    location: str = ""
    jdTextPreview: str | None = None
    majorRequirement: str = ""
    educationRequirement: str = ""
