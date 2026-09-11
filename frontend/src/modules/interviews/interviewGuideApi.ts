import { API_BASE, requestJson, requestResponse } from "@/shared/api/httpClient";

export type CommonQuestionResultType = "capability" | "non_scoring";

export interface CommonGuideQuestion {
  questionId?: string;
  question: string;
  evaluationPoints: string[];
  required: boolean;
  resultType: CommonQuestionResultType;
  order?: number;
}

export interface InterviewGuideTemplateView {
  templateId: string;
  name: string;
  round: "first";
  isDefault: boolean;
  isActive: boolean;
  archivedAt?: string;
  latestVersion?: number;
  latestVersionId?: string;
  latestStatus?: "draft" | "published" | "retired";
  publishedVersion?: number;
  publishedVersionId?: string;
  questions: CommonGuideQuestion[];
  sourceDocumentId?: string;
  versions: Array<{
    templateVersionId: string;
    version: number;
    status: string;
    questionCount: number;
    sourceDocumentId?: string;
    createdAt: string;
    publishedAt?: string;
  }>;
  updatedAt: string;
}

export interface JobGuideTemplateBinding {
  jobId: string;
  configuredTemplateId?: string;
  inheritDefault: boolean;
  resolvedTemplate?: {
    templateId: string;
    templateVersionId: string;
    name: string;
    version: number;
    isDefault: boolean;
  };
}

export function getAdminInterviewGuideTemplates(token: string) {
  return requestJson<InterviewGuideTemplateView[]>(token, "/admin/interview-guide-templates");
}

export function getPublishedInterviewGuideTemplates(token: string) {
  return requestJson<InterviewGuideTemplateView[]>(token, "/interview-guide-templates/published");
}

export function createInterviewGuideTemplate(
  token: string,
  input: { name: string; templateId?: string; questions: CommonGuideQuestion[] }
) {
  return requestJson<InterviewGuideTemplateView>(token, "/admin/interview-guide-templates", {
    method: "POST",
    body: JSON.stringify(input)
  });
}

export function importInterviewGuideTemplate(
  token: string,
  input: { name: string; file: File; templateId?: string }
) {
  const form = new FormData();
  form.append("name", input.name);
  form.append("file", input.file);
  if (input.templateId) form.append("template_id", input.templateId);
  return requestJson<InterviewGuideTemplateView>(token, "/admin/interview-guide-templates/import", {
    method: "POST",
    body: form
  });
}

export function updateInterviewGuideTemplateDraft(
  token: string,
  versionId: string,
  input: { name?: string; questions?: CommonGuideQuestion[] }
) {
  return requestJson<InterviewGuideTemplateView>(token, `/admin/interview-guide-template-versions/${versionId}`, {
    method: "PATCH",
    body: JSON.stringify(input)
  });
}

export function publishInterviewGuideTemplate(token: string, versionId: string, isDefault: boolean) {
  return requestJson<InterviewGuideTemplateView>(token, `/admin/interview-guide-template-versions/${versionId}/publish`, {
    method: "POST",
    body: JSON.stringify({ isDefault })
  });
}

export function archiveInterviewGuideTemplate(token: string, templateId: string) {
  return requestJson<InterviewGuideTemplateView>(token, `/admin/interview-guide-templates/${templateId}/archive`, {
    method: "POST"
  });
}

export function setInterviewGuideTemplateActive(token: string, templateId: string, isActive: boolean) {
  return requestJson<InterviewGuideTemplateView>(token, `/admin/interview-guide-templates/${templateId}/status`, {
    method: "PATCH",
    body: JSON.stringify({ isActive })
  });
}

export function getJobInterviewGuideTemplate(token: string, jobId: string) {
  return requestJson<JobGuideTemplateBinding>(token, `/job-management/jobs/${jobId}/interview-guide-template`);
}

export function updateJobInterviewGuideTemplate(token: string, jobId: string, templateId?: string) {
  return requestJson<JobGuideTemplateBinding>(token, `/job-management/jobs/${jobId}/interview-guide-template`, {
    method: "PATCH",
    body: JSON.stringify({ templateId: templateId || null })
  });
}

export async function downloadFirstInterviewGuide(token: string, applicationId: string, draft = false) {
  const response = await requestResponse(token, `/applications/${applicationId}/interviews/first/guide/export.pdf?draft=${draft}`);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const encodedName = disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1];
  const filename = encodedName ? decodeURIComponent(encodedName) : "一面题单.pdf";
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // 给浏览器留出完成下载的时间，再释放 Blob URL。
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export async function previewFirstInterviewGuide(token: string, applicationId: string, draft = false) {
  const preview = window.open("about:blank", "_blank");
  if (preview) preview.opener = null;
  let response: Response;
  try {
    response = await requestResponse(token, `/applications/${applicationId}/interviews/first/guide/export.pdf?draft=${draft}`);
  } catch (error) {
    preview?.close();
    throw error;
  }
  const url = URL.createObjectURL(await response.blob());
  if (preview) {
    preview.location.href = url;
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export function firstInterviewGuidePreviewUrl(applicationId: string, draft = false) {
  return `${API_BASE}/applications/${applicationId}/interviews/first/guide/export.pdf?draft=${draft}`;
}
