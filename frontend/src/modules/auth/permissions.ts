export type BusinessPermission =
  | "resume.upload"
  | "resume_submission.manage"
  | "application.create"
  | "job_document.upload"
  | "job_document.confirm"
  | "job.edit"
  | "job.delete"
  | "hard_screening.policy.manage"
  | "hard_screening.catalog.manage"
  | "interview_guide.manage"
  | "screening.run"
  | "hard_screening.review"
  | "department_review.manage"
  | "application.delete"
  | "candidate_document.upload"
  | "candidate_document.manage"
  | "first_interview.manage"
  | "second_interview.manage"
  | "final_decision.manage";

export type PermissionEffect = "allow" | "deny";

/**
 * 管理端用职责包而非原子权限码配置角色和个人例外。实际可配置包由后端
 * responsibilityCatalog 返回，前端仅保留这个联合类型保障 API 调用安全。
 */
export type ResponsibilityBundleCode =
  | "candidate_materials"
  | "job_and_screening_policy"
  | "department_recruitment_execution"
  | "second_interview_management"
  | "final_recruitment_decision"
  | "global_recruitment_configuration"
  | "delete_business_records";

export function hasBusinessPermission(
  user: { permissions?: string[] } | null | undefined,
  permission: BusinessPermission
): boolean {
  return Boolean(user?.permissions?.includes(permission));
}
