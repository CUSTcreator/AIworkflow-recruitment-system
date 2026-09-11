export type ApplicationStatus =
  | "resume_processing"
  | "resume_processing_failed"
  | "resume_review_required"
  | "waiting_job_profile"
  | "hard_screening_pending"
  | "hard_screening_running"
  | "hard_screening_review"
  | "submitted"
  | "screening_running"
  | "screening_failed"
  | "department_review"
  | "first_interview_planning"
  | "first_interview_scheduled"
  | "first_interview_in_progress"
  | "first_interview_evaluation"
  | "hr_second_review"
  | "second_interview_in_progress"
  | "second_interview_evaluation"
  | "final_review"
  | "offer_process"
  | "on_hold"
  | "manual_review"
  | "closed_rejected"
  | "closed_cancelled"
  | "resume_replaced";

export type FinalDecision = "offer_process" | "closed_rejected" | "manual_review";

export interface Candidate {
  candidateId: string;
  displayName: string;
  anonymizedCode: string;
  currentTitle: string;
  yearsOfExperience: string;
  education: string;
  tags: string[];
  resumePdfUrl?: string;
  resumePdfFilename?: string;
}

export interface Job {
  jobId: string;
  title: string;
  department: string;
  owner: string;
  headcount: number;
  openHeadcount: number;
  location: string;
  jdTextPreview?: string;
  majorRequirement?: string;
  educationRequirement?: string;
}

export interface Application {
  applicationId: string;
  candidateId: string;
  jobId: string;
  status: ApplicationStatus;
  department: string;
  currentOwner: string;
  assignedFirstInterviewer: string;
  assignedSecondHr: string;
  submittedAt: string;
  updatedAt: string;
  dueAt: string;
  overdue: boolean;
  resumePdfUrl?: string;
  resumeSubmissionId?: string;
}
