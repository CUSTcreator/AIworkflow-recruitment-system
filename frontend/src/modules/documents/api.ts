import { requestJson, requestUrlResponse, toAbsoluteApiUrl } from "@/shared/api/httpClient";

export type ApplicationDocumentScope = "candidate" | "application" | "job";
export type ApplicationUploadCategory = "standard_resume" | "psychological_assessment" | "academic_transcript" | "other";
export type CandidateDocumentCategory = "psychological_assessment" | "academic_transcript" | "certificate" | "portfolio" | "other";
export type ApplicationDocumentCategory = "resume" | ApplicationUploadCategory | CandidateDocumentCategory | "job";
export type ApplicationDocumentSourceStage = "import" | "screening" | "first_interview" | "second_interview" | "final_review" | "manual_upload" | "job";

export interface ApplicationDocument {
  documentId: string;
  scope: ApplicationDocumentScope;
  displayName: string;
  filename: string;
  primary: boolean;
  category: ApplicationDocumentCategory;
  sourceStage: ApplicationDocumentSourceStage;
  note?: string;
  status: string;
  imagesUrl: string;
  canRename: boolean;
  canDelete: boolean;
  uploadedAt: string;
  uploadedBy: string;
  uploadedByName: string;
  deleteImpactCount?: number;
}

export interface ApplicationDocumentsView {
  applicationId: string;
  candidateId?: string;
  documents: ApplicationDocument[];
  permissions: {
    canUpload: boolean;
  };
}

export interface CandidateDocumentsView {
  candidateId: string;
  activeApplicationCount: number;
  currentResume?: {
    submissionId: string;
    filename: string;
    pdfUrl: string;
    uploadedAt: string;
    status: string;
  } | null;
  documents: ApplicationDocument[];
  permissions: {
    canUpload: boolean;
  };
}

export async function getCandidateDocuments(token: string, candidateId: string) {
  return requestJson<CandidateDocumentsView>(token, `/candidates/${candidateId}/documents`);
}

export async function uploadCandidateDocument(
  token: string,
  candidateId: string,
  input: { file: File; displayName: string; category: CandidateDocumentCategory; sourceStage?: "import" | "manual_upload"; note?: string }
) {
  const body = new FormData();
  body.append("file", input.file);
  body.append("displayName", input.displayName);
  body.append("category", input.category);
  body.append("sourceStage", input.sourceStage ?? "manual_upload");
  if (input.note?.trim()) body.append("note", input.note.trim());
  const document = await requestJson<ApplicationDocument>(token, `/candidates/${candidateId}/documents`, { method: "POST", body });
  announceCandidateDocumentsChanged(candidateId);
  return document;
}

export async function renameCandidateDocument(
  token: string,
  candidateId: string,
  documentId: string,
  displayName: string,
) {
  const document = await requestJson<ApplicationDocument>(
    token,
    `/candidates/${candidateId}/documents/${documentId}`,
    { method: "PATCH", body: JSON.stringify({ displayName }) },
  );
  announceCandidateDocumentsChanged(candidateId);
  return document;
}

export async function deleteCandidateDocument(
  token: string,
  candidateId: string,
  documentId: string,
) {
  await requestJson<void>(token, `/candidates/${candidateId}/documents/${documentId}`, { method: "DELETE" });
  announceCandidateDocumentsChanged(candidateId);
}

export async function getApplicationDocuments(token: string, applicationId: string) {
  return requestJson<ApplicationDocumentsView>(
    token,
    `/applications/${applicationId}/documents`
  );
}

export async function uploadApplicationDocument(
  token: string,
  applicationId: string,
  input: {
    file: File;
    displayName: string;
    category: ApplicationUploadCategory;
    sourceStage: Exclude<ApplicationDocumentSourceStage, "import" | "job">;
    note?: string;
  }
) {
  const body = new FormData();
  body.append("file", input.file);
  body.append("displayName", input.displayName);
  body.append("category", input.category);
  body.append("sourceStage", input.sourceStage);
  if (input.note?.trim()) body.append("note", input.note.trim());
  const document = await requestJson<ApplicationDocument>(
    token,
    `/applications/${applicationId}/documents`,
    { method: "POST", body }
  );
  announceApplicationDocumentsChanged(applicationId);
  return document;
}

export async function renameApplicationDocument(
  token: string,
  applicationId: string,
  documentId: string,
  displayName: string
) {
  const document = await requestJson<ApplicationDocument>(
    token,
    `/applications/${applicationId}/documents/${documentId}`,
    { method: "PATCH", body: JSON.stringify({ displayName }) }
  );
  announceApplicationDocumentsChanged(applicationId);
  return document;
}

export async function deleteApplicationDocument(
  token: string,
  applicationId: string,
  documentId: string
) {
  await requestJson<void>(token, `/applications/${applicationId}/documents/${documentId}`, {
    method: "DELETE"
  });
  announceApplicationDocumentsChanged(applicationId);
}

export async function loadApplicationDocumentPages(
  token: string,
  document: ApplicationDocument
): Promise<string[]> {
  const manifestUrl = toAbsoluteApiUrl(document.imagesUrl) ?? document.imagesUrl;
  const response = await requestUrlResponse(token, manifestUrl);
  const body = (await response.json()) as { pages?: string[] };
  const pageUrls = body.pages ?? [];
  if (pageUrls.length === 0) throw new Error("资料没有可展示的页面");
  return Promise.all(
    pageUrls.map(async (page) => {
      const url = toAbsoluteApiUrl(page) ?? page;
      const pageResponse = await requestUrlResponse(token, url);
      return URL.createObjectURL(await pageResponse.blob());
    })
  );
}

export const APPLICATION_DOCUMENTS_CHANGED_EVENT = "application-documents-changed";

export function announceApplicationDocumentsChanged(applicationId: string) {
  window.dispatchEvent(new CustomEvent(APPLICATION_DOCUMENTS_CHANGED_EVENT, { detail: { applicationId } }));
}

export const CANDIDATE_DOCUMENTS_CHANGED_EVENT = "candidate-documents-changed";

export function announceCandidateDocumentsChanged(candidateId: string) {
  window.dispatchEvent(new CustomEvent(CANDIDATE_DOCUMENTS_CHANGED_EVENT, { detail: { candidateId } }));
}
