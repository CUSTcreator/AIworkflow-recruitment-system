import type {
  CapabilityFrameworkReadItem,
  JobFitReadItem,

  ScreeningAssessment,
  ScreeningAssessmentReadModel,
  VerificationFocusReadItem
} from "@/modules/assessment/contracts";
import type {
  FirstInterviewProgressDraft,
  FirstInterviewPlanningReadModel,
  FirstInterviewWorkspaceReadModel,
  InterviewPlan,
  InterviewQuestion
} from "@/modules/interviews/contracts";

function text(value: unknown, fallback = ''): string {
  return typeof value === "string" ? value : fallback;
}

function number(value: unknown, fallback: number | null = null): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? [...new Set(value.filter((item): item is string => typeof item === "string" && item.length > 0))]
    : [];
}

function priority(value: unknown): "high" | "medium" | "low" {
  return value === "high" || value === "low" ? value : "medium";
}

function requirementItem(raw: Record<string, unknown>): JobFitReadItem {
  const core = Array.isArray(raw.coreCapabilities) ? raw.coreCapabilities : [];
  const supporting = Array.isArray(raw.supportingCapabilities) ? raw.supportingCapabilities : [];
  const capabilities = [...core, ...supporting] as Array<Record<string, unknown>>;
  const evidenceIds = capabilities.flatMap((capability) => {
    const evidence = Array.isArray(capability.evidence) ? capability.evidence : [];
    return evidence.flatMap((item) => {
      const value = item as Record<string, unknown>;
      const sourceIds = strings(value.sourceEvidenceIds);
      return sourceIds.length > 0 ? sourceIds : strings([value.evidenceId]);
    });
  });
  return {
    id: text(raw.jobUnitId),
    name: text(raw.sourceText),
    score: number(raw.score),
    level: text(raw.sourceSection, "岗位要求"),
    level_description: capabilities.map((item) => text(item.contentFitDescription)).filter(Boolean).join("；"),
    summary: capabilities.map((item) => text(item.name)).filter(Boolean).join("、"),
    evidence_ids: [...new Set(evidenceIds)]
  };
}

function canonicalFrameworkItem(raw: Record<string, unknown>): CapabilityFrameworkReadItem {
  const indicators = Array.isArray(raw.indicators) ? raw.indicators : [];
  return {
    id: text(raw.frameworkId),
    name: text(raw.frameworkName),
    score: number(raw.score, 0) ?? 0,
    level: null,
    conclusion: `${number(raw.activatedIndicatorCount, 0) ?? 0}/${number(raw.indicatorCount, 0) ?? 0} 个指标获得有效证明`,
    indicators: indicators.map((item) => {
      const indicator = item as Record<string, unknown>;
      return {
        id: text(indicator.indicatorId),
        name: text(indicator.name, text(indicator.indicatorId)),
        score: number(indicator.score, 0) ?? 0,
        level: null,
        level_description: text(indicator.levelDescription),
        conclusion: text(indicator.primaryProjectName),
        evidence_ids: strings(indicator.evidenceIds)
      };
    })
  };
}


function focusItem(raw: Record<string, unknown>, index: number): VerificationFocusReadItem {
  const title = text(raw.title);
  return {
    id: text(raw.focusId, text(raw.targetId, text(raw.target_id, `FOCUS_${index + 1}`))),
    title,
    summary: text(raw.questionGoal, text(raw.question_goal, text(raw.unknownPoint))),
    expected_evidence: text(raw.expectedEvidence, text(raw.expected_evidence)),
    priority: priority(raw.priority),
    evidence_ids: strings(raw.evidenceIds ?? raw.sourceEvidenceIds ?? raw.answered_by_evidence_ids)
  };
}

function uniqueVerificationItems<T extends VerificationFocusReadItem>(items: T[]): T[] {
  const result = new Map<string, T>();
  const priorityRank = { low: 1, medium: 2, high: 3 } as const;
  items.forEach((item) => {
    const key = `${item.title.trim()}|${item.summary.trim()}`;
    const current = result.get(key);
    if (!current) {
      result.set(key, item);
      return;
    }
    result.set(key, {
      ...current,
      priority: priorityRank[item.priority] > priorityRank[current.priority] ? item.priority : current.priority,
      evidence_ids: [...new Set([...current.evidence_ids, ...item.evidence_ids])]
    });
  });
  return [...result.values()];
}

export function buildScreeningReviewReadModel(screening: ScreeningAssessment): ScreeningAssessmentReadModel {
  const payload = screening.screeningResultView;
  if (!payload || payload.viewSchemaVersion !== "screening_result_view_v2_0") {
    throw new Error("当前初步筛选结果格式已过期，请重新运行初步筛选。");
  }
  const summary = payload.summary;
  // 新旧初筛结果在没有待核验事项时都允许省略 interviewFocus；页面应显示空列表而非崩溃。
  const firstInterviewFocus = payload.interviewFocus?.firstInterviewFocus ?? [];
  const interviewTargets = uniqueVerificationItems(
    firstInterviewFocus
      .map((item, index) => focusItem(item, index))
      .filter((item) => item.title)
  );
  return {
    summary: {
      total_score: summary.overallScore ?? 0,
      job_fit_score: summary.jobCapabilityFitScore ?? 0,
      resume_experience_score: summary.resumeExperienceScore ?? 0,
      education_score: summary.educationBackgroundScore ?? null,
      qualification_status: summary.qualificationStatus,
      recommendation: screening.summary,
    },
    job_fit: {
      groups: payload.jobRequirements.map((item) => requirementItem(item as unknown as Record<string, unknown>))
    },
    capability_performance: {
      frameworks: payload.resumeCapabilities
        .map((item) => canonicalFrameworkItem(item as unknown as Record<string, unknown>))
        .filter((item) => item.score > 0)
        .sort((a, b) => b.score - a.score)
    },
    verification_focus: {
      interview_targets: interviewTargets
    },
    evidence_index: payload.evidenceIndex,
    available_actions: ["enter_first_interview", "hold", "manual_review", "reject"]
  };
}

function normalizedSuggestion(
  question: InterviewQuestion | Record<string, unknown>
): InterviewQuestion {
  const raw = question as unknown as Record<string, unknown>;
  const questionId = text(raw.questionId, text(raw.question_id));
  const verificationTargetIds = strings(
    raw.verificationTargetIds ?? raw.interviewTargetIds ?? raw.interview_target_ids
  );
  const mainQuestion = text(raw.mainQuestion, text(raw.question));
  const questionPriority = priority(raw.priority);
  const sourceSuggestionId = text(raw.sourceSuggestionId, text(raw.source_suggestion_id, questionId));

  return {
    questionId,
    verificationTargetIds,
    mainQuestion,
    followUpQuestions: strings(raw.followUpQuestions ?? raw.follow_up_questions),
    expectedEvidence: strings(raw.expectedEvidence ?? raw.expected_evidence),
    negativeSignals: strings(raw.negativeSignals ?? raw.negative_signals),
    priority: questionPriority,
    confirmed: typeof raw.confirmed === "boolean" ? raw.confirmed : false,
    sourceType: "ai_suggestion",
    sourceSuggestionId,
    finalText: text(raw.finalText, text(raw.final_text, mainQuestion)),
    finalQuestionId: text(raw.finalQuestionId, text(raw.final_question_id, `FQ_${questionId}`)),
    isEdited: typeof raw.isEdited === "boolean"
      ? raw.isEdited
      : typeof raw.is_edited === "boolean"
        ? raw.is_edited
        : false,
    recommendation: raw.recommendation === "high" || raw.recommendation === "low" || raw.recommendation === "medium"
      ? raw.recommendation
      : questionPriority,
    validationGoal: text(
      raw.validationGoal,
      text(raw.validation_goal, text(raw.purpose, verificationTargetIds.join("、")))
    )
  };
}

function limitedSuggestionCandidates(items: Array<InterviewQuestion | Record<string, unknown>>) {
  return items;
}

export function buildFirstInterviewPlanningReadModel(
  screening: ScreeningAssessment,
  sourcePlan: InterviewPlan,
  draftGuide: InterviewPlan
): FirstInterviewPlanningReadModel {
  const screeningReadModel = buildScreeningReviewReadModel(screening);
  const coveredIds = new Set(draftGuide.questions.flatMap((question) => question.verificationTargetIds));
  const uncovered = draftGuide.targets.filter((target) => !coveredIds.has(target.targetId)).map((target) => target.title);
  const byText = new Map<string, string[]>();
  draftGuide.questions.forEach((question) => {
    const key = (question.finalText ?? question.mainQuestion).trim().toLowerCase();
    if (key) byText.set(key, [...(byText.get(key) ?? []), question.questionId]);
  });
  const duplicateIds = [...byText.values()].filter((ids) => ids.length > 1).flat();
  const unlinkedIds = draftGuide.questions.filter((question) => question.verificationTargetIds.length === 0).map((question) => question.questionId);
  const targetCounts = draftGuide.targets.map((target) => draftGuide.questions.filter((question) => question.verificationTargetIds.includes(target.targetId)).length);
  const concentrated = draftGuide.questions.length >= 3 && targetCounts.some((count) => count / draftGuide.questions.length > 0.7);
  return {
    screening_summary: screeningReadModel.summary,
    verification_focus: { items: screeningReadModel.verification_focus.interview_targets },
    question_suggestions: limitedSuggestionCandidates(
      sourcePlan.questionSuggestions ?? sourcePlan.questions.filter((question) => question.sourceType !== "interviewer_custom")
    ).map((question) => normalizedSuggestion(question)),
    draft_guide: draftGuide,
    coverage_check: {
      covered: draftGuide.targets.length - uncovered.length,
      total: draftGuide.targets.length,
      uncovered,
      duplicate_question_ids: duplicateIds,
      unlinked_question_ids: unlinkedIds,
      concentration_warning: concentrated ? "题目方向较集中，建议覆盖更多验证目标。" : undefined
    },
    available_actions: ["save_draft", "undo", "confirm"]
  };
}

export function buildFirstInterviewWorkspaceReadModel(
  screening: ScreeningAssessment,
  confirmedGuide: InterviewPlan,
  progress?: FirstInterviewProgressDraft
): FirstInterviewWorkspaceReadModel {
  const screeningReadModel = buildScreeningReviewReadModel(screening);
  return {
    assessment_summary: screeningReadModel.summary,
    confirmed_guide: confirmedGuide,
    verification_focus: screeningReadModel.verification_focus,
    evidence_index: screeningReadModel.evidence_index,
    progress: progress ?? {
      applicationId: confirmedGuide.applicationId,
      guideId: confirmedGuide.planId,
      rawNotes: "",
      questionResponses: [],
      verificationTargetStatuses: {}
    }
  };
}
