import {
  ArrowDown,
  ArrowUp,
  Download,
  Printer,
  ChevronDown,
  Eye,
  GripVertical,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
  X
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import type { CandidateDecisionOverviewView, EvidenceIndexItem } from "@/modules/assessment/contracts";
import type { InterviewPlan, InterviewQuestion } from "@/modules/interviews/contracts";
import { buildFirstInterviewPlanningReadModel, buildScreeningReviewReadModel } from "@/modules/assessment/readModelMappers";
import { useFirstInterviewPlanWorkspace } from "@/modules/interviews/hooks/useFirstInterviewPlanWorkspace";
import type { FirstInterviewPlanReadModel } from "@/modules/interviews/firstInterviewApi";
import { useRequiredApplicationId } from "@/modules/applications/hooks/useRequiredApplicationId";
import { useInterviewCommands } from "@/modules/interviews/hooks/useInterviewCommands";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { PageHeader } from "@/shared/ui/PageHeader";
import { CandidateDecisionOverviewCard } from "@/modules/assessment/components/CandidateDecisionOverviewCard";
import { ScoreExplanationButton } from "@/modules/assessment/components/ScoreExplanationButton";
import { DocumentWorkspace } from "@/modules/documents/components/DocumentWorkspace";
import { DecisionFocusCard } from "@/modules/interviews/components/DecisionSupportCards";
import { HardScreeningResultCard } from "@/modules/assessment/components/HardScreeningResultCard";
import { RecruitmentMilestoneCard } from "@/modules/applications/components/RecruitmentMilestoneCard";
import { PageLoading } from "@/shared/ui/PageLoading";
import { useToast } from "@/shared/toast/ToastProvider";
import type { RecoveryAction } from "@/shared/recovery/actions";

const priorityLabels: Record<InterviewQuestion["priority"], string> = {
  high: "高优先级",
  medium: "中优先级",
  low: "低优先级"
};

function cloneQuestion(question: InterviewQuestion): InterviewQuestion {
  return {
    ...question,
    confirmed: false,
    followUpQuestions: [...question.followUpQuestions],
    expectedEvidence: [...question.expectedEvidence],
    negativeSignals: [...question.negativeSignals],
    verificationTargetIds: [...new Set(question.verificationTargetIds)]
  };
}

function uniqueTargets(targets: InterviewPlan["targets"]): InterviewPlan["targets"] {
  return [...new Map(targets.map((target) => [target.targetId, target])).values()];
}

function prepareFormalGuide(plan: InterviewPlan, applicationId: string): InterviewPlan {
  const sourceTechnicalQuestions = plan.technicalQuestions ?? plan.questions.filter((question) => question.sectionType !== "common");
  // 新发布的草稿从空题单开始；只有已确认题单或已保存过的草稿才恢复已选技术题。
  // 兼容旧版本：它们会把 AI 建议题预填进 technicalQuestions，但没有 draftRevision。
  const restoreSelectedQuestions = plan.confirmed || Boolean(plan.draftRevision && plan.draftRevision > 0);
  const selectedQuestions = restoreSelectedQuestions
    ? sourceTechnicalQuestions.map((question) => ({
        ...cloneQuestion(question),
        sectionType: "technical" as const,
        sourceType: question.sourceType ?? "ai_suggestion",
        sourceSuggestionId: question.sourceSuggestionId ?? question.questionId,
        finalQuestionId: question.finalQuestionId ?? question.questionId,
        finalText: question.finalText ?? question.mainQuestion
      }))
    : [];
  const commonQuestions = (plan.commonQuestions ?? plan.questions.filter((question) => question.sectionType === "common"))
    .map((question) => ({ ...cloneQuestion(question), sectionType: "common" as const }));
  return {
    ...plan,
    applicationId,
    guideType: "technical_first_round",
    targets: uniqueTargets(plan.targets),
    questions: selectedQuestions,
    technicalQuestions: selectedQuestions,
    commonQuestions,
    questionCount: selectedQuestions.length,
    interviewerNotes: plan.interviewerNotes ?? "重点追问个人贡献边界、工程验证方式和异常处理过程。"
  };
}

function withQuestionCount(plan: InterviewPlan): InterviewPlan {
  return { ...plan, questionCount: plan.questions.length };
}

export function FirstInterviewPlanScreen() {
  const applicationId = useRequiredApplicationId();
  const navigate = useNavigate();
  const toast = useToast();
  const { token, runFirstInterviewPlanning, continueFirstInterviewManually, saveFirstInterviewPlanDraft, confirmFirstInterviewPlan, loadEvidence, previewGuide, downloadGuide } = useInterviewCommands();
  const planningRequested = useRef(false);
  const { workspace, loading, loadError, reload: loadWorkspace } = useFirstInterviewPlanWorkspace(token, applicationId);
  const context = workspace;
  const sourcePlan = workspace?.plan;
  const [editablePlan, setEditablePlan] = useState<InterviewPlan>();
  const [savedPlan, setSavedPlan] = useState<InterviewPlan>();
  const [draggedQuestionId, setDraggedQuestionId] = useState<string>();
  const [activeEvidenceId, setActiveEvidenceId] = useState<string>();
  const [activeEvidenceDetail, setActiveEvidenceDetail] = useState<EvidenceIndexItem>();
  const [screeningReviewOpen, setScreeningReviewOpen] = useState(false);

  useEffect(() => {
    planningRequested.current = false;
  }, [applicationId]);

  useEffect(() => {
    const planningStatus = context?.planningState?.status;
    if (
      !sourcePlan
      && context?.application.status === "first_interview_planning"
      && (!planningStatus || planningStatus === "not_started")
      && !planningRequested.current
    ) {
      planningRequested.current = true;
      void runFirstInterviewPlanning(applicationId).then(loadWorkspace);
      return;
    }
    if (sourcePlan) {
      const formalGuide = prepareFormalGuide(sourcePlan, applicationId);
      setEditablePlan((current) => current?.planId === sourcePlan.planId && current.applicationId === applicationId ? current : formalGuide);
      setSavedPlan((current) => current?.planId === sourcePlan.planId && current.applicationId === applicationId ? current : formalGuide);
    }
  }, [applicationId, context?.application.status, context?.planningState?.status, loadWorkspace, sourcePlan, runFirstInterviewPlanning]);

  const readModel = useMemo(() => {
    if (!context || !sourcePlan || !editablePlan || !context.screening.screeningResultView) return undefined;
    return buildFirstInterviewPlanningReadModel(context.screening, sourcePlan, editablePlan);
  }, [context, sourcePlan, editablePlan]);
  const screeningReadModel = useMemo(() => {
    if (!context?.screening.screeningResultView) return undefined;
    return buildScreeningReviewReadModel(context.screening);
  }, [context?.screening]);

  if (loadError) return <PageHeader title="一面题单加载失败" description={loadError} actions={<Button onClick={() => void loadWorkspace()}>重新加载</Button>} />;
  if (loading || !context) return <PageLoading label="正在加载一面题单" />;

  const { application, candidate } = context;
  const evidenceIndex = screeningReadModel?.evidence_index ?? {};
  const activeEvidence = activeEvidenceDetail ?? (activeEvidenceId ? evidenceIndex[activeEvidenceId] : undefined);

  async function selectEvidence(evidenceId: string) {
    setActiveEvidenceId(evidenceId);
    setActiveEvidenceDetail(evidenceIndex[evidenceId]);
    if (!token) return;
    try {
      setActiveEvidenceDetail(await loadEvidence(applicationId, evidenceId));
    } catch {
      // Keep the evidence summary visible if detail loading fails.
    }
  }

  async function retryPlanning() {
    planningRequested.current = true;
    await runFirstInterviewPlanning(applicationId);
    await loadWorkspace();
  }

  async function continueManually() {
    const redirect = await continueFirstInterviewManually(applicationId);
    await loadWorkspace();
    if (redirect) navigate(redirect);
  }

  if (!sourcePlan || !editablePlan || !readModel) {
    return (
      <>
        <PlanningPendingView
          candidateName={candidate.displayName}
          jobTitle={context.job.title ?? "岗位"}
          applicationId={applicationId}
          decisionOverview={context.decisionOverview}
          activeEvidence={activeEvidence}
          planningState={context.planningState}
          recoveryActions={context.recoveryActions}
          onEvidenceClick={(evidenceId) => void selectEvidence(evidenceId)}
          onRetry={() => void retryPlanning()}
          onContinueManually={() => void continueManually()}
          onReviewScreening={() => setScreeningReviewOpen(true)}
        />
        {screeningReviewOpen ? (
          <ScreeningSourceDialog
            available={Boolean(screeningReadModel)}
            overview={context.decisionOverview}
            message={context.planningState?.recoveryMessage}
            onClose={() => setScreeningReviewOpen(false)}
          />
        ) : null}
      </>
    );
  }

  const currentPlan = editablePlan;
  const canOperate = workspace.availableActions.some((action) =>
    ["run_first_interview_planning", "confirm_first_guide"]
      .includes(action)
  );
  const isPlanning = application.status === "first_interview_planning";
  const commonQuestions = currentPlan.commonQuestions ?? [];
  const commonTemplateMissing = currentPlan.commonTemplateStatus === "missing" || !currentPlan.commonTemplate;
  const hasInvalidQuestions = currentPlan.questions.some((question) => !(question.finalText ?? question.mainQuestion).trim());
  const hasUnsavedChanges = savedPlan ? JSON.stringify(currentPlan) !== JSON.stringify(savedPlan) : false;

  async function handlePreviewGuide() {
    try {
      await previewGuide(applicationId, isPlanning);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : "打印预览失败，请稍后重试");
    }
  }

  async function handleDownloadGuide() {
    try {
      await downloadGuide(applicationId, isPlanning);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : "导出 PDF 失败，请稍后重试");
    }
  }

  function patchPlan(patch: Partial<InterviewPlan>) {
    setEditablePlan((current) => current ? withQuestionCount({ ...current, ...patch }) : current);
  }

  function patchQuestion(questionId: string, patch: Partial<InterviewQuestion>) {
    patchPlan({ questions: currentPlan.questions.map((question) => question.questionId === questionId ? { ...question, ...patch } : question) });
  }

  function addSuggestedQuestion(question: InterviewQuestion) {
    if (currentPlan.questions.some((item) => item.sourceSuggestionId === question.questionId)) return;
    const finalQuestionId = `FQ_${question.questionId}`;
    patchPlan({
      questions: [
        ...currentPlan.questions,
        {
          ...cloneQuestion(question),
          questionId: finalQuestionId,
          finalQuestionId,
          finalText: question.mainQuestion,
          sourceType: "ai_suggestion",
          sourceSuggestionId: question.questionId,
          isEdited: false
        }
      ]
    });
  }

  function addCustomQuestion() {
    const finalQuestionId = `FQ_CUSTOM_${Date.now()}`;
    patchPlan({
      questions: [
        ...currentPlan.questions,
        {
          questionId: finalQuestionId,
          finalQuestionId,
          sourceType: "interviewer_custom",
          finalText: "",
          isEdited: false,
          verificationTargetIds: [],
          mainQuestion: "",
          followUpQuestions: [],
          expectedEvidence: [],
          negativeSignals: [],
          priority: "medium",
          confirmed: false
        }
      ]
    });
  }

  function removeQuestion(questionId: string) {
    patchPlan({ questions: currentPlan.questions.filter((question) => question.questionId !== questionId) });
  }

  function moveQuestion(questionId: string, direction: -1 | 1) {
    const index = currentPlan.questions.findIndex((question) => question.questionId === questionId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= currentPlan.questions.length) return;
    const questions = [...currentPlan.questions];
    const [item] = questions.splice(index, 1);
    questions.splice(nextIndex, 0, item);
    patchPlan({ questions });
  }

  function dropQuestion(targetId: string) {
    if (!draggedQuestionId || draggedQuestionId === targetId) return;
    const questions = [...currentPlan.questions];
    const from = questions.findIndex((item) => item.questionId === draggedQuestionId);
    const to = questions.findIndex((item) => item.questionId === targetId);
    if (from < 0 || to < 0) return;
    const [item] = questions.splice(from, 1);
    questions.splice(to, 0, item);
    patchPlan({ questions });
    setDraggedQuestionId(undefined);
  }

  function toggleQuestionTarget(questionId: string, targetId: string) {
    const question = currentPlan.questions.find((item) => item.questionId === questionId);
    if (!question) return;
    const exists = question.verificationTargetIds.includes(targetId);
    patchQuestion(questionId, {
      verificationTargetIds: exists ? question.verificationTargetIds.filter((id) => id !== targetId) : [...question.verificationTargetIds, targetId]
    });
  }

  async function saveDraft() {
    const saved = await saveFirstInterviewPlanDraft(applicationId, currentPlan);
    if (saved) setSavedPlan(structuredClone(currentPlan));
  }

  function undoChanges() {
    if (savedPlan) setEditablePlan(structuredClone(savedPlan));
  }

  async function confirm() {
    const finalized = {
      ...currentPlan,
      technicalQuestions: currentPlan.questions.map((question) => ({
        ...question,
        sectionType: "technical" as const,
        mainQuestion: question.finalText ?? question.mainQuestion,
        finalQuestionId: question.finalQuestionId ?? question.questionId
      })),
      questions: currentPlan.questions
    };
    const redirect = await confirmFirstInterviewPlan(applicationId, finalized);
    if (redirect) await loadWorkspace();
    if (redirect) navigate(redirect);
  }

  return (
    <>
      <PageHeader
        title={`${candidate.displayName} · 一面题单确认`}
        actions={
          <>
            <Button onClick={() => void handlePreviewGuide()}>
              <Printer size={16} /> 打印预览
            </Button>
            <Button onClick={() => void handleDownloadGuide()}>
              <Download size={16} /> 导出 PDF
            </Button>
            {isPlanning && canOperate ? (
              <>
                <Button disabled={!hasUnsavedChanges} onClick={undoChanges}>
                  <RotateCcw size={16} /> 撤销未保存修改
                </Button>
                <Button onClick={() => void saveDraft()}>
                  <Save size={16} /> 保存草稿
                </Button>
                <Button disabled={hasInvalidQuestions} onClick={() => void confirm()} variant="primary">
                  <Save size={16} /> 确认题单
                </Button>
              </>
            ) : null}

            {isPlanning ? workspace.recoveryActions.map((action) => (
              <Button
                key={action.action}
                type="button"
                onClick={() => {
                  if (action.action === "run_first_interview_planning") void retryPlanning();
                  else if (action.action === "continue_first_interview_manually") void continueManually();
                  else setScreeningReviewOpen(true);
                }}
              >
                {action.action === "run_first_interview_planning" ? <RotateCcw size={16} /> : null}
                {action.label}
              </Button>
            )) : null}

          </>
        }
      />
      <DocumentWorkspace
        applicationId={applicationId}
        leftTitle="AI 建议题与验证重点"
        rightTitle="正式一面题单"
        activeEvidence={activeEvidence}
        left={
          <div className="space-y-5">
            <RecruitmentMilestoneCard applicationId={applicationId} />
            <HardScreeningResultCard job={context.job} value={context.hardScreening} />
<CandidateDecisionOverviewCard
              overview={context.decisionOverview}
              stage="screening"
              onEvidenceClick={(evidenceId) => void selectEvidence(evidenceId)}
            />

            {activeEvidence ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                  <div className="font-semibold">简历原文</div>
                <div className="mt-1 leading-6">{activeEvidence.rawText || "原文位置已记录，可返回审核页在简历中定位。"}</div>
              </div>
            ) : null}



            <Panel title="AI 建议题单">
              <div className="space-y-3">
                {readModel.question_suggestions.map((question) => {
                  const added = currentPlan.questions.some((item) => item.sourceSuggestionId === question.questionId);
                  return (
                    <article key={question.questionId} className="rounded-md border border-line p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge className="border-blue-200 bg-blue-50 text-blue-700">推荐度：{priorityLabels[question.recommendation ?? question.priority]}</Badge>
                        <span className="text-xs text-muted">验证目标：{question.validationGoal || question.verificationTargetIds.join("、")}</span>
                      </div>
                      <div className="mt-3 font-medium leading-7 text-ink">{question.mainQuestion}</div>
                      <div className="mt-3 text-sm text-slate-700">希望确认：{question.expectedEvidence.join("、") || "具体事实、个人动作与验证结果"}</div>
                      {question.followUpQuestions.length > 0 ? (
                        <details className="group mt-3 rounded-md bg-slate-50 p-3">
                          <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium text-slate-700">
                            追问建议 <ChevronDown size={15} className="transition group-open:rotate-180" />
                          </summary>
                          <ul className="mt-2 space-y-2 text-sm leading-6 text-slate-700">
                            {question.followUpQuestions.map((followUp) => <li key={followUp}>· {followUp}</li>)}
                          </ul>
                        </details>
                      ) : null}
                      <div className="mt-3 flex justify-end">
                        {isPlanning && canOperate ? (
                          <Button disabled={added} onClick={() => addSuggestedQuestion(question)}>
                            <Plus size={16} /> {added ? "已加入" : "加入题单"}
                          </Button>
                        ) : null}
                      </div>
                    </article>
                  );
                })}
              </div>
            </Panel>
          </div>
        }
        right={
          <div className="space-y-5">
            <Panel title="技术题单" action={isPlanning && canOperate ? <Button onClick={addCustomQuestion}><Plus size={16} /> 新增自定义</Button> : undefined}>
              {currentPlan.questions.length === 0 ? (
                <div className="rounded-md border border-dashed border-line p-8 text-center text-sm text-muted">从左侧加入 AI 建议题，或新增自定义题。</div>
              ) : (
                <div className="space-y-3">
                  {currentPlan.questions.map((question, index) => (
                    <QuestionEditor
                      key={question.questionId}
                      question={question}
                      index={index}
                      total={currentPlan.questions.length}
                      targets={currentPlan.targets}
                      disabled={!isPlanning || !canOperate}
                      onPatch={(patch) => patchQuestion(question.questionId, patch)}
                      onRemove={() => removeQuestion(question.questionId)}
                      onMove={(direction) => moveQuestion(question.questionId, direction)}
                      onToggleTarget={(targetId) => toggleQuestionTarget(question.questionId, targetId)}
                      onDragStart={(event) => { setDraggedQuestionId(question.questionId); event.dataTransfer.effectAllowed = "move"; }}
                      onDrop={() => dropQuestion(question.questionId)}
                    />
                  ))}
                </div>
              )}
            </Panel>

            <Panel title={`通用题单${currentPlan.commonTemplate ? ` · ${currentPlan.commonTemplate.name}` : ""}`}>
              {commonTemplateMissing ? (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
                  当前岗位未配置通用题单，本次仍可直接确认，正式题单将只包含技术题。
                </div>
              ) : (
                <div className="space-y-3">
                  {commonQuestions.map((question, index) => (
                    <article key={question.questionId} className="rounded-md border border-line bg-slate-50 p-4">
                      <div className="flex items-start gap-3">
                        <Badge className="shrink-0 border-violet-200 bg-violet-50 text-violet-700">通用题 {index + 1}</Badge>
                        <div className="min-w-0">
                          <div className="font-medium leading-6 text-ink">{question.finalText ?? question.mainQuestion}</div>
                          {(question.evaluationPoints ?? question.expectedEvidence).length > 0 ? <div className="mt-2 text-xs leading-5 text-muted">考察要点：{(question.evaluationPoints ?? question.expectedEvidence).join("、")}</div> : null}
                          <div className="mt-2 text-xs text-muted">{question.resultType === "non_scoring" ? "记录为非能力信息，不直接改变能力分" : "可形成能力评价证据"}</div>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </Panel>

            <Panel title="覆盖情况（仅供参考）">
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-slate-700">覆盖 {readModel.coverage_check.covered}/{readModel.coverage_check.total}</span>
                  <span className="text-muted">{readModel.coverage_check.total ? Math.round(readModel.coverage_check.covered / readModel.coverage_check.total * 100) : 0}%</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                  <div className="h-full bg-emerald-500" style={{ width: `${readModel.coverage_check.total ? readModel.coverage_check.covered / readModel.coverage_check.total * 100 : 0}%` }} />
                </div>
                <QualityAlerts readModel={readModel.coverage_check} />
            </Panel>
          </div>
        }
      />
      {screeningReviewOpen ? (
        <ScreeningSourceDialog
          available={Boolean(screeningReadModel)}
          overview={context.decisionOverview}
          message={context.planningState?.recoveryMessage}
          onClose={() => setScreeningReviewOpen(false)}
        />
      ) : null}
    </>
  );
}

function PlanningPendingView({
  candidateName,
  jobTitle,
  applicationId,
  decisionOverview,
  activeEvidence,
  planningState,
  recoveryActions,
  onEvidenceClick,
  onRetry,
  onContinueManually,
  onReviewScreening
}: {
  candidateName: string;
  jobTitle: string;
  applicationId: string;
  decisionOverview: CandidateDecisionOverviewView;
  activeEvidence?: EvidenceIndexItem;
  planningState?: FirstInterviewPlanReadModel["planningState"];
  recoveryActions: RecoveryAction[];
  onEvidenceClick: (evidenceId: string) => void;
  onRetry: () => void;
  onContinueManually: () => void;
  onReviewScreening: () => void;
}) {
  const status = planningState?.status ?? "not_started";
  const needsRecovery = recoveryActions.length > 0;
  const statusText = {
    not_started: "正在提交题单生成任务",
    pending: "题单任务正在排队",
    running: "正在生成个性化面试问题",
    ready: "题单生成完成",
    failed: "个性化题单生成未完成",
    blocked: "题单生成待确认"
  }[status];
  return (
    <>
      <PageHeader
        title={`${candidateName} · ${jobTitle} · 一面题单确认`}
        description={statusText}
        actions={needsRecovery ? (
          <>
            {recoveryActions.map((action) => (
              <Button
                key={action.action}
                type="button"
                onClick={
                  action.action === "run_first_interview_planning"
                    ? onRetry
                    : action.action === "continue_first_interview_manually"
                      ? onContinueManually
                      : onReviewScreening
                }
                variant={action.action === "continue_first_interview_manually" ? "primary" : undefined}
              >
                {action.action === "run_first_interview_planning" ? <RotateCcw size={16} /> : null}
                {action.label}
              </Button>
            ))}
          </>
        ) : undefined}
      />
      <DocumentWorkspace
        applicationId={applicationId}
        leftTitle="初步筛选结论与验证重点"
        rightTitle="一面题单"
        activeEvidence={activeEvidence}
        left={
          <div className="space-y-5">
<CandidateDecisionOverviewCard
              overview={decisionOverview}
              stage="screening"
              onEvidenceClick={onEvidenceClick}
            />

            {activeEvidence ? (
              <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                  <div className="font-semibold">简历原文</div>
                <div className="mt-1 leading-6">{activeEvidence.rawText || "原文位置已记录，可在简历中定位。"}</div>
              </div>
            ) : null}


          </div>
        }
        right={
          <div className="space-y-5">
            <Panel title="AI 建议题单">
              {needsRecovery ? (
                <div className="rounded-md border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                  <div className="font-semibold">题单生成需要处理</div>
                  <p className="mt-1 leading-6">{planningState?.recoveryMessage || planningState?.error || "后台任务未能生成有效题单，请选择页面提供的处理方式。"}</p>
                </div>
              ) : (
                <div className="space-y-3" aria-label={statusText}>
                  {[0, 1, 2].map((index) => <QuestionSkeleton key={index} />)}
                </div>
              )}
            </Panel>

            <Panel title="面试官题单">
              <div className="rounded-md border border-dashed border-line p-8 text-center text-sm text-muted">
                AI建议题生成完成后，可选择、编辑、排序或新增自定义题目。
              </div>
            </Panel>

            <Panel title="覆盖情况">
              <div className="flex items-center gap-2 text-sm text-muted">
                <RefreshCw className={needsRecovery ? "" : "animate-spin"} size={16} />
                {needsRecovery ? "处理后将重新计算覆盖情况" : "题单生成后自动检查验证目标覆盖情况"}
              </div>
            </Panel>
          </div>
        }
      />
    </>
  );
}

function ScreeningSourceDialog({
  available,
  overview,
  message,
  onClose,
}: {
  available: boolean;
  overview: CandidateDecisionOverviewView;
  message?: string | null;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/40 p-4" role="dialog" aria-modal="true" aria-labelledby="screening-source-dialog-title">
      <div className="flex max-h-[88vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-start justify-between gap-3 border-b border-line p-5">
          <div>
            <h2 id="screening-source-dialog-title" className="font-semibold text-ink">初步筛选结果与题单来源</h2>
            <p className="mt-1 text-sm text-muted">查看本次题单使用的初步筛选结论和待核验重点。</p>
          </div>
          <button type="button" className="rounded-md p-2 text-muted hover:bg-slate-100" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-5">
          {available ? (
            <CandidateDecisionOverviewCard overview={overview} stage="screening" />
          ) : (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm leading-6 text-amber-900">
              {message || "当前没有可用的正式初步筛选结果，题单无法自动绑定来源。"}
            </div>
          )}
        </div>
        <div className="flex justify-end border-t border-line p-4"><Button onClick={onClose}>关闭</Button></div>
      </div>
    </div>
  );
}

function QuestionSkeleton() {
  return (
    <div className="animate-pulse rounded-md border border-line p-4">
      <div className="h-4 w-24 rounded bg-slate-200" />
      <div className="mt-4 h-4 w-full rounded bg-slate-200" />
      <div className="mt-2 h-4 w-4/5 rounded bg-slate-100" />
      <div className="mt-4 h-3 w-2/3 rounded bg-slate-100" />
    </div>
  );
}

function Accordion({ title, children }: { title: string; children: ReactNode }) {
  return <details className="group rounded-md border border-line"><summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold text-ink">{title}<ChevronDown size={16} className="transition group-open:rotate-180" /></summary><div className="border-t border-line p-4">{children}</div></details>;
}

function Panel({ title, children, action, unframed = false }: { title: string; children: ReactNode; action?: ReactNode; unframed?: boolean }) {
  return <section className={unframed ? "" : "rounded-md border border-line p-4"}><div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-ink">{title}</h2>{action}</div>{children}</section>;
}

function EvidenceButton({ ids, evidenceIndex, onSelect }: { ids: string[]; evidenceIndex: Record<string, { evidenceId: string }>; onSelect: (id: string) => void }) {
  const id = ids.find((item) => evidenceIndex[item]) ?? ids[0];
  return id ? <button type="button" onClick={() => onSelect(id)} className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-blue-700"><Eye size={14} /> 查看依据</button> : null;
}

function QuestionEditor({ question, index, total, targets, disabled, onPatch, onRemove, onMove, onToggleTarget, onDragStart, onDrop }: {
  question: InterviewQuestion;
  index: number;
  total: number;
  targets: InterviewPlan["targets"];
  disabled: boolean;
  onPatch: (patch: Partial<InterviewQuestion>) => void;
  onRemove: () => void;
  onMove: (direction: -1 | 1) => void;
  onToggleTarget: (targetId: string) => void;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
  onDrop: () => void;
}) {
  const [editing, setEditing] = useState(!question.finalText);
  const sourceLabel = question.sourceType === "interviewer_custom" ? "自定义" : "AI 建议";
  return (
    <article draggable={!disabled} onDragStart={onDragStart} onDragOver={(event) => event.preventDefault()} onDrop={onDrop} className="rounded-md border border-line p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <GripVertical size={17} className="text-slate-400" />
          <Badge className="border-slate-200 bg-slate-50 text-slate-700">Q{index + 1}</Badge>
          <Badge className={question.sourceType === "interviewer_custom" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-blue-200 bg-blue-50 text-blue-700"}>{sourceLabel}</Badge>
          {question.isEdited ? <span className="text-xs text-muted">已编辑</span> : null}
        </div>
        <div className="flex gap-1">
          <IconButton label="上移" disabled={disabled || index === 0} onClick={() => onMove(-1)}><ArrowUp size={14} /></IconButton>
          <IconButton label="下移" disabled={disabled || index === total - 1} onClick={() => onMove(1)}><ArrowDown size={14} /></IconButton>
          <IconButton label="编辑" disabled={disabled} onClick={() => setEditing((value) => !value)}><Pencil size={14} /></IconButton>
          <IconButton label="删除" disabled={disabled} onClick={onRemove} danger><Trash2 size={14} /></IconButton>
        </div>
      </div>
      {editing ? (
        <textarea className="mt-3 min-h-24 w-full rounded-md border border-line px-3 py-2 text-sm leading-6" value={question.finalText ?? question.mainQuestion} disabled={disabled} onChange={(event) => onPatch({ finalText: event.target.value, mainQuestion: event.target.value, isEdited: question.sourceType === "ai_suggestion" ? true : question.isEdited })} />
      ) : <p className="mt-3 text-sm leading-6 text-slate-800">{question.finalText ?? question.mainQuestion}</p>}
      <details className="group mt-3 rounded-md bg-slate-50 p-3">
        <summary className="flex cursor-pointer list-none items-center justify-between text-xs font-semibold text-slate-700">关联验证目标<ChevronDown size={14} className="transition group-open:rotate-180" /></summary>
        <div className="mt-2 space-y-2">
          {targets.map((target) => <label key={target.targetId} className="flex items-start gap-2 text-xs text-slate-700"><input type="checkbox" className="mt-0.5" checked={question.verificationTargetIds.includes(target.targetId)} disabled={disabled} onChange={() => onToggleTarget(target.targetId)} /><span>{target.title}</span></label>)}
        </div>
      </details>
    </article>
  );
}

function IconButton({ label, children, disabled, onClick, danger = false }: { label: string; children: ReactNode; disabled: boolean; onClick: () => void; danger?: boolean }) {
  return <button type="button" title={label} aria-label={label} disabled={disabled} onClick={onClick} className={`inline-flex h-8 w-8 items-center justify-center rounded-md border disabled:opacity-40 ${danger ? "border-rose-200 text-rose-700 hover:bg-rose-50" : "border-line text-slate-600 hover:bg-slate-50"}`}>{children}</button>;
}

function QualityAlerts({ readModel }: { readModel: ReturnType<typeof buildFirstInterviewPlanningReadModel>["coverage_check"] }) {
  const alerts = [
    readModel.uncovered.length ? `未覆盖：${readModel.uncovered.join("、")}` : "",
    readModel.duplicate_question_ids.length ? `重复题目：${readModel.duplicate_question_ids.length} 题` : "",
    readModel.unlinked_question_ids.length ? `未关联验证目标：${readModel.unlinked_question_ids.length} 题` : "",
    readModel.concentration_warning ?? ""
  ].filter(Boolean);
  return alerts.length ? <div className="mt-3 space-y-2">{alerts.map((alert) => <div key={alert} className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">{alert}</div>)}</div> : <div className="mt-3 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">覆盖与题单质量检查通过</div>;
}
