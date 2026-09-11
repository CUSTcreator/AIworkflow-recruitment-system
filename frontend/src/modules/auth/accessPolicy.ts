import type { ApplicationStatus } from "@/modules/applications/contracts";
import type { Role } from "@/modules/auth/contracts";

export const roleLabels: Record<Role, string> = {
  department_manager: "部门主管",
  department_recruiter: "部门招聘人",
  hr: "HR"
};

export const roleDescriptions: Record<Role, string> = {
  department_manager: "只读查看本部门招聘进度和候选人摘要。",
  department_recruiter: "负责本部门硬筛复核、初步筛选、部门审核和技术一面。",
  hr: "负责候选人导入、二面审核、二面执行和最终招聘决策。"
};

export const roles: Role[] = [
  "department_manager",
  "department_recruiter",
  "hr"
];

export function displayRoleName(user: {
  role: string;
  roleId?: string;
  roleName?: string;
}): string {
  const configuredName = user.roleName?.trim();
  if (configuredName && configuredName !== user.role && configuredName !== user.roleId) {
    return configuredName;
  }
  return (roleLabels as Record<string, string>)[user.role] ?? "未配置角色";
}

export function ownerRoleForStatus(status: ApplicationStatus): Role | "system" | "none" {
  switch (status) {
    case "resume_processing":
    case "resume_processing_failed":
    case "resume_review_required":
      return "hr";
    case "hard_screening_pending":
    case "hard_screening_running":
    case "hard_screening_review":
    case "submitted":
    case "screening_running":
    case "screening_failed":
      return "department_recruiter";
    case "department_review":
    case "first_interview_planning":
    case "first_interview_scheduled":
    case "first_interview_in_progress":
    case "first_interview_evaluation":
      return "department_recruiter";
    case "hr_second_review":
    case "manual_review":
    case "second_interview_in_progress":
    case "second_interview_evaluation":
    case "final_review":
      return "hr";
    default:
      return "none";
  }
}

export function canViewCandidateDetail(_role: Role): boolean {
  return true;
}
