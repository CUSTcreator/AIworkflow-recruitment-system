import { FileUp, Plus, Save, Send, X } from 'lucide-react';
import { useCallback, useEffect, useState } from "react";

import {
  archiveInterviewGuideTemplate,
  createInterviewGuideTemplate,
  getAdminInterviewGuideTemplates,
  importInterviewGuideTemplate,
  publishInterviewGuideTemplate,
  setInterviewGuideTemplateActive,
  updateInterviewGuideTemplateDraft,
  type CommonGuideQuestion,
  type InterviewGuideTemplateView
} from "@/modules/interviews/interviewGuideApi";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { Section } from "@/shared/ui/Section";


const emptyQuestion = (): CommonGuideQuestion => ({
  question: "",
  evaluationPoints: [],
  required: true,
  resultType: "capability"
});

export function InterviewGuideTemplatePanel({ token }: { token: string | null }) {
  const [templates, setTemplates] = useState<InterviewGuideTemplateView[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(true);
  const [importName, setImportName] = useState("");
  const [importFile, setImportFile] = useState<File>();
  const [showArchived, setShowArchived] = useState(false);
  const [editing, setEditing] = useState<{
    templateId?: string;
    versionId?: string;
    name: string;
    questions: CommonGuideQuestion[];
    isDefault: boolean;
  }>();

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      setTemplates(await getAdminInterviewGuideTemplates(token));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "通用题单加载失败");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void load(); }, [load]);

  async function execute(action: () => Promise<unknown>, success: string) {
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败");
    }
  }

  async function upload() {
    if (!token || !importFile || !importName.trim()) return;
    await execute(
      () => importInterviewGuideTemplate(token, { name: importName.trim(), file: importFile }),
      "题单已解析为草稿，请预览后发布。"
    );
    setImportFile(undefined);
    setImportName("");
  }

  async function saveDraft() {
    if (!token || !editing) return;
    const questions = editing.questions.filter((item) => item.question.trim());
    if (!questions.length) {
      setError("题单至少需要一道题目");
      return;
    }
    await execute(
      () => editing.versionId
        ? updateInterviewGuideTemplateDraft(token, editing.versionId, { name: editing.name, questions })
        : createInterviewGuideTemplate(token, { name: editing.name, templateId: editing.templateId, questions }),
      "题单草稿已保存。"
    );
    setEditing(undefined);
  }

  const visibleTemplates = templates.filter((template) => showArchived || !template.archivedAt);

  return (
    <div className="space-y-4">
      {error ? <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div> : null}
      {message ? <div className="rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div> : null}
      <Section
        title="通用题单"
        description="发布后的题单可被岗位继承；修改会产生新版本，不影响已确认的候选人题单。"
        action={<div className="flex gap-2"><Button type="button" onClick={() => setShowArchived((current) => !current)}>{showArchived ? "隐藏已归档" : "查看已归档"}</Button><Button onClick={() => setEditing({ name: "", questions: [emptyQuestion()], isDefault: templates.filter((item) => !item.archivedAt).length === 0 })}><Plus size={16} />新建题单</Button></div>}
      >
        <div className="mb-5 grid gap-3 rounded-md border border-line bg-slate-50 p-4 md:grid-cols-[minmax(180px,1fr)_minmax(220px,1fr)_auto] md:items-end">
          <label className="text-xs font-medium text-slate-700">题单名称
            <input className="admin-input mt-1" value={importName} onChange={(event) => setImportName(event.target.value)} placeholder="例如：默认一面通用题单" />
          </label>
          <label className="text-xs font-medium text-slate-700">导入文件
            <input className="mt-1 block w-full text-sm" type="file" accept=".xlsx,.docx,.pdf" onChange={(event) => setImportFile(event.target.files?.[0])} />
          </label>
          <Button variant="primary" disabled={!importName.trim() || !importFile} onClick={() => void upload()}><FileUp size={16} />导入并预览</Button>
        </div>
        {loading ? <p className="text-sm text-muted">正在加载题单…</p> : null}
        <div className="space-y-3">
          {visibleTemplates.map((template) => (
            <article key={template.templateId} className="rounded-md border border-line bg-white p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold text-ink">{template.name}</h3>
                    {template.isDefault ? <Badge className="border-blue-200 bg-blue-50 text-blue-700">系统默认</Badge> : null}
                    <Badge>{template.archivedAt ? "已归档" : template.isActive ? "已启用" : "已停用"}</Badge>
                    <Badge>{template.latestStatus === "published" ? "已发布" : "草稿"}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted">当前 V{template.latestVersion ?? "-"} · {template.questions.length} 道题</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {template.archivedAt ? <span className="self-center text-xs text-muted">已归档 · {new Date(template.archivedAt).toLocaleString("zh-CN")}</span> : <>
                  {template.latestStatus === "draft" && template.latestVersionId ? (
                    <Button onClick={() => setEditing({
                      templateId: template.templateId,
                      versionId: template.latestVersionId,
                      name: template.name,
                      questions: template.questions.map((item) => ({ ...item, evaluationPoints: [...item.evaluationPoints] })),
                      isDefault: template.isDefault
                    })}>编辑草稿</Button>
                  ) : (
                    <Button onClick={() => setEditing({
                      templateId: template.templateId,
                      name: template.name,
                      questions: template.questions.map((item) => ({ ...item, questionId: undefined, evaluationPoints: [...item.evaluationPoints] })),
                      isDefault: template.isDefault
                    })}>创建新版本</Button>
                  )}
                  {template.latestStatus === "draft" && template.latestVersionId ? (
                    <Button variant="primary" onClick={() => token && void execute(
                      () => publishInterviewGuideTemplate(token, template.latestVersionId!, template.isDefault || !templates.some((item) => item.isDefault)),
                      "题单已发布。"
                    )}><Send size={15} />发布</Button>
                  ) : null}
                  {!template.isDefault && template.publishedVersionId ? (
                    <Button onClick={() => token && void execute(
                      () => publishInterviewGuideTemplate(token, template.publishedVersionId!, true),
                      "已设为系统默认题单。"
                    )}>设为系统默认</Button>
                  ) : null}
                  <Button variant={template.isActive ? "danger" : "secondary"} onClick={() => token && void execute(
                    () => setInterviewGuideTemplateActive(token, template.templateId, !template.isActive),
                    template.isActive ? "题单已停用。" : "题单已启用。"
                  )}>{template.isActive ? "停用" : "启用"}</Button>
                  <Button variant="danger" onClick={() => token && window.confirm(`确认归档题单“${template.name}”吗？归档后不能再用于岗位或生成新版本。`) && void execute(
                    () => archiveInterviewGuideTemplate(token, template.templateId),
                    "题单已归档，历史申请中的冻结题目不受影响。"
                  )}>归档</Button>
                  </>}
                </div>
              </div>
              <ol className="mt-3 grid gap-1 text-sm text-slate-700">
                {template.questions.slice(0, 4).map((question, index) => <li key={question.questionId ?? index}>{index + 1}. {question.question}</li>)}
                {template.questions.length > 4 ? <li className="text-muted">还有 {template.questions.length - 4} 道题…</li> : null}
              </ol>
            </article>
          ))}
          {!loading && visibleTemplates.length === 0 ? <div className="rounded-md border border-dashed border-line p-8 text-center text-sm text-muted">尚未配置符合当前筛选条件的通用题单。</div> : null}
        </div>
      </Section>

      {editing ? (
        <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/40 p-4" role="dialog" aria-modal="true">
          <div className="mx-auto max-w-3xl rounded-lg bg-white p-5 shadow-xl">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold">{editing.versionId ? "编辑题单草稿" : editing.templateId ? "创建题单新版本" : "新建通用题单"}</h2>
              <button className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={() => setEditing(undefined)}><X size={18} /></button>
            </div>
            <label className="mt-4 block text-sm font-medium">题单名称
              <input className="admin-input mt-1" value={editing.name} onChange={(event) => setEditing({ ...editing, name: event.target.value })} />
            </label>
            <div className="mt-4 space-y-3">
              {editing.questions.map((question, index) => (
                <article key={question.questionId ?? index} className="rounded-md border border-line p-3">
                  <div className="flex items-start gap-2">
                    <span className="mt-2 text-xs font-semibold text-muted">通用题 {index + 1}</span>
                    <textarea className="min-h-20 flex-1 rounded-md border border-line px-3 py-2 text-sm" value={question.question} onChange={(event) => setEditing({
                      ...editing,
                      questions: editing.questions.map((item, itemIndex) => itemIndex === index ? { ...item, question: event.target.value } : item)
                    })} />
                    <button className="rounded-md p-2 text-muted hover:bg-rose-50 hover:text-rose-700" onClick={() => setEditing({ ...editing, questions: editing.questions.filter((_, itemIndex) => itemIndex !== index) })}><X size={16} /></button>
                  </div>
                  <input className="admin-input mt-2" placeholder="考察要点，用；分隔" value={question.evaluationPoints.join("；")} onChange={(event) => setEditing({
                    ...editing,
                    questions: editing.questions.map((item, itemIndex) => itemIndex === index ? { ...item, evaluationPoints: event.target.value.split(/[；;]+/).map((value) => value.trim()).filter(Boolean) } : item)
                  })} />
                  <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-700">
                    <label><input type="checkbox" checked={question.required} onChange={(event) => setEditing({ ...editing, questions: editing.questions.map((item, itemIndex) => itemIndex === index ? { ...item, required: event.target.checked } : item) })} /> 必问</label>
                    <select value={question.resultType} onChange={(event) => setEditing({ ...editing, questions: editing.questions.map((item, itemIndex) => itemIndex === index ? { ...item, resultType: event.target.value as CommonGuideQuestion["resultType"] } : item) })}>
                      <option value="capability">能力相关</option>
                      <option value="non_scoring">非评分信息</option>
                    </select>
                  </div>
                </article>
              ))}
            </div>
            <div className="mt-4 flex items-center justify-between">
              <Button onClick={() => setEditing({ ...editing, questions: [...editing.questions, emptyQuestion()] })}><Plus size={16} />添加题目</Button>
              <Button variant="primary" disabled={!editing.name.trim()} onClick={() => void saveDraft()}><Save size={16} />保存草稿</Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
