import { CheckCircle2, CircleAlert, FileUp, Plus, RefreshCw, Save, Sparkles, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";

import { getDepartments, type DepartmentOption } from "@/modules/applications/api";
import {
  confirmJobDrafts,
  deleteJobDraft,
  getJobDocument,
  getJobDrafts,
  repairJobDocument,
  retryJobDocument,
  updateJobDraft,
  uploadJobDocument,
  type JobDraftFieldHint,
  type JobDraftHardScreeningCriterion,
  type JobDraftHardScreeningPreview,
  type JobDraftView,
  type JobDocumentImportView
} from "@/modules/jobs/documentApi";
import { useAuth } from "@/modules/auth/AuthProvider";
import { toUserFacingError } from "@/shared/utils/displayText";
import { Button } from "@/shared/ui/Button";
import { Section } from "@/shared/ui/Section";
import { getDocumentStatusPresentation } from "@/modules/documents/status";
import { useToast } from "@/shared/toast/ToastProvider";
import { recoveryAction } from "@/shared/recovery/actions";

export function JobRequirementImportPanel({
  initialDocumentId = "",
  onConfirmed
}: {
  initialDocumentId?: string;
  onConfirmed?: () => void;
}) {
  const { token } = useAuth();
  const toast = useToast();
  const [departments, setDepartments] = useState<DepartmentOption[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [documentId, setDocumentId] = useState(initialDocumentId);
  const [importTask, setImportTask] = useState<JobDocumentImportView>();
  const [drafts, setDrafts] = useState<JobDraftView[]>([]);
  const [uploading, setUploading] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [savingId, setSavingId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [duplicateAttention, setDuplicateAttention] = useState<string[]>([]);
  const [repairing, setRepairing] = useState(false);

  useEffect(() => {
    if (!token) return;
    void getDepartments(token).then((items) => {
      setDepartments(items);
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "部门加载失败"));
  }, [token]);

  useEffect(() => {
    if (initialDocumentId) setDocumentId(initialDocumentId);
  }, [initialDocumentId]);

  const refreshDocument = useCallback(async () => {
    if (!token || !documentId) return;
    const next = await getJobDocument(token, documentId);
    setImportTask(next);
    if (["review_required", "partially_confirmed", "completed"].includes(next.import_status)) {
      setDrafts(await getJobDrafts(token, documentId));
    }
  }, [documentId, token]);

  useEffect(() => {
    if (!documentId) return;
    void refreshDocument().catch((reason) =>
      setError(reason instanceof Error ? reason.message : "解析状态查询失败")
    );
  }, [documentId, refreshDocument]);

  async function handleUpload(event: FormEvent) {
    event.preventDefault();
    if (!token || !file) {
      setError("请选择招聘要求XLSX文件。");
      return;
    }
    setUploading(true);
    setError("");
    setMessage("");
    setDrafts([]);
    try {
      const response = await uploadJobDocument(token, file);
      setDocumentId(response.document_id);
      setMessage(response.reused ? "该文件已经导入，已恢复原处理记录。" : "文件已提交后台解析。");
      toast.success(response.reused ? "该招聘要求已导入，已恢复原记录" : "招聘要求已接收，正在后台解析");
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "文件上传失败";
      setError(text);
      toast.error(text);
    } finally {
      setUploading(false);
    }
  }

  function patchDraft(draftId: string, patch: Partial<JobDraftView>) {
    setDrafts((current) =>
      current.map((draft) => draft.job_draft_id === draftId ? { ...draft, ...patch } : draft)
    );
  }

  async function saveDraft(draft: JobDraftView) {
    if (!token || !documentId) return;
    setSavingId(draft.job_draft_id);
    setError("");
    try {
      const saved = await updateJobDraft(token, documentId, draft.job_draft_id, {
        title: draft.title,
        headcount: draft.headcount,
        responsibilities: normalizeLines(draft.responsibilities),
        qualifications: normalizeLines(draft.qualifications),
        education_requirement: draft.education_requirement,
        major_requirement: draft.major_requirement,
        department_id: draft.department_id,
        preset_model_id: draft.preset_model_id,
        hard_screening_rules: draft.hard_screening_preview ?? [],
      });
      patchDraft(draft.job_draft_id, saved);
      setMessage(`已保存：${saved.title}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "岗位草稿保存失败");
    } finally {
      setSavingId("");
    }
  }

  async function deleteDraft(draft: JobDraftView) {
    if (!token || !documentId || !window.confirm(`确定删除未确认岗位草稿“${draft.title}”吗？删除后不会创建正式岗位。`)) return;
    setDeletingId(draft.job_draft_id);
    setError("");
    try {
      const result = await deleteJobDraft(token, documentId, draft.job_draft_id);
      await refreshDocument();
      setMessage(result.import_status === "completed" ? "已删除最后一条草稿，当前导入已结束。" : `已删除草稿：${draft.title}`);
      toast.success("未确认岗位草稿已删除");
      onConfirmed?.();
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "岗位草稿删除失败";
      setError(text);
      toast.error(text);
    } finally {
      setDeletingId("");
    }
  }

  async function confirmAll() {
    if (!token || !documentId) return;
    const groups = new Map<string, JobDraftView[]>();
    drafts.filter((item) => item.duplicate_status === "same_upload").forEach((item) => {
      const key = item.duplicate_group_id || item.job_draft_id;
      groups.set(key, [...(groups.get(key) || []), item]);
    });
    const unresolved = drafts.filter((item) => item.duplicate_status === "existing_job" && !["overwrite", "skip"].includes(item.resolution || ""));
    groups.forEach((items) => {
      const primary = items.filter((item) => ["keep", "overwrite"].includes(item.resolution || ""));
      if (primary.length !== 1 || items.some((item) => item !== primary[0] && item.resolution !== "skip")) unresolved.push(...items);
    });
    if (unresolved.length) {
      const ids = [...new Set(unresolved.map((item) => item.job_draft_id))];
      setDuplicateAttention(ids);
      setError(`还有 ${ids.length} 个重复岗位未处理，请选择覆盖或跳过。`);
      window.document.getElementById(`job-draft-${ids[0]}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    setDuplicateAttention([]);
    const pendingDrafts = drafts.filter((draft) => draft.status === "draft");
    const invalidRuleDraft = pendingDrafts.find((draft) =>
      (draft.hard_screening_preview ?? []).some((rule) => rule.enabled && !String(rule.expected_value).trim())
    );
    if (invalidRuleDraft) {
      setError(`“${invalidRuleDraft.title}”存在未填写要求的硬筛条件，请补充或删除后再确认。`);
      return;
    }
    setConfirming(true);
    setError("");
    try {
      await Promise.all(pendingDrafts.map((draft) => updateJobDraft(token, documentId, draft.job_draft_id, {
        title: draft.title, headcount: draft.headcount, responsibilities: normalizeLines(draft.responsibilities),
        qualifications: normalizeLines(draft.qualifications), education_requirement: draft.education_requirement,
        major_requirement: draft.major_requirement, department_id: draft.department_id,
        preset_model_id: draft.preset_model_id, resolution: draft.resolution,
        hard_screening_rules: draft.hard_screening_preview ?? [],
      })));
      const result = await confirmJobDrafts(token, documentId, pendingDrafts.map((draft) => ({
        draft_id: draft.job_draft_id,
        hard_screening_rules: draft.hard_screening_preview ?? [],
      })));
      const created = result.jobs.filter((item) => !item.skipped).length;
      const profileQueued = result.jobs.filter((item) => !item.skipped && ["queued", "processing"].includes(item.profile_status ?? "")).length;
      const message = profileQueued > 0
        ? `已创建或更新 ${created} 个正式岗位；其中 ${profileQueued} 个岗位画像正在后台生成。`
        : `已创建或更新 ${created} 个正式岗位。`;
      setMessage(message);
      toast.success(message);
      await refreshDocument();
      onConfirmed?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "岗位确认失败");
    } finally {
      setConfirming(false);
    }
  }
  async function retry() {
    if (!token || !documentId) return;
    setError("");
    try {
      await retryJobDocument(token, documentId);
      setMessage("已重新提交岗位文件解析任务。");
      toast.success("岗位文件已重新提交解析");
      await refreshDocument();
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "岗位文件重试失败";
      setError(text);
      toast.error(text);
    }
  }

  async function submitRepair(payload: Parameters<typeof repairJobDocument>[2]) {
    if (!token || !documentId) return;
    setRepairing(true);
    setError("");
    try {
      await repairJobDocument(token, documentId, payload);
      setMessage("已提交人工修复，系统将继续解析岗位文件。");
      toast.success("人工修复已提交");
      await refreshDocument();
    } catch (reason) {
      const text = reason instanceof Error ? reason.message : "岗位人工修复失败";
      setError(text);
      toast.error(text);
    } finally {
      setRepairing(false);
    }
  }

  const editable = importTask?.import_status === "review_required" || importTask?.import_status === "partially_confirmed";

  return (
      <div className="space-y-4">
        <Section title="上传招聘要求" description="上传XLSX后，系统提取多个岗位，并根据文件中的部门名称自动分发。">
          <form className="grid gap-3 md:grid-cols-[1fr_auto]" onSubmit={handleUpload}>
            <Field label="招聘要求文件">
              <input
                className="block h-10 w-full rounded-md border border-line bg-white px-3 py-2"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </Field>
            <Button className="self-end" variant="primary" type="submit" disabled={uploading}>
              <FileUp size={16} />{uploading ? "正在上传" : "上传并解析"}
            </Button>
          </form>
          {message ? <p className="mt-3 text-sm text-emerald-700">{message}</p> : null}
          {error ? <p className="mt-3 text-sm text-rose-700">{error}</p> : null}
        </Section>

        {importTask ? (
          <Section
            title={importTask.original_filename}
            description={`导入状态：${getDocumentStatusPresentation(importTask.import_status).label}`}
            action={(importTask.available_actions ?? []).some((item) => ["retry_job_extraction", "retry_publish_job_drafts"].includes(item.action)) ? (
              <Button type="button" onClick={() => void retry()}>
                <RefreshCw size={16} />{recoveryAction(importTask.available_actions ?? [], "retry_publish_job_drafts")?.label ?? recoveryAction(importTask.available_actions ?? [], "retry_job_extraction")?.label ?? "重新处理"}
              </Button>
            ) : !editable && importTask.import_status !== "completed" ? (
              <Button type="button" onClick={() => void refreshDocument()}>
                <RefreshCw size={16} />刷新状态
              </Button>
            ) : undefined}
          >
            {importTask.error_message ? (
              <p className="rounded-md bg-rose-50 p-3 text-sm text-rose-700">{toUserFacingError(importTask.error_message)}</p>
            ) : null}
            {(importTask.available_actions ?? []).some((item) => item.action === "review_job_headers") ? (
              <HeaderRepairForm
                context={importTask.recovery_context_json}
                disabled={repairing}
                onSubmit={(payload) => void submitRepair(payload)}
              />
            ) : null}
            {(importTask.available_actions ?? []).some((item) => item.action === "review_job_fields") && drafts.length === 0 ? (
              <FieldRepairForm disabled={repairing} onSubmit={(fields) => void submitRepair({ action: "review_job_fields", fields })} />
            ) : null}
            {!editable && importTask.import_status !== "completed" && importTask.import_status !== "failed" ? (
              <div className="flex items-center gap-2 py-8 text-sm text-muted">
                <RefreshCw size={18} />后台正在处理，完成后请点击“刷新状态”。
              </div>
            ) : null}
            {drafts.length > 0 ? (
              <div className="app-scrollbar max-h-[68vh] space-y-4 overflow-y-auto pr-2">
                {drafts.map((draft) => (
                  <DraftEditor
                    key={draft.job_draft_id}
                    draft={draft}
                    departments={departments}
                    editable={editable && draft.status === "draft"}
                    saving={savingId === draft.job_draft_id}
                    deleting={deletingId === draft.job_draft_id}
                    onChange={(patch) => patchDraft(draft.job_draft_id, patch)}
                    onSave={() => void saveDraft(draft)}
                    onDelete={() => void deleteDraft(draft)}
                  />
                ))}
              </div>
            ) : null}
            {editable && drafts.length > 0 ? (
              <div className="mt-4 flex justify-end border-t border-line pt-4">
                <Button type="button" variant="primary" disabled={confirming || drafts.some((draft) => draft.extraction_assistance?.status === "review_required") || drafts.some((draft) => draft.status === "draft" && draft.available_actions && !draft.available_actions.some((item) => item.action === "confirm_job_draft"))} onClick={() => void confirmAll()}>
                  <CheckCircle2 size={16} />{confirming ? "正在确认" : "确认岗位及硬筛条件"}
                </Button>
              </div>
            ) : null}
          </Section>
        ) : null}
      </div>
  );
}

function HeaderRepairForm({
  context,
  disabled,
  onSubmit,
}: {
  context?: JobDocumentImportView["recovery_context_json"];
  disabled: boolean;
  onSubmit: (payload: { action: "review_job_headers"; sheet_name: string; header_row_index: number; header_mapping: Record<string, number> }) => void;
}) {
  const sheets = context?.sheets ?? [];
  const [sheetIndex, setSheetIndex] = useState(0);
  const [rowIndex, setRowIndex] = useState(0);
  const [mapping, setMapping] = useState<Record<string, string>>({ title: "" });
  const sheet = sheets[sheetIndex];
  const rows = sheet?.rows ?? [];
  const headers = (rows[rowIndex] ?? []).map((value, index) => ({ index, label: String(value ?? `第${index + 1}列`) }));
  const fields = [
    ["title", "职位名称"], ["department", "部门"], ["headcount", "招聘人数"],
    ["responsibilities", "工作职责"], ["qualifications", "任职资格"], ["education", "学历"], ["major", "专业"],
  ] as const;
  return (
    <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-4">
      <p className="font-medium text-amber-900">请确认岗位表头后继续</p>
      {sheets.length === 0 ? <p className="mt-2 text-sm text-amber-800">暂时无法读取表头预览，请重新上传文件。</p> : (
        <>
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <label className="text-sm">工作表
              <select className="mt-1 h-9 w-full rounded-md border border-line bg-white px-2" value={sheetIndex} disabled={disabled} onChange={(event) => { setSheetIndex(Number(event.target.value)); setRowIndex(0); setMapping({ title: "" }); }}>
                {sheets.map((item, index) => <option key={item.name} value={index}>{item.name}</option>)}
              </select>
            </label>
            <label className="text-sm">表头行
              <select className="mt-1 h-9 w-full rounded-md border border-line bg-white px-2" value={rowIndex} disabled={disabled} onChange={(event) => setRowIndex(Number(event.target.value))}>
                {rows.slice(0, 20).map((_, index) => <option key={index} value={index}>第 {index + 1} 行</option>)}
              </select>
            </label>
          </div>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {fields.map(([field, label]) => (
              <label key={field} className="text-sm">{label}
                <select className="mt-1 h-9 w-full rounded-md border border-line bg-white px-2" value={mapping[field] ?? ""} disabled={disabled} onChange={(event) => setMapping((current) => ({ ...current, [field]: event.target.value }))}>
                  <option value="">不映射</option>
                  {headers.map((header) => <option key={header.index} value={header.index}>{header.index + 1}. {header.label}</option>)}
                </select>
              </label>
            ))}
          </div>
          <Button className="mt-3" type="button" variant="primary" disabled={disabled || !sheet || mapping.title === ""} onClick={() => onSubmit({ action: "review_job_headers", sheet_name: sheet.name, header_row_index: rowIndex, header_mapping: Object.fromEntries(Object.entries(mapping).filter(([, value]) => value !== "").map(([key, value]) => [key, Number(value)])) })}>
            <CheckCircle2 size={16} />确认表头并继续
          </Button>
        </>
      )}
    </div>
  );
}

function FieldRepairForm({ disabled, onSubmit }: { disabled: boolean; onSubmit: (fields: Record<string, Record<string, unknown>>) => void }) {
  const [value, setValue] = useState("{\n  \"Sheet1!A2\": {\n    \"title\": \"\"\n  }\n}");
  const [parseError, setParseError] = useState("");
  return (
    <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 p-4">
      <p className="font-medium text-amber-900">请校正岗位字段后继续</p>
      <p className="mt-1 text-xs text-amber-800">按“工作表!单元格”填写行引用，例如 Sheet1!A2；字段名可用 title、headcount、responsibilities、qualifications、education_requirement、major_requirement、department_name。</p>
      <textarea className="mt-3 min-h-28 w-full rounded-md border border-line bg-white px-3 py-2 font-mono text-xs" value={value} disabled={disabled} onChange={(event) => { setValue(event.target.value); setParseError(""); }} />
      {parseError ? <p className="mt-1 text-xs text-rose-700">{parseError}</p> : null}
      <Button className="mt-3" type="button" variant="primary" disabled={disabled} onClick={() => { try { const parsed = JSON.parse(value); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(); setParseError(""); onSubmit(parsed); } catch { setParseError("请输入有效的 JSON 对象"); } }}>
        <CheckCircle2 size={16} />提交字段修复
      </Button>
    </div>
  );
}

function DraftEditor({
  draft,
  departments,
  editable,
  saving,
  deleting,
  onChange,
  onSave,
  onDelete
}: {
  draft: JobDraftView;
  departments: DepartmentOption[];
  editable: boolean;
  saving: boolean;
  deleting: boolean;
  onChange: (patch: Partial<JobDraftView>) => void;
  onSave: () => void;
  onDelete: () => void;
}) {
  const hintFor = (field: JobDraftFieldHint["field"]) =>
    draft.extraction_assistance?.field_hints.find((item) => item.field === field);
  const hardScreeningRules = draft.hard_screening_preview ?? [];
  const updateHardScreeningRule = (index: number, patch: Partial<JobDraftHardScreeningPreview>) => {
    onChange({
      hard_screening_preview: hardScreeningRules.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item
      ),
    });
  };

  return (
    <article className="rounded-md border border-line bg-slate-50 p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink">
          <span>岗位 {draft.sequence_no}</span>
          {draft.extraction_assistance ? <ExtractionStatus status={draft.extraction_assistance.status} /> : null}
        </div>
        {draft.status === "confirmed" ? <span className="text-xs font-semibold text-emerald-700">已创建</span> : null}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="职位名称" hint={hintFor("title")}>
          <input className="h-10 w-full rounded-md border border-line bg-white px-3" value={draft.title}
            disabled={!editable} onChange={(event) => onChange({ title: event.target.value })} />
        </Field>
        <Field label="招聘人数" hint={hintFor("headcount")}>
          <input className="h-10 w-full rounded-md border border-line bg-white px-3" type="number" min={1}
            value={draft.headcount ?? ""} disabled={!editable}
            onChange={(event) => onChange({ headcount: event.target.value ? Number(event.target.value) : undefined })} />
        </Field>
        <Field label="所属部门" hint={hintFor("department_id")}>
          <select className="h-10 w-full rounded-md border border-line bg-white px-3" value={draft.department_id ?? ""}
            disabled={!editable} onChange={(event) => onChange({ department_id: event.target.value || undefined })}>
            <option value="">
              {draft.source_department_name
                ? draft.department_match_status === "auto_create"
                  ? `确认后自动创建：${draft.source_department_name}`
                  : `未匹配：${draft.source_department_name}`
                : "未识别部门"}
            </option>
            {departments.map((item) => (
              <option key={item.departmentId} value={item.departmentId}>{item.name}</option>
            ))}
          </select>

        </Field>
      </div>
      {draft.duplicate_status !== "new_job" ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">{draft.duplicate_status === "existing_job" ? "检测到已有同部门同名岗位" : "检测到本次文件内部重复岗位"}</p>
          <div className="mt-2 flex flex-wrap gap-4">
            {draft.duplicate_status === "same_upload" && !draft.existing_job_id ? <label><input type="radio" checked={draft.resolution === "keep"} disabled={!editable} onChange={() => onChange({ resolution: "keep" })} /> 保留此条</label> : null}
            {draft.existing_job_id ? <label><input type="radio" checked={draft.resolution === "overwrite"} disabled={!editable} onChange={() => onChange({ resolution: "overwrite" })} /> 覆盖现有岗位</label> : null}
            <label><input type="radio" checked={draft.resolution === "skip"} disabled={!editable} onChange={() => onChange({ resolution: "skip" })} /> 跳过此条</label>
          </div>
        </div>
      ) : null}      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <TextArea label="工作职责" hint={hintFor("responsibilities")} value={draft.responsibilities.join("\n")} disabled={!editable}
          onChange={(value) => onChange({ responsibilities: splitEditorLines(value) })} />
        <TextArea label="任职资格" hint={hintFor("qualifications")} value={draft.qualifications.join("\n")} disabled={!editable}
          onChange={(value) => onChange({ qualifications: splitEditorLines(value) })} />
      </div>
      <div className="mt-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="font-medium">硬筛条件</p>
            <p className="mt-1 text-xs text-blue-800">确认岗位后，启用的条件将直接作为该岗位的硬筛策略生效。</p>
          </div>
          {editable ? (
            <Button
              type="button"
              onClick={() => onChange({
                hard_screening_preview: [
                  ...hardScreeningRules,
                  { criterion_type: "custom", name: "自定义条件", expected_value: "", description: "", enabled: true },
                ],
              })}
            >
              <Plus size={15} />添加条件
            </Button>
          ) : null}
        </div>
        {hardScreeningRules.length ? (
          <div className="mt-3 space-y-2">
            {hardScreeningRules.map((item, index) => (
              <div className="grid items-center gap-2 md:grid-cols-[11rem_minmax(0,1fr)_auto_auto]" key={`${item.criterion_type}-${index}`}>
                <select
                  aria-label={`岗位 ${draft.sequence_no} 的硬筛条件类型`}
                  className="h-9 rounded-md border border-blue-200 bg-white px-2"
                  value={item.criterion_type}
                  disabled={!editable}
                  onChange={(event) => {
                    const criterionType = event.target.value as JobDraftHardScreeningCriterion;
                    updateHardScreeningRule(index, {
                      criterion_type: criterionType,
                      name: hardScreeningName(criterionType),
                      expected_value: "",
                      description: "",
                    });
                  }}
                >
                  {HARD_SCREENING_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
                {item.criterion_type === "highest_education_status" ? (
                  <select
                    aria-label={`${item.name}要求`}
                    className="h-9 min-w-0 rounded-md border border-blue-200 bg-white px-3"
                    value={item.expected_value}
                    disabled={!editable}
                    onChange={(event) => updateHardScreeningRule(index, { expected_value: event.target.value, description: "" })}
                  >
                    <option value="">请选择毕业状态</option>
                    <option value="已毕业">已毕业</option>
                    <option value="在读">在读</option>
                  </select>
                ) : (
                  <input
                    aria-label={`${item.name}要求`}
                    className="h-9 min-w-0 rounded-md border border-blue-200 bg-white px-3"
                    type={["minimum_experience_years", "highest_education_graduation_year"].includes(item.criterion_type) ? "number" : "text"}
                    min={item.criterion_type === "minimum_experience_years" ? 0 : item.criterion_type === "highest_education_graduation_year" ? 1950 : undefined}
                    max={item.criterion_type === "highest_education_graduation_year" ? 2100 : undefined}
                    step={item.criterion_type === "minimum_experience_years" ? 0.5 : item.criterion_type === "highest_education_graduation_year" ? 1 : undefined}
                    value={item.expected_value}
                    disabled={!editable}
                    onChange={(event) => updateHardScreeningRule(index, { expected_value: event.target.value, description: "" })}
                  />
                )}
                <label className="inline-flex h-9 items-center gap-2 whitespace-nowrap text-xs font-medium text-blue-900">
                  <input
                    type="checkbox"
                    checked={item.enabled}
                    disabled={!editable}
                    onChange={(event) => updateHardScreeningRule(index, { enabled: event.target.checked })}
                  />
                  启用
                </label>
                {editable ? (
                  <Button
                    className="w-9 px-0"
                    type="button"
                    variant="ghost"
                    title="删除硬筛条件"
                    aria-label="删除硬筛条件"
                    onClick={() => onChange({ hard_screening_preview: hardScreeningRules.filter((_, itemIndex) => itemIndex !== index) })}
                  >
                    <Trash2 size={15} />
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        ) : <p className="mt-2 text-xs text-blue-800">未识别到明确硬性条件。</p>}
      </div>
      <div className="mt-3 flex items-end gap-3">
        <Field label="学历要求" hint={hintFor("education_requirement")} className="flex-1">
          <input className="h-10 w-full rounded-md border border-line bg-white px-3"
            value={draft.education_requirement ?? ""} disabled={!editable}
            onChange={(event) => onChange({ education_requirement: event.target.value })} />
        </Field>
        <Field label="专业要求" hint={hintFor("major_requirement")} className="flex-1">
          <input className="h-10 w-full rounded-md border border-line bg-white px-3"
            value={draft.major_requirement ?? ""} disabled={!editable}
            onChange={(event) => onChange({ major_requirement: event.target.value })} />
        </Field>
        <Field label="经历评价模型" className="min-w-56">
          <select
            className="h-10 w-full rounded-md border border-line bg-white px-3"
            value={draft.preset_model_id}
            disabled={!editable}
            onChange={(event) => onChange({
              preset_model_id: event.target.value as JobDraftView["preset_model_id"]
            })}
          >
            <option value="engineering_experience">工程技术经历</option>
            <option value="general_professional_experience">通用职业经历</option>
          </select>
        </Field>
        {editable ? (
          <>
            <Button type="button" disabled={saving || deleting || !draft.title.trim()} onClick={onSave}>
              <Save size={16} />{saving ? "保存中" : "保存"}
            </Button>
            <Button type="button" variant="danger" disabled={saving || deleting} onClick={onDelete}>
              <Trash2 size={16} />{deleting ? "删除中" : "删除草稿"}
            </Button>
          </>
        ) : null}
      </div>
    </article>
  );
}

function ExtractionStatus({ status }: { status: "ai_assisted" | "review_required" }) {
  const review = status === "review_required";
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${review ? "text-amber-700" : "text-sky-700"}`}>
      {review ? <CircleAlert size={13} /> : <Sparkles size={13} />}
      {review ? "需要确认" : "AI辅助提取"}
    </span>
  );
}

function Field({ label, hint, className = "", children }: { label: string; hint?: JobDraftFieldHint; className?: string; children: ReactNode }) {
  return (
    <label className={`space-y-1.5 text-sm ${className}`}>
      <span className="flex items-center gap-1.5 font-medium text-slate-700">
        {label}{hint ? <ExtractionStatus status={hint.status} /> : null}
      </span>
      {children}
      {hint?.status === "review_required" ? <span className="block text-xs text-amber-700">{hint.message}</span> : null}
    </label>
  );
}

function TextArea({ label, hint, value, disabled, onChange }: {
  label: string;
  hint?: JobDraftFieldHint;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label} hint={hint}>
      <textarea className="min-h-32 w-full rounded-md border border-line bg-white px-3 py-2"
        value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
    </Field>
  );
}
function splitEditorLines(value: string): string[] {
  return value.split(/\r?\n/);
}

function normalizeLines(lines: string[]): string[] {
  return lines.map((item) => item.trim()).filter(Boolean);
}

const HARD_SCREENING_TYPES: Array<{ value: JobDraftHardScreeningCriterion; label: string }> = [
  { value: "minimum_degree", label: "最低学历" },
  { value: "highest_education_status", label: "最高学历状态" },
  { value: "highest_education_graduation_year", label: "最高学历毕业年份" },
  { value: "minimum_experience_years", label: "相关工作经验" },
  { value: "project_experience", label: "相关项目经验" },
  { value: "required_skill", label: "必备技能" },
  { value: "certification", label: "证书/资质" },
  { value: "custom", label: "自定义条件" },
];

function hardScreeningName(type: JobDraftHardScreeningCriterion): string {
  return HARD_SCREENING_TYPES.find((item) => item.value === type)?.label ?? "自定义条件";
}
