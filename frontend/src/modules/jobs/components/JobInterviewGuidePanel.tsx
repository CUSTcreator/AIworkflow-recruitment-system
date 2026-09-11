import { Save } from 'lucide-react';
import { useEffect, useState } from "react";

import {
  getJobInterviewGuideTemplate,
  getPublishedInterviewGuideTemplates,
  updateJobInterviewGuideTemplate,
  type InterviewGuideTemplateView
} from "@/modules/interviews/interviewGuideApi";
import { Button } from "@/shared/ui/Button";


export function JobInterviewGuidePanel({ token, jobId }: { token: string | null; jobId: string }) {
  const [templates, setTemplates] = useState<InterviewGuideTemplateView[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [resolvedName, setResolvedName] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) return;
    void Promise.all([
      getPublishedInterviewGuideTemplates(token),
      getJobInterviewGuideTemplate(token, jobId)
    ]).then(([items, binding]) => {
      setTemplates(items);
      setTemplateId(binding.configuredTemplateId ?? "");
      setResolvedName(binding.resolvedTemplate ? binding.resolvedTemplate.name : "尚无可用通用题单");
      setError("");
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "题单配置加载失败"));
  }, [jobId, token]);

  async function save() {
    if (!token) return;
    try {
      const binding = await updateJobInterviewGuideTemplate(token, jobId, templateId || undefined);
      setResolvedName(binding.resolvedTemplate ? binding.resolvedTemplate.name : "尚无可用通用题单");
      setMessage("岗位通用题单已更新。");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "题单配置保存失败");
    }
  }

  return (
    <div className="mt-5 rounded-md border border-line p-4">
      <h3 className="text-sm font-semibold text-ink">一面通用题单</h3>
      <p className="mt-1 text-xs text-muted">默认继承系统题单；只有该岗位需要不同通用问题时才单独选择。</p>
      {error ? <p className="mt-3 rounded-md bg-rose-50 p-3 text-sm text-rose-700">{error}</p> : null}
      {message ? <p className="mt-3 rounded-md bg-emerald-50 p-3 text-sm text-emerald-700">{message}</p> : null}
      <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-xs font-medium text-slate-700">题单来源
          <select className="mt-1 h-10 w-full rounded-md border border-line bg-white px-3 text-sm" value={templateId} onChange={(event) => setTemplateId(event.target.value)}>
            <option value="">继承系统默认题单</option>
            {templates.map((template) => <option key={template.templateId} value={template.templateId}>{template.name} · V{template.publishedVersion}</option>)}
          </select>
        </label>
        <Button variant="primary" onClick={() => void save()}><Save size={16} />保存</Button>
      </div>
      <p className="mt-3 text-xs text-muted">当前实际使用：{resolvedName}</p>
    </div>
  );
}
