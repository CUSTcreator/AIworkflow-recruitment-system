import { AlertTriangle, BriefcaseBusiness, CalendarPlus, ChevronDown, Eye, Pencil, RefreshCw, XCircle } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type {
  EvidenceIndexItem,
} from "@/modules/assessment/contracts";
import type { InterviewEvidence } from "@/modules/interviews/contracts";
import { gateLabels } from "@/modules/assessment/labels";
import { buildScreeningReviewReadModel } from "@/modules/assessment/readModelMappers";
import { readScoreSnapshot } from "@/modules/assessment/scoreSnapshotMapper";
import { useInterviewCommands } from "@/modules/interviews/hooks/useInterviewCommands";
import { useSecondInterviewReviewReadModel } from "@/modules/interviews/hooks/useInterviewPageReadModels";
import { useRequiredApplicationId } from "@/modules/applications/hooks/useRequiredApplicationId";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { CandidateDecisionOverviewCard } from "@/modules/assessment/components/CandidateDecisionOverviewCard";
import { RecruitmentMilestoneCard } from "@/modules/applications/components/RecruitmentMilestoneCard";
import { HardScreeningResultCard } from "@/modules/assessment/components/HardScreeningResultCard";
import { ScoreExplanationButton } from "@/modules/assessment/components/ScoreExplanationButton";
import { JobCapabilityMatchPanel, type CapabilityUpdateView } from "@/modules/assessment/components/JobCapabilityMatchPanel";
import { DocumentWorkspace } from "@/modules/documents/components/DocumentWorkspace";
import {
  FirstInterviewQuestionRecordsCard,
  FirstInterviewRawNotesCard,
  InterviewOriginalRecordsSection
} from "@/modules/interviews/components/OriginalInterviewRecords";
import {
  DecisionFocusCard,
  StageHandoffCard
} from "@/modules/interviews/components/DecisionSupportCards";
import { NonCapabilityCard } from "@/modules/interviews/components/NonCapabilityCard";
import { PageLoading } from "@/shared/ui/PageLoading";
import { hasRecoveryAction, recoveryAction } from "@/shared/recovery/actions";
import { workspaceReturnPath } from "@/shared/navigation/workspaceReturn";

function scoreText(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "待核验";
}

function evidenceUpdate(evidence: InterviewEvidence[]): { label: string; tone: string } | undefined {
  if (evidence.length === 0) return undefined;
  const hasPositive = evidence.some((item) => item.polarity === "positive");
  const hasNegative = evidence.some((item) => item.polarity === "negative");
  if (hasPositive && hasNegative) return { label: "一面有分歧", tone: "border-amber-200 bg-amber-50 text-amber-800" };
  if (hasNegative) return { label: "一面存疑", tone: "border-orange-200 bg-orange-50 text-orange-800" };
  if (hasPositive) return { label: "一面确认", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" };
  return { label: "一面补充", tone: "border-blue-200 bg-blue-50 text-blue-700" };
}

export function HrSecondReviewScreen({ mode = "review" }: { mode?: "review" | "final" }) {
  const applicationId = useRequiredApplicationId();
  const navigate = useNavigate();
  const location = useLocation();
  const returnPath = workspaceReturnPath(location.state);
  const {
    approveSecondInterview,
    makeHrDecision,
    makeFinalDecision,
    retryPostFirstScoring,
    retryPostSecondScoring,
    rebuildScreeningAssessment,
    repairPostFirstScoring,
    repairPostSecondScoring,
    loadEvidence
  } = useInterviewCommands();
  const { data: workspaceView, loading, error: loadError, reload: loadWorkspace, token } = useSecondInterviewReviewReadModel(applicationId, mode);

  const context = workspaceView;
  const [activeEvidenceId, setActiveEvidenceId] = useState<string>();
  const [activeEvidenceDetail, setActiveEvidenceDetail] = useState<EvidenceIndexItem>();
  const [retryingAssessment, setRetryingAssessment] = useState(false);
  const [repairingAssessment, setRepairingAssessment] = useState(false);
  const [repairEditorOpen, setRepairEditorOpen] = useState(false);
  const [repairNotes, setRepairNotes] = useState("");
  const [decisionSubmitting, setDecisionSubmitting] = useState(false);
  const evidenceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (evidenceTimerRef.current) clearTimeout(evidenceTimerRef.current);
  }, []);

  if (loading) return <PageLoading label={mode === "final" ? "正在加载最终审核" : "正在加载二面审核"} />;
  if (loadError) {
    return <PageHeader
      title="二面工作区加载失败"
      description={loadError}
      actions={<>
        <Button onClick={() => navigate("/candidates")}>返回招聘流程</Button>
        <Button onClick={() => void loadWorkspace()}>重新加载</Button>
      </>}
    />;
  }
  if (!context) return <PageHeader title="未找到 HR 审核任务" description="请从任务中心重新进入。" />;

  const { application, candidate, job, screening } = context;
  const readModel = buildScreeningReviewReadModel(screening);
  const reviewPackage = workspaceView.reviewPackage;
  const finalPackage = workspaceView.finalPackage;
  const hrAssessment = workspaceView.hrAssessment;
  const firstEvidence = workspaceView.firstEvidence;
  const afterFirstSnapshot = readScoreSnapshot(workspaceView.afterFirstScoreSnapshot);
  const afterSecondSnapshot = readScoreSnapshot(workspaceView.afterSecondScoreSnapshot);
  const activeSnapshot = mode === "final"
    ? (afterSecondSnapshot ?? afterFirstSnapshot)
    : afterFirstSnapshot;
  // 历史 V2/V3 由招聘流程页的结果弹窗查看；终审工作台只展示当前决策所需的
  // 最新已发布结果。V3 尚未发布时临时回退 V2，并由下方提示明确告知用户。
  const activeChanges = mode === "final"
    ? (workspaceView.afterSecondAssessmentChanges ?? workspaceView.afterFirstAssessmentChanges)
    : workspaceView.afterFirstAssessmentChanges;
  const hasStageScore = Boolean(activeSnapshot);
  // 最终审核页可能先进入、V3 仍在运行；标题必须以已发布的 AAV 为准，不能把 V2 伪装成 V3。
  const decisionStage = workspaceView.decisionOverview.assessmentStage;
  const pendingAssessmentMessage = mode === "final" && decisionStage !== "after_second_interview"
    ? "二面后评估处理中，当前展示上一轮已发布结果。"
    : undefined;
  const activeAssessmentStage = activeChanges?.stage ?? activeSnapshot?.stage ?? decisionStage;
  const showScoreChanges = Boolean(activeSnapshot || activeChanges) && activeAssessmentStage !== "screening";
  const showCapabilityScoreUpdates = showScoreChanges;
  const currentSummary = {
    total: activeSnapshot?.total ?? readModel.summary.total_score,
    capabilityTotal: activeSnapshot?.capabilityTotal,
    jobFit: activeSnapshot?.jobFit ?? readModel.summary.job_fit_score,
    resumeExperience: activeSnapshot?.resumeExperience ?? readModel.summary.resume_experience_score,
    education: activeSnapshot?.education ?? readModel.summary.education_score
  };
  const activeEvidence: EvidenceIndexItem | undefined = activeEvidenceId
    ? activeEvidenceDetail ?? readModel.evidence_index[activeEvidenceId]
    : undefined;
  const canOperate = workspaceView.availableActions.includes("approve_second_interview");
  const canOperateFinal = workspaceView.availableActions.includes("offer");
  const canRetryFirstAssessment = workspaceView.availableActions.includes("retry_post_first_scoring")
    || hasRecoveryAction(workspaceView.recoveryActions, "retry_post_first_scoring");
  const canRetrySecondAssessment = workspaceView.availableActions.includes("retry_post_second_scoring")
    || hasRecoveryAction(workspaceView.recoveryActions, "retry_post_second_scoring");
  const canEditFeedback = hasRecoveryAction(
    workspaceView.recoveryActions,
    mode === "final" ? "edit_second_interview_feedback" : "edit_first_interview_feedback",
  );
  const canRebuildScreeningSource = hasRecoveryAction(
    workspaceView.recoveryActions,
    "rebuild_screening_assessment",
  );
  const canRebuildFirstAssessment = hasRecoveryAction(
    workspaceView.recoveryActions,
    "rebuild_previous_post_first_assessment",
  );
  const recoveryMessage = typeof workspaceView.workflowStatus.recoveryMessage === "string"
    ? workspaceView.workflowStatus.recoveryMessage
    : workspaceView.workflowStatus.runStatus === "review_required"
      ? (mode === "final"
        ? "二面记录输入待确认，请查看二面记录；必要时修改记录后重新计算。"
        : "一面记录输入待确认，请查看一面记录；必要时修改记录后重新计算。")
      : "本轮评估未完成，请选择一种处理方式。";
  const jobRequirements = screening.screeningResultView?.jobRequirements ?? [];

  async function completeFinalDecision(action: () => Promise<string | undefined>) {
    if (decisionSubmitting) return;
    setDecisionSubmitting(true);
    const result = await action();
    if (result === undefined) {
      setDecisionSubmitting(false);
      return;
    }
    navigate(returnPath, { replace: true });
  }

  async function go(action: Promise<string | undefined>) {
    const redirectTo = await action;
    if (redirectTo) await loadWorkspace();
    if (redirectTo) navigate(redirectTo);
  }

  async function retryAssessment() {
    setRetryingAssessment(true);
    const result = mode === "final"
      ? await retryPostSecondScoring(applicationId)
      : await retryPostFirstScoring(applicationId);
    setRetryingAssessment(false);
    if (result !== undefined) await loadWorkspace();
  }

  async function submitFeedbackRepair() {
    if (!repairNotes.trim()) return;
    setRepairingAssessment(true);
    const result = mode === "final"
      ? await repairPostSecondScoring(applicationId, repairNotes.trim())
      : await repairPostFirstScoring(applicationId, repairNotes.trim());
    setRepairingAssessment(false);
    if (result !== undefined) {
      setRepairEditorOpen(false);
      await loadWorkspace();
    }
  }

  async function showResumeEvidence(evidenceId: string) {
    setActiveEvidenceId(evidenceId);
    setActiveEvidenceDetail(readModel.evidence_index[evidenceId]);
    if (token) {
      try {
        setActiveEvidenceDetail(await loadEvidence(applicationId, evidenceId));
      } catch {
        // Keep the evidence summary visible if detail loading fails.
      }
    }
    if (evidenceTimerRef.current) clearTimeout(evidenceTimerRef.current);
    evidenceTimerRef.current = setTimeout(() => {
      setActiveEvidenceId(undefined);
      setActiveEvidenceDetail(undefined);
      evidenceTimerRef.current = null;
    }, 5000);
  }

  function resumeEvidenceButton(ids: string[]) {
    const evidenceId = ids.find((id) => readModel.evidence_index[id]) ?? ids[0];
    if (!evidenceId || !readModel.evidence_index[evidenceId]) return null;
    return (
      <button
        type="button"
        className="inline-flex items-center gap-1 text-xs font-semibold text-blue-700 hover:text-blue-900"
        onClick={() => void showResumeEvidence(evidenceId)}
      >
        <Eye size={14} /> 查看简历依据
      </button>
    );
  }

  function evidenceForRequirement(ids: string[]) {
    const idSet = new Set(ids.filter(Boolean));
    return firstEvidence.filter((item) => idSet.has(item.requirementId));
  }

  function capabilityUpdate(ids: string[]): CapabilityUpdateView | undefined {
    const evidence = evidenceForRequirement(ids);
    const update = evidenceUpdate(evidence);
    return update ? {
      ...update,
      detail: <InterviewEvidenceNote evidence={evidence} />
    } : undefined;
  }

  function capabilityScoreUpdate(ids: string[]): CapabilityUpdateView | undefined {
    const change = activeChanges?.capabilityChanges?.find((item) => ids.includes(item.capabilityId));
    if (!change || typeof change.delta !== "number" || !Number.isFinite(change.delta)) {
      return showCapabilityScoreUpdates
        ? {
            label: "较上一版 +0.0",
            tone: "border-slate-200 bg-slate-50 text-slate-600",
            detail: <p className="mt-3 text-xs text-slate-500">本轮面评未改变该能力项的正式评分。</p>
          }
        : undefined;
    }
    const normalizedDelta = Math.abs(change.delta) < 0.05 ? 0 : change.delta;
    const direction = `${normalizedDelta >= 0 ? "+" : ""}${normalizedDelta.toFixed(1)}`;
    const previous = scoreText(change.previousScore);
    const current = scoreText(change.currentScore);
    return {
      label: `${previous} → ${current}（${direction}）`,
      tone: change.delta > 0.05
        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
        : change.delta < -0.05
          ? "border-amber-200 bg-amber-50 text-amber-800"
          : "border-slate-200 bg-slate-50 text-slate-600",
      detail: <p className="mt-3 text-xs text-slate-500">本轮面评对该能力项的正式评分变化。</p>
    };
  }

  return (
    <div>
      <PageHeader
        title={mode === "final"
          ? `${candidate.displayName} · 最终人工决策`
          : `${candidate.displayName} · ${job.title}`}
        actions={
          mode === "review" ? (
            <>
              {canRetryFirstAssessment ? (
                <Button disabled={retryingAssessment} onClick={() => void retryAssessment()}>
                  <RefreshCw size={16} />{retryingAssessment ? "正在提交" : "重新计算当前评估"}
                </Button>
              ) : null}
              {canOperate ? (
                <>
                  <Button onClick={() => void go(approveSecondInterview(applicationId))} variant="primary">
                    <CalendarPlus size={16} /> 推进
                  </Button>
                  <Button onClick={() => void go(makeHrDecision(applicationId, "不推进"))} variant="danger">
                    <XCircle size={16} /> 不推进
                  </Button>
                </>
              ) : null}
            </>
          ) : (
            <>
              {canRetrySecondAssessment ? (
                <Button disabled={retryingAssessment} onClick={() => void retryAssessment()}>
                  <RefreshCw size={16} />{retryingAssessment ? "正在提交" : "重新计算当前评估"}
                </Button>
              ) : null}
              {canOperateFinal ? (
                <>
                  <Button disabled={decisionSubmitting} onClick={() => void completeFinalDecision(
                    () => makeFinalDecision(applicationId, "offer_process"),
                  )} variant="primary">
                    <BriefcaseBusiness size={16} /> 通过
                  </Button>
                  <Button disabled={decisionSubmitting} onClick={() => void completeFinalDecision(
                    () => makeFinalDecision(applicationId, "closed_rejected"),
                  )} variant="danger">
                    <XCircle size={16} /> 不通过
                  </Button>
                </>
              ) : null}
            </>
          )
        }
      />

      {workspaceView.recoveryActions.length > 0 ? (
        <div className="mx-auto mb-4 max-w-[1600px] rounded-md border border-amber-200 bg-amber-50 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex gap-2 text-sm text-amber-900">
              <AlertTriangle className="mt-0.5 shrink-0" size={17} />
              <div><p className="font-semibold">评估需要处理</p><p className="mt-1">{recoveryMessage}</p></div>
            </div>
            <div className="flex flex-wrap gap-2">
              {canEditFeedback ? (
                <Button type="button" onClick={() => {
                  const original = mode === "final" ? workspaceView.secondOriginalRecord : workspaceView.firstOriginalRecord;
                  const raw = original?.rawNotes?.content ?? "";
                  const questions = mode === "review" && workspaceView.firstOriginalRecord
                    ? workspaceView.firstOriginalRecord.recordedQuestions
                      .filter((item) => item.answerSummary.trim() || item.interviewerNote.trim())
                      .map((item) => `${item.questionText}\n${item.answerSummary}\n${item.interviewerNote}`.trim())
                      .join("\n\n")
                    : "";
                  setRepairNotes(raw || questions);
                  setRepairEditorOpen(true);
                }}><Pencil size={16} />{recoveryAction(workspaceView.recoveryActions, mode === "final" ? "edit_second_interview_feedback" : "edit_first_interview_feedback")?.label ?? "修改面评"}</Button>
              ) : null}
              {canRebuildScreeningSource ? (
                <Button type="button" disabled={retryingAssessment} onClick={() => void (async () => {
                  setRetryingAssessment(true);
                  const result = await rebuildScreeningAssessment(applicationId);
                  setRetryingAssessment(false);
                  if (result !== undefined) await loadWorkspace();
                })()}>
                  <RefreshCw size={16} />{recoveryAction(workspaceView.recoveryActions, "rebuild_screening_assessment")?.label ?? "重新生成初步筛选依据"}
                </Button>
              ) : null}
              {canRebuildFirstAssessment ? (
                <Button type="button" disabled={retryingAssessment} onClick={() => void (async () => {
                  setRetryingAssessment(true);
                  const result = await retryPostFirstScoring(applicationId);
                  setRetryingAssessment(false);
                  if (result !== undefined) await loadWorkspace();
                })()}>
                  <RefreshCw size={16} />{recoveryAction(workspaceView.recoveryActions, "rebuild_previous_post_first_assessment")?.label ?? "重新生成上一轮评估依据"}
                </Button>
              ) : null}
            </div>
          </div>
          {repairEditorOpen ? (
            <div className="mt-4 border-t border-amber-200 pt-4">
              <label className="text-sm font-medium text-amber-950">修正后的本轮面评记录</label>
              <textarea className="mt-2 min-h-36 w-full rounded-md border border-amber-300 bg-white px-3 py-2 text-sm" value={repairNotes} onChange={(event) => setRepairNotes(event.target.value)} />
              <div className="mt-3 flex justify-end gap-2">
                <Button type="button" disabled={repairingAssessment} onClick={() => setRepairEditorOpen(false)}>取消</Button>
                <Button type="button" variant="primary" disabled={repairingAssessment || !repairNotes.trim()} onClick={() => void submitFeedbackRepair()}>
                  <RefreshCw size={16} />{repairingAssessment ? "正在提交" : "保存并重新计算"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}

      <DocumentWorkspace
        applicationId={applicationId}
        rightTitle={mode === "final" ? "最终决策" : "二面审核"}
        activeEvidence={activeEvidence}
        right={
          <div className="space-y-3">
            <RecruitmentMilestoneCard applicationId={applicationId} />
            <HardScreeningResultCard job={workspaceView.job} value={workspaceView.hardScreening} />
            <CandidateDecisionOverviewCard
              overview={workspaceView.decisionOverview}
              stage={decisionStage}
              pendingMessage={pendingAssessmentMessage}
              changes={activeChanges}
              showScoreChanges={showScoreChanges}
              onEvidenceClick={(id) => void showResumeEvidence(id)}
            />
            <NonCapabilityCard card={workspaceView.nonCapabilityCard} />

            <StageHandoffCard handoff={workspaceView.decisionSupport.stageHandoff} />

            {mode === "review" ? (
              <>
                <FirstInterviewRawNotesCard record={workspaceView.firstOriginalRecord} />
                <FirstInterviewQuestionRecordsCard record={workspaceView.firstOriginalRecord} />
              </>
            ) : (
              <InterviewOriginalRecordsSection
                first={workspaceView.firstOriginalRecord}
                second={workspaceView.secondOriginalRecord}
              />
            )}

            <Accordion title="岗位要求能力匹配情况" defaultOpen>
              <JobCapabilityMatchPanel
                requirements={jobRequirements}
                evidenceButton={resumeEvidenceButton}
                getUpdate={capabilityUpdate}
                getScoreUpdate={capabilityScoreUpdate}
              />
            </Accordion>

            {mode === "final" && workspaceView.decisionSupport.hrConditions.length > 0 ? (
              <Accordion title="HR 条件与适配结论" defaultOpen>
                <div className="space-y-2">
                  {workspaceView.decisionSupport.hrConditions.map((item) => (
                    <div key={item.conditionId} className="rounded-md border border-line bg-white p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-semibold text-ink">{item.label}</span>
                        <Badge className={item.status === "positive"
                          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                          : item.status === "neutral"
                            ? "border-blue-200 bg-blue-50 text-blue-700"
                            : "border-amber-200 bg-amber-50 text-amber-800"}
                        >
                          {item.status === "positive" ? "匹配" : item.status === "neutral" ? "部分确认" : "存在顾虑"}
                        </Badge>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-slate-700">{item.value}</p>
                    </div>
                  ))}
                </div>
              </Accordion>
            ) : null}

            {readModel.capability_performance.frameworks.length > 0 ? (
              <Accordion title="预设经历能力验证情况" defaultOpen>
                <div className="space-y-3">
                  {readModel.capability_performance.frameworks.map((framework) => {
                    const frameworkEvidence = evidenceForRequirement([framework.id, ...framework.indicators.map((item) => item.id)]);
                    const update = evidenceUpdate(frameworkEvidence);
                    return (
                      <details key={framework.id} className="group rounded-md border border-line p-3">
                        <summary className="cursor-pointer list-none">
                          <div className="flex items-center justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center justify-between gap-3 text-sm">
                                <span className="truncate font-semibold text-ink">{framework.name}</span>
                                <div className="flex shrink-0 items-center gap-2">
                                  {update ? <Badge className={update.tone}>{update.label}</Badge> : null}
                                  <span className="font-semibold text-ink">{scoreText(framework.score)}</span>
                                </div>
                              </div>
                              <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                                <div className="h-full bg-blue-600" style={{ width: `${Math.max(0, Math.min(100, framework.score))}%` }} />
                              </div>
                              <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted">{framework.conclusion}</p>
                            </div>
                            <ChevronDown size={16} className="shrink-0 text-muted transition group-open:rotate-180" />
                          </div>
                        </summary>
                        <div className="mt-3 space-y-2 border-t border-line pt-3">
                          {framework.indicators.map((indicator) => {
                            const indicatorEvidence = evidenceForRequirement([indicator.id]);
                            const indicatorUpdate = evidenceUpdate(indicatorEvidence);
                            return (
                              <div key={indicator.id} className="rounded-md bg-slate-50 p-3">
                                <div className="flex items-center justify-between gap-3 text-sm">
                                  <span className="font-medium text-ink">{indicator.name}</span>
                                  <div className="flex items-center gap-2">
                                    {indicatorUpdate ? <Badge className={indicatorUpdate.tone}>{indicatorUpdate.label}</Badge> : null}
                                    <span className="text-xs font-semibold text-slate-700">{scoreText(indicator.score)}</span>
                                  </div>
                                </div>
                                <div className="mt-2 flex items-center justify-between gap-3">
                                  <IndicatorLevelMeter level={indicator.level} score={indicator.score} description={indicator.level_description} />
                                  {resumeEvidenceButton(indicator.evidence_ids)}
                                </div>
                                <InterviewEvidenceNote evidence={indicatorEvidence} />
                              </div>
                            );
                          })}
                        </div>
                      </details>
                    );
                  })}
                </div>
              </Accordion>
            ) : null}
</div>
        }
      />
    </div>
  );
}

function Accordion({ title, children, defaultOpen = false }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  return (
    <details className="group rounded-md border border-line bg-white" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-semibold text-ink">
        {title}
        <ChevronDown size={16} className="text-muted transition group-open:rotate-180" />
      </summary>
      <div className="border-t border-line p-4">{children}</div>
    </details>
  );
}

function ScoreDelta({
  current,
  baseline,
  baselineLabel = "初步筛选结果"
}: {
  current: number | null | undefined;
  baseline: number | null | undefined;
  baselineLabel?: string;
}) {
  if (typeof current !== "number" || typeof baseline !== "number") return null;
  const delta = current - baseline;
  const normalizedDelta = Math.abs(delta) < 0.05 ? 0 : delta;
  const deltaText = `${normalizedDelta >= 0 ? "+" : ""}${normalizedDelta.toFixed(1)}`;
  return <div className="mt-1 text-xs text-muted">{baselineLabel} {scoreText(baseline)} · {deltaText}</div>;
}

function ScoreBar({ label, value, baseline }: { label: string; value: number | null; baseline?: number | null }) {
  const safeValue = value ?? 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-xs">
        <span className="font-medium text-slate-700">{label}</span>
        <div className="flex items-center gap-2">
          {baseline !== undefined ? <ScoreDelta current={value} baseline={baseline} /> : null}
          <span className="font-semibold text-ink">{scoreText(value)}</span>
        </div>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full ${value === null ? "bg-slate-300" : "bg-blue-600"}`} style={{ width: `${Math.max(0, Math.min(100, safeValue))}%` }} />
      </div>
    </div>
  );
}

function InterviewEvidenceNote({ evidence }: { evidence: InterviewEvidence[] }) {
  if (evidence.length === 0) return null;
  return (
    <div className="mt-3 rounded-md border border-violet-100 bg-violet-50 p-3 text-xs leading-5 text-violet-900">
      <span className="font-semibold">一面依据：</span>{evidence[0].text}
    </div>
  );
}

function IndicatorLevelMeter({ level, score, description }: { level: number | null; score: number; description: string }) {
  const active = level === null ? Math.max(1, Math.min(5, Math.ceil(score / 20))) : Math.max(1, Math.min(5, level));
  return (
    <div className="flex min-w-0 items-end gap-2" aria-label={description}>
      <span className="text-[11px] leading-5 text-muted">{description}</span>
      <div className="flex h-3 items-end gap-0.5">
        {[4, 6, 8, 10, 12].map((height, index) => (
          <span key={height} className={`w-1 rounded-[1px] ${index < active ? "bg-teal-600" : "bg-slate-200"}`} style={{ height }} />
        ))}
      </div>
    </div>
  );
}
