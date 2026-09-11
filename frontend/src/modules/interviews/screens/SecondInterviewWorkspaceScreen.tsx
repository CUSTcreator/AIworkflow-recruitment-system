import { CheckCircle2, XCircle } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { SecondInterviewProgressDraft } from "@/modules/interviews/contracts";
import { buildScreeningReviewReadModel } from "@/modules/assessment/readModelMappers";
import { readScoreSnapshot } from "@/modules/assessment/scoreSnapshotMapper";
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
import {
  FirstInterviewQuestionRecordsCard,
  FirstInterviewRawNotesCard
} from "@/modules/interviews/components/OriginalInterviewRecords";
import {
  DecisionFocusCard,
  StageHandoffCard
} from "@/modules/interviews/components/DecisionSupportCards";
import { NonCapabilityCard } from "@/modules/interviews/components/NonCapabilityCard";
import { InterviewTimeDialog } from "@/modules/interviews/components/InterviewTimeDialog";
import { useDebouncedSave } from "@/shared/hooks/useDebouncedSave";
import { useSecondInterviewWorkspaceReadModel } from "@/modules/interviews/hooks/useInterviewPageReadModels";
import { PageLoading } from "@/shared/ui/PageLoading";
import { workspaceReturnPath } from "@/shared/navigation/workspaceReturn";

type SaveNotice = { kind: "saved" | "error"; message: string };

function progressPayload(
  applicationId: string,
  rawNotes: string,
  progress?: SecondInterviewProgressDraft
): SecondInterviewProgressDraft {
  return {
    progressDraftId: progress?.progressDraftId,
    applicationId,
    rawNotes,
    createdAt: progress?.createdAt,
    updatedAt: progress?.updatedAt
  };
}

export function SecondInterviewWorkspaceScreen() {
  const applicationId = useRequiredApplicationId();
  const navigate = useNavigate();
  const location = useLocation();
  const returnPath = workspaceReturnPath(location.state);
  const { saveSecondInterviewProgress, completeSecondInterview } = useInterviewCommands();
  const [rawNotes, setRawNotes] = useState("");
  const [hydrated, setHydrated] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [saveNotice, setSaveNotice] = useState<SaveNotice>();
  const [pendingDecision, setPendingDecision] = useState<"pass" | "reject">();
  const hydratedSource = useRef("");
  const lastSavedPayload = useRef("");
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { data: workspace, loading, error: loadError, reload: loadWorkspace } = useSecondInterviewWorkspaceReadModel(applicationId);

  const progress = workspace?.progressDraft;
  const canOperate = Boolean(
    workspace?.availableActions.includes("save_second_interview_progress")
  );

  useEffect(() => {
    if (!workspace) return;
    const sourceKey = `${applicationId}:${progress?.progressDraftId ?? "new"}`;
    if (hydratedSource.current === sourceKey) return;
    const notesText = progress?.rawNotes ?? "";
    setHydrated(false);
    setRawNotes(notesText);
    lastSavedPayload.current = JSON.stringify(progressPayload(applicationId, notesText, progress));
    hydratedSource.current = sourceKey;
    setHydrated(true);
  }, [applicationId, progress, workspace]);

  useEffect(() => () => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
  }, []);

  const draft = useMemo(
    () => workspace ? progressPayload(applicationId, rawNotes, progress) : undefined,
    [applicationId, progress, rawNotes, workspace]
  );

  const showSavedNotice = useCallback(() => {
    if (noticeTimer.current) clearTimeout(noticeTimer.current);
    setSaveNotice({ kind: "saved", message: "草稿已自动保存" });
    noticeTimer.current = setTimeout(() => setSaveNotice(undefined), 1500);
  }, []);

  const persistDraft = useCallback(async (value: SecondInterviewProgressDraft) => {
    const saved = await saveSecondInterviewProgress(applicationId, value, { silent: true });
    if (!saved) {
      setSaveNotice({ kind: "error", message: "自动保存失败，请检查网络后重试" });
      throw new Error("二面草稿保存失败");
    }
    lastSavedPayload.current = JSON.stringify(value);
    showSavedNotice();
  }, [applicationId, saveSecondInterviewProgress, showSavedNotice]);
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

  if (loading) return <PageLoading label="正在加载二面工作台" />;
  if (loadError) return <PageHeader title="二面工作台加载失败" description={loadError} actions={<Button onClick={() => void loadWorkspace()}>重新加载</Button>} />;
  if (!workspace || !draft) {
    return <PageHeader title="未找到二面工作任务" description="请从任务中心重新进入二面工作台。" />;
  }

  const activeDraft = draft;
  const readModel = buildScreeningReviewReadModel(workspace.screening);
  const afterFirstScore = readScoreSnapshot(workspace.afterFirstScoreSnapshot);
  const showScoreChanges = Boolean(afterFirstScore || workspace.afterFirstAssessmentChanges);
  async function saveBeforeDecision(): Promise<boolean> {
    cancelDraftSave();
    const saved = await saveSecondInterviewProgress(applicationId, activeDraft, { silent: true });
    if (saved) {
      lastSavedPayload.current = JSON.stringify(activeDraft);
      setSaveNotice(undefined);
    } else {
      setSaveNotice({ kind: "error", message: "记录保存失败，暂时不能提交二面决定" });
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
    const result = await completeSecondInterview(applicationId, {
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
          className={`fixed right-5 top-5 z-50 rounded-md border px-4 py-3 text-sm font-medium shadow-lg ${
            saveNotice.kind === "saved"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-rose-200 bg-rose-50 text-rose-800"
          }`}
        >
          {saveNotice.message}
        </div>
      ) : null}

      <PageHeader
        eyebrow="HR 二面工作台"
        title={`${workspace.candidate.displayName} · HR 二面`}
        description="左侧查看一面结论和二面重点，右侧记录二面事实；停止输入后自动保存。"
        actions={
          <>
            {canOperate ? (
              <>
                <Button disabled={submitting} onClick={() => requestDecision("pass")} variant="primary">
                  <CheckCircle2 size={16} /> 二面通过
                </Button>
                <Button disabled={submitting} onClick={() => requestDecision("reject")} variant="danger">
                  <XCircle size={16} /> 二面不通过
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <InterviewTimeDialog
        open={Boolean(pendingDecision)}
        title={pendingDecision === "pass" ? "确认二面通过" : "确认二面不通过"}
        description="请填写本次二面实际进行的时间。"
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
        leftTitle="候选人评估与二面重点"
        rightTitle="HR 二面记录"
        left={
          <div className="space-y-5">
            <RecruitmentMilestoneCard applicationId={applicationId} />
            <HardScreeningResultCard job={workspace.job} value={workspace.hardScreening} />
            <CandidateDecisionOverviewCard
              overview={workspace.decisionOverview}
              stage="after_first_interview"
              /** 二面工作台直接展示已发布 V2 相对 V1 的变化，不再要求用户跳转到其他页面。 */
              changes={workspace.afterFirstAssessmentChanges}
              showScoreChanges={showScoreChanges}
            />
            <NonCapabilityCard card={workspace.nonCapabilityCard} />
            <StageHandoffCard handoff={workspace.decisionSupport.stageHandoff} />
            <FirstInterviewRawNotesCard record={workspace.firstOriginalRecord} />
            <FirstInterviewQuestionRecordsCard record={workspace.firstOriginalRecord} />
          </div>
        }
        right={
          <div className="space-y-5">
            <Panel title="面试官自由记录">
              <p className="mb-3 text-xs leading-5 text-muted">记录岗位意愿、稳定性、沟通协作、到岗时间和其他流程条件；停止输入后会自动保存。</p>
              <textarea
                className="min-h-64 w-full rounded-md border border-line px-3 py-2 text-sm leading-6"
                value={rawNotes}
                disabled={!canOperate || submitting}
                onChange={(event) => setRawNotes(event.target.value)}
                placeholder="例如：岗位意愿、到岗时间、实习周期、沟通协作和其他流程条件……"
              />
              <div className="mt-3 text-xs text-muted">自由记录可随时补充，也可以留空提交。</div>
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
