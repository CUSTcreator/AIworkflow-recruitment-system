export type JobAssignmentRole =
  | "hiring_manager"
  | "department_recruiter";

export function jobConfigurationLabel(
  missingAssignments: JobAssignmentRole[],
): string {
  const missingManager = missingAssignments.includes("hiring_manager");
  const missingRecruiter = missingAssignments.includes("department_recruiter");
  if (missingManager && missingRecruiter) return "负责人未配置";
  if (missingManager) return "未配置部门主管";
  if (missingRecruiter) return "未配置招聘人";
  return "负责人配置完整";
}

