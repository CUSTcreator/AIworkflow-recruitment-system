import { CheckCircle2, ChevronDown, Download, Printer, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type {
  FirstInterviewProgressDraft,
  InterviewPlan,
  QuestionResponse
} from "@/modules/interviews/contracts";
import { buildFirstInterviewWorkspaceReadModel } from "@/modules/assessment/readModelMappers";
import { useInterviewCommands } from "@/modules/interviews/hooks/useInterviewCommands";
import { Badge } from "@/shared/ui/Badge";
import { Button } from "@/shared/ui/Button";
import { ScoreExplanationButton } from "@/modules/assessment/components/ScoreExplanationButton";
import { PageHeader } from "@/shared/ui/PageHeader";
import { CandidateDecisionOverviewCard } from "@/modules/assessment/components/CandidateDecisionOverviewCard";
import { RecruitmentMilestoneCard } from "@/modules/applications/components/RecruitmentMilestoneCard";
import { HardScreeningResultCard } from "@/modules/assessment/components/HardScreeningResultCard";
import { DocumentWorkspace } from "@/modules/documents/components/DocumentWorkspace";
import { useRequiredApplicationId } from "@/modules/applications/hooks/useRequiredApplicationId";
import { InterviewTimeDialog } from "@/modules/interviews/components/InterviewTimeDialog";
import {
  AssessmentCoverageCard,
  DecisionFocusCard
} from "@/modules/interviews/components/DecisionSupportCards";
import { useDebouncedSave } from "@/shared/hooks/useDebouncedSave";
import { useFirstInterviewWorkspaceReadModel } from "@/modules/interviews/hooks/useInterviewPageReadModels";
import { PageLoading } from "@/shared/ui/PageLoading";
import { useToast } from "@/shared/toast/ToastProvider";
import { workspaceReturnPath } from "@/shared/navigation/workspaceReturn";

interface OptionalQuestionNote {
  answerSummary: string;
  interviewerNote: string;
}

type SaveNotice = { kind: "saved" | "error"; message: string };

function questionNotesFrom(plan: InterviewPlan, progress?: FirstInterviewProgressDraft): Record<string, OptionalQuestionNote> {
  return Object.fromEntries(
    plan.questions.map((question) => {
      const response = progress?.questionResponses.find((item) => item.questionId === question.questionId);
      return [
        question.questionId,
        {
          answerSummary: response?.answerSummary ?? "",
          interviewerNote: response?.interviewerNote ?? ""
        }
      ];
    })
  );
}

function progressPayload(
  applicationId: string,
  plan: InterviewPlan,
  rawNotes: string,
  questionNotes: Record<string, OptionalQuestionNote>,
  progress?: FirstInterviewProgressDraft
): FirstInterviewProgressDraft {
  const questionResponses: QuestionResponse[] = plan.questions.map((question) => ({
    questionResponseId: progress?.questionResponses.find((item) => item.questionId === question.questionId)?.questionResponseId ?? "",
    applicationId,
    interviewId: `INT_${applicationId}_FIRST`,
    interviewRound: "first",
    questionId: question.questionId,
    answerSummary: questionNotes[question.questionId]?.answerSummary ?? "",
    interviewerNote: questionNotes[question.questionId]?.interviewerNote ?? "",
    confirmedByInterviewer: true
  }));

  return {
    progressDraftId: progress?.progressDraftId,
    applicationId,
    guideId: plan.planId,
    rawNotes,
    questionResponses,
    verificationTargetStatuses: progress?.verificationTargetStatuses ?? {},
    createdAt: progress?.createdAt,
    updatedAt: progress?.updatedAt
  };
}

export function FirstInterviewWorkspaceScreen() {
  const applicationId = useRequiredApplicationId();
  const navigate = useNavigate();
  const location = useLocation();
  const returnPath = workspaceReturnPath(location.state);
  const toast = useToast();
  const { saveFirstInterviewProgress, completeFirstInterview, token, previewGuide, downloadGuide } = useInterviewCommands();
  const [rawNotes, setRawNotes] = useState("");
  const [questionNotes, setQuestionNotes] = useState<Record<string, OptionalQuestionNote>>({});
  const [hydrated, setHydrated] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saveNotice, setSaveNotice] = useState<SaveNotice>();
  const [pendingDecision, setPendingDecision] = useState<"pass" | "reject">();
  const hydratedSource = useRef("");
  const lastSavedPayload = useRef("");
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data: workspace, loading, error: loadError, reload: loadWorkspace } = useFirstInterviewWorkspaceReadModel(applicationId);

  const context = workspace;
  const plan = workspace?.plan;
  const progress = workspace?.progressDraft;
  const canOperate = Boolean(
    context?.availableActions.includes("save_first_interview_progress")
  );

  useEffect(() => {
    if (!plan) return;
    const sourceKey = `${applicationId}:${plan.planId}:${progress?.progressDraftId ?? "new"}`;
    if (hydratedSource.current === sourceKey) return;
    const notes = questionNotesFrom(plan, progress);
    const notesText = progress?.rawNotes ?? "";
    setHydrated(false);
    setRawNotes(notesText);
    setQuestionNotes(notes);
    lastSavedPayload.current = JSON.stringify(progressPayload(applicationId, plan, notesText, notes, progress));
    hydratedSource.current = sourceKey;
    setHydrated(true);
  }, [applicationId, plan, progress]);

  useEffect(() => () => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
  }, []);

  const draft = useMemo(
    () => plan ? progressPayload(applicationId, plan, rawNotes, questionNotes, progress) : undefined,
    [applicationId, plan, progress, questionNotes, rawNotes]
  );

  const showSavedNotice = useCallback(() => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setSaveNotice({ kind: "saved", message: "草稿已自动保存" });
    noticeTimer.current = setTimeout(() => setSaveNotice(undefined), 1500);
  }, []);

  const persistDraft = useCallback(async (value: FirstInterviewProgressDraft) => {
    const saved = await saveFirstInterviewProgress(applicationId, value, { silent: true });
    if (!saved) {
      setSaveNotice({ kind: "error", message: "自动保存失败，请检查网络后重试" });
      throw new Error("一面草稿保存失败");
    }
    lastSavedPayload.current = JSON.stringify(value);
    showSavedNotice();
  }, [applicationId, saveFirstInterviewProgress, showSavedNotice]);
  const {
    schedule: scheduleDraftSave,
    cancel: cancelDraftSave
  } = useDebouncedSave(persistDraft, 800);

  useEffect(() => {
    if (!hydrated || !canOperate || !draft || submitting) return;
    const serialized = JSON.stringify(draft);
    if (serialized === lastSavedPayload.current) return;
    scheduleDraftSave(draft);
  }, [canOperate, draft, hydrated, scheduleDraftSave, submitting]);

  if (loading) return <PageLoading label="正在加载一面工作台" />;
  if (loadError) return <PageHeader title="一面工作台加载失败" description={loadError} actions={<Button onClick={() => void loadWorkspace()}>重新加载</Button>} />;
  if (!context || !plan || !draft) {
    return <PageHeader title="未找到一面工作任务" description="请先确认一面题单。" />;
  }

  async function handlePreviewGuide() {
    try {
      await previewGuide(applicationId);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : "打印预览失败，请稍后重试");
    }
  }

  async function handleDownloadGuide() {
    try {
      await downloadGuide(applicationId);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : "导出 PDF 失败，请稍后重试");
    }
  }

  const activeDraft = draft;
  const targetLookup = new Map(plan.targets.map((target) => [target.targetId, target]));
  const recordedQuestionCount = Object.values(questionNotes).filter(
    (note) => note.answerSummary.trim() || note.interviewerNote.trim()
  ).length;

  function updateQuestionNote(questionId: string, patch: Partial<OptionalQuestionNote>) {
    setQuestionNotes((current) => ({
      ...current,
      [questionId]: { ...current[questionId], ...patch }
    }));
  }

  async function saveBeforeDecision(): Promise<boolean> {
    cancelDraftSave();
    const saved = await saveFirstInterviewProgress(applicationId, activeDraft, { silent: true });
    if (saved) {
      lastSavedPayload.current = JSON.stringify(activeDraft);
      setSaveNotice(undefined);
    } else {
      setSaveNotice({ kind: "error", message: "记录保存失败，暂时不能提交一面决定" });
    }
    return saved;
  }

  function requestDecision(decision: "pass" | "reject") {
    setPendingDecision(decision);
  }

  async function decide(decision: "pass" | "reject", effectiveAt: string) {
    if (submitting) return;
    setSubmitting(true);
    const saved = await saveBeforeDecision();
    if (!saved) {
      setSubmitting(false);
      return;
    }
    const result = await completeFirstInterview(applicationId, {
      ...activeDraft,
      decision,
      effectiveAt,
      timezone: "Asia/Shanghai"
    });
    if (result === undefined) {
      setSubmitting(false);
      return;
    }
    navigate(returnPath, { replace: true });
  }

  return (
    <>
      {saveNotice ? (
        <div
          role="status"
          className={`fixed right-4 top-16 z-[70] rounded-md border px-4 py-2 text-sm font-medium shadow-lg ${
            saveNotice.kind === "saved"
              ? "border-emerald-200 bg-emerald-50 text-emerald-700"
              : "border-rose-200 bg-rose-50 text-rose-700"
          }`}
        >
          {saveNotice.message}
        </div>
      ) : null}

      <PageHeader
        eyebrow="技术一面工作台"
        title={`${context.candidate.displayName} · 一面记录与面评`}
        description="左侧查看候选人初步筛选评估与验证重点，右侧记录面试事实并直接作出一面决定。"
        actions={
          <>
            <Button disabled={!token || submitting} onClick={() => void handlePreviewGuide()}>
              <Printer size={16} /> 打印预览
            </Button>
            <Button disabled={!token || submitting} onClick={() => void handleDownloadGuide()}>
              <Download size={16} /> 导出 PDF
            </Button>
            {canOperate ? (
              <>
                <Button disabled={submitting} onClick={() => requestDecision("pass")} variant="primary">
                  <CheckCircle2 size={16} /> 一面通过
                </Button>
                <Button disabled={submitting} onClick={() => requestDecision("reject")} variant="danger">
                  <XCircle size={16} /> 一面不通过
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <InterviewTimeDialog
        open={Boolean(pendingDecision)}
        title={pendingDecision === "pass" ? "确认一面通过" : "确认一面不通过"}
        description="请填写本次一面实际进行的时间。"
        confirmLabel={pendingDecision === "pass" ? "确认通过" : "确认不通过"}
        variant={pendingDecision === "reject" ? "danger" : "primary"}
        onClose={() => setPendingDecision(undefined)}
        onConfirm={async (effectiveAt) => {
          if (pendingDecision) await decide(pendingDecision, effectiveAt);
          setPendingDecision(undefined);
        }}
      />
      <DocumentWorkspace
        applicationId={applicationId}
        leftTitle="候选人评估与面试重点"
        rightTitle="一面面试记录"
        left={
          <div className="space-y-5">
            <RecruitmentMilestoneCard applicationId={applicationId} />
            <HardScreeningResultCard job={context.job} value={context.hardScreening} />
            <CandidateDecisionOverviewCard
              overview={context.decisionOverview}
              stage="screening"
            />
          </div>
        }
        right={
          <div className="space-y-5">
            <Panel title="面试官自由记录">
              <p className="mb-3 text-xs leading-5 text-muted">主要记录事实、候选人的具体动作和可核验结果；停止输入后会自动保存。</p>
              <textarea
                className="min-h-64 w-full rounded-md border border-line px-3 py-2 text-sm leading-6"
                value={rawNotes}
                disabled={!canOperate || submitting}
                onChange={(event) => setRawNotes(event.target.value)}
                placeholder="例如：候选人负责了哪些模块、遇到什么问题、如何验证结果……"
              />
              <div className="mt-3 text-xs text-muted">只填写自由记录也可以作出一面决定。</div>
            </Panel>

            <Panel title={`逐题记录（可选 · ${recordedQuestionCount}/${plan.questions.length}）`}>
              <div className="space-y-3">
                {plan.questions.map((question, index) => {
                  const sectionType = question.sectionType === "common" ? "common" : "technical";
                  const sectionIndex = plan.questions
                    .slice(0, index + 1)
                    .filter((item) => (item.sectionType === "common" ? "common" : "technical") === sectionType)
                    .length;
                  const previous = index > 0 ? plan.questions[index - 1] : undefined;
                  const startsSection = index === 0 || (previous?.sectionType === "common" ? "common" : "technical") !== sectionType;
                  const note = questionNotes[question.questionId];
                  const recorded = Boolean(note?.answerSummary.trim() || note?.interviewerNote.trim());
                  return (
                    <div key={question.questionId} className="space-y-2">
                    {startsSection ? <h3 className="pt-2 text-xs font-semibold uppercase tracking-wide text-muted">{sectionType === "common" ? "通用题单" : "技术题单"}</h3> : null}
                    <details className="group rounded-md border border-line" open={recorded || sectionIndex === 1}>
                      <summary className="flex cursor-pointer list-none items-start justify-between gap-3 px-3 py-3">
                        <div className="flex min-w-0 gap-2">
                          <Badge className={recorded ? "shrink-0 border-emerald-200 bg-emerald-50 text-emerald-700" : "shrink-0 border-slate-200 bg-slate-50 text-slate-700"}>
                            {sectionType === "common" ? "通用题" : "技术题"} {sectionIndex}
                          </Badge>
                          <span className="line-clamp-2 text-sm font-medium leading-5 text-ink">{question.finalText ?? question.mainQuestion}</span>
                        </div>
                        <ChevronDown size={15} className="mt-0.5 shrink-0 text-muted transition group-open:rotate-180" />
                      </summary>
                      <div className="border-t border-line p-3">
                        <div className="mb-2 flex flex-wrap gap-2">
                          {question.verificationTargetIds.map((targetId) => (
                            <Badge key={targetId} className="border-slate-200 bg-slate-50 text-slate-700">
                              {targetLookup.get(targetId)?.title ?? "未命名核验重点"}
                            </Badge>
                          ))}
                        </div>
                        <textarea
                          className="min-h-24 w-full rounded-md border border-line px-3 py-2 text-sm leading-6"
                          value={note?.answerSummary ?? ""}
                          disabled={!canOperate || submitting}
                          onChange={(event) => updateQuestionNote(question.questionId, { answerSummary: event.target.value })}
                          placeholder="可选：记录该题回答摘要"
                        />
                        <input
                          className="mt-3 h-10 w-full rounded-md border border-line px-3 text-sm"
                          value={note?.interviewerNote ?? ""}
                          disabled={!canOperate || submitting}
                          onChange={(event) => updateQuestionNote(question.questionId, { interviewerNote: event.target.value })}
                          placeholder="可选：面试官备注"
                        />
                      </div>
                    </details>
                    </div>
                  );
                })}
              </div>
            </Panel>
          </div>
        }
      />
    </>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-md border border-line p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
      </div>
      {children}
    </section>
  );
}
