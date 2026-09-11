import { useEffect, useMemo, useState } from "react";

import {
  getAvailableHardScreeningCriteria,
  getHardScreeningPolicy,
  saveHardScreeningPolicy,
  type HardScreeningCatalogCriterion,
  type HardScreeningPolicyView,
  type HardScreeningRuleInput
} from "@/modules/applications/api";
import { useAuth } from "@/modules/auth/AuthProvider";
import { Button } from "@/shared/ui/Button";
import { Section } from "@/shared/ui/Section";

function toRule(criterion: HardScreeningCatalogCriterion): HardScreeningRuleInput {
  return {
    ruleId: crypto.randomUUID(), criterionId: criterion.criterionId, name: criterion.name,
    valueMode: criterion.valueMode, evaluationBinding: criterion.evaluationBinding, enabled: true,
    condition: criterion.valueMode === "select" ? { selectedValues: [criterion.allowedValues[0] ?? ""] } : criterion.valueMode === "number" ? { min: null, max: null } : { text: "" }
  };
}

function normalizeLoadedRule(rule: HardScreeningRuleInput, criteria: HardScreeningCatalogCriterion[]): HardScreeningRuleInput {
  const matched = criteria.find((item) => item.criterionId === rule.criterionId || item.code === rule.criterionType);
  if (!matched) return rule;
  return {
    ...rule, criterionId: matched.criterionId, name: matched.name, valueMode: matched.valueMode,
    evaluationBinding: matched.evaluationBinding,
    condition: rule.condition ?? (matched.valueMode === "select"
      ? { selectedValues: [String(rule.expectedValue ?? matched.allowedValues[0] ?? "")] }
      : matched.valueMode === "number" ? { min: typeof rule.expectedValue === "number" ? rule.expectedValue : null, max: null }
      : { text: String(rule.expectedValue ?? "") })
  };
}

export function HardScreeningPolicyPanel({ jobId, onSaved }: { jobId: string; onSaved?: (policy: HardScreeningPolicyView) => void }) {
  const { token } = useAuth();
  const [criteria, setCriteria] = useState<HardScreeningCatalogCriterion[]>([]);
  const [rules, setRules] = useState<HardScreeningRuleInput[]>([]);
  const [policyMeta, setPolicyMeta] = useState<Pick<HardScreeningPolicyView, "enabled" | "generationMode" | "generationStatus">>({ enabled: false });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token || !jobId) return;
    setError("");
    void Promise.all([getAvailableHardScreeningCriteria(token), getHardScreeningPolicy(token, jobId)])
      .then(([catalog, policy]) => {
        setCriteria(catalog);
        setRules(policy.rules.map((rule) => normalizeLoadedRule(rule, catalog)));
        setPolicyMeta({ enabled: policy.enabled, generationMode: policy.generationMode, generationStatus: policy.generationStatus });
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "硬筛策略加载失败"));
  }, [jobId, token]);

  const usedIds = useMemo(() => new Set(rules.map((item) => item.criterionId).filter(Boolean)), [rules]);
  const available = criteria.filter((item) => !usedIds.has(item.criterionId));
  const update = (index: number, patch: Partial<HardScreeningRuleInput>) => setRules((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));

  async function save() {
    if (!token || !jobId) return;
    setSaving(true); setError(""); setMessage("");
    try {
      const policy = await saveHardScreeningPolicy(token, jobId, { enabled: rules.length > 0, rules });
      setRules(policy.rules.map((rule) => normalizeLoadedRule(rule, criteria)));
      setPolicyMeta({ enabled: policy.enabled, generationMode: policy.generationMode, generationStatus: policy.generationStatus });
      setMessage("硬筛策略已保存；重新运行当前申请后生效，也会影响后续导入。");
      onSaved?.(policy);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "硬筛条件保存失败");
    } finally { setSaving(false); }
  }

  return <Section title="岗位硬筛策略" description="新导入候选人按当前岗位策略执行；已产生的硬筛结果不会被改写。">
    {!jobId ? <p className="text-sm text-muted">请先确认并创建岗位。</p> : <>
      {policyMeta.generationMode === "job_requirement_auto" && !policyMeta.enabled ? <div className={`mb-3 rounded-md border p-3 text-sm ${policyMeta.generationStatus === "degraded" ? "border-amber-300 bg-amber-50 text-amber-900" : "border-blue-200 bg-blue-50 text-blue-800"}`}><p className="font-medium">{policyMeta.generationStatus === "degraded" ? "系统已按确定性规则生成硬筛草稿，部分自由文本需要确认" : "系统已根据岗位要求生成硬筛策略草稿"}</p></div> : null}
      <div className="flex flex-wrap items-center gap-3"><select aria-label="添加硬筛条件" className="h-10 min-w-52 rounded-md border border-line bg-white px-3 text-sm" defaultValue="" onChange={(event) => { const item = criteria.find((criterion) => criterion.criterionId === event.target.value); if (item) setRules((current) => [...current, toRule(item)]); event.currentTarget.value = ""; }}><option value="">添加条件…</option>{available.map((item) => <option key={item.criterionId} value={item.criterionId}>{item.name}</option>)}</select><Button type="button" variant="primary" disabled={saving} onClick={() => void save()}>{saving ? "保存中" : policyMeta.generationMode === "job_requirement_auto" && !policyMeta.enabled ? "确认并启用" : "保存"}</Button></div>
      <div className="mt-3 space-y-2">{rules.map((rule, index) => <PolicyRuleEditor key={rule.ruleId ?? index} rule={rule} criterion={criteria.find((item) => item.criterionId === rule.criterionId)} onChange={(patch) => update(index, patch)} onDelete={() => setRules((current) => current.filter((_, itemIndex) => itemIndex !== index))} />)}{rules.length === 0 ? <p className="text-sm text-muted">当前岗位未配置硬筛条件。</p> : null}</div>
    </>}
    {message ? <p className="mt-3 text-sm text-emerald-700">{message}</p> : null}{error ? <p className="mt-3 text-sm text-rose-700">{error}</p> : null}
  </Section>;
}

function PolicyRuleEditor({ rule, criterion, onChange, onDelete }: { rule: HardScreeningRuleInput; criterion?: HardScreeningCatalogCriterion; onChange: (patch: Partial<HardScreeningRuleInput>) => void; onDelete: () => void }) {
  if (!criterion) return <div className="flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"><span>{rule.name}：该历史硬筛项已停用或删除，请删除后重新配置。</span><Button type="button" variant="danger" onClick={onDelete}>删除</Button></div>;
  const condition = rule.condition ?? {};
  return <div className="grid items-start gap-3 rounded-md border border-line bg-slate-50 p-3 lg:grid-cols-[220px_minmax(0,1fr)_auto]"><div className="pt-2 text-sm font-medium text-slate-700">{criterion.name}</div><HardScreeningValueEditor criterion={criterion} condition={condition} onChange={(condition) => onChange({ condition })} /><Button type="button" variant="danger" onClick={onDelete}>删除</Button></div>;
}

function HardScreeningValueEditor({ criterion, condition, onChange }: { criterion: HardScreeningCatalogCriterion; condition: NonNullable<HardScreeningRuleInput["condition"]>; onChange: (condition: NonNullable<HardScreeningRuleInput["condition"]>) => void }) {
  if (criterion.valueMode === "select") return <select aria-label={`${criterion.name}要求`} className="h-10 rounded-md border border-line bg-white px-3 text-sm" value={condition.selectedValues?.[0] ?? ""} onChange={(event) => onChange({ selectedValues: [event.target.value] })}>{criterion.allowedValues.map((value) => <option key={value} value={value}>{value}</option>)}</select>;
  if (criterion.valueMode === "number") return <div className="grid gap-2 sm:grid-cols-2"><label className="flex h-10 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm"><span className="text-muted">最小值</span><input className="min-w-0 flex-1 border-0 bg-transparent outline-none" type="number" placeholder="不限制" value={condition.min ?? ""} onChange={(event) => onChange({ ...condition, min: event.target.value === "" ? null : Number(event.target.value) })} /></label><label className="flex h-10 items-center gap-2 rounded-md border border-line bg-white px-3 text-sm"><span className="text-muted">最大值</span><input className="min-w-0 flex-1 border-0 bg-transparent outline-none" type="number" placeholder="不限制" value={condition.max ?? ""} onChange={(event) => onChange({ ...condition, max: event.target.value === "" ? null : Number(event.target.value) })} /></label></div>;
  return <textarea aria-label={`${criterion.name}要求`} className="min-h-10 resize-y rounded-md border border-line bg-white px-3 py-2 text-sm" rows={2} value={condition.text ?? ""} onChange={(event) => onChange({ text: event.target.value })} placeholder={`填写${criterion.name}的硬性要求`} />;
}
