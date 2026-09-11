/** V1/V2/V3 工作台主流程：所有 API 由 Playwright 模拟，绝不写入开发数据库。 */
import { expect, test, type Page, type Route } from "@playwright/test";

const applicationId = "APP_DELIVERY_E2E";

const hrUser = {
  userId: "U_E2E_HR", username: "e2e_hr", displayName: "E2E 招聘 HR",
  role: "hr", roleId: "hr", roleName: "招聘 HR", departmentId: "DEPT_TECH",
  businessScope: "organization", isSystemAdmin: true, mustChangePassword: false,
  permissions: ["hard_screening.review", "department_review.manage", "first_interview.manage", "second_interview.manage", "final_decision.manage"],
};

const application = {
  applicationId, candidateId: "CAND_DELIVERY_E2E", jobId: "JOB_DELIVERY_E2E",
  status: "department_review", department: "技术部", currentOwner: "招聘 HR",
  assignedFirstInterviewer: "部门招聘专员", assignedSecondHr: "招聘 HR",
  submittedAt: "2026-09-01T09:00:00+08:00", updatedAt: "2026-09-01T09:00:00+08:00",
  dueAt: "2026-09-10T09:00:00+08:00", overdue: false,
};

const candidate = {
  candidateId: "CAND_DELIVERY_E2E", displayName: "交付测试候选人", anonymizedCode: "C-001",
  currentTitle: "AI 应用开发实习生", yearsOfExperience: "1 年", education: "计算机科学本科", tags: [],
};

const job = {
  jobId: "JOB_DELIVERY_E2E", title: "AI 应用开发工程师", department: "技术部",
  owner: "技术负责人", headcount: 1, openHeadcount: 1, location: "上海",
  jdTextPreview: "负责 AI 应用的工程化开发", majorRequirement: "计算机相关专业", educationRequirement: "本科及以上",
};

const evidenceIndex = {
  EVD_001: { evidenceId: "EVD_001", rawText: "负责实现检索服务并完成上线交付。", sourceLineStart: 3, sourceLineEnd: 3 },
};

const hardScreening = {
  resultId: "HSR_DELIVERY_E2E",
  policyId: "HSP_DELIVERY_E2E",
  status: "passed",
  summary: "全部硬性条件均通过。",
  counts: { total: 1, passed: 1, failed: 0, manualReview: 0, notEvaluated: 0 },
  requirements: [{
    ruleId: "HSRULE_CET6",
    name: "英语等级",
    requirementText: "大学英语六级",
    status: "passed",
    reason: "简历明确记录英语等级六级。",
    reasonCode: "semantic_match_passed",
    sourceQuotes: ["英语等级：六级"],
  }],
};

const screening = {
  screeningAssessmentId: "SCORE_DELIVERY_E2E", applicationId, sourceBundleRef: "object://delivery-e2e",
  scoreStatus: "scored", summary: "建议推进一面", versionMetadata: { bundleVersion: "v1", generatedAt: "2026-09-01T09:00:00+08:00", source: "e2e" },
  screeningResultView: {
    viewSchemaVersion: "screening_result_view_v2_0",
    applicationId,
    summary: { overallScore: 75, jobCapabilityFitScore: 72, resumeExperienceScore: 78, educationBackgroundScore: 70, qualificationStatus: "verified" },
    jobRequirements: [{
      jobUnitId: "JD_UNIT_001", sourceText: "具备 AI 应用工程化交付经验", sourceSection: "任职资格", score: 72,
      coreCapabilities: [{
        capabilityId: "CAP_DELIVERY", name: "工程交付能力", definition: "可交付并维护 AI 应用服务", role: "core", score: 72,
        contentFitDescription: "候选人有检索服务上线经历", evidenceQualityDescription: "原文项目证据", evidence: [{
          evidenceId: "MATCH_001", sourceEvidenceIds: ["EVD_001"], evidenceType: "resume", score: 72, qualityScore: 80,
          contentFitDescription: "实现并上线", reason: "项目原文",
        }],
      }],
      supportingCapabilities: [],
    }],
    resumeCapabilities: [], interviewFocus: { firstInterviewFocus: [] }, evidenceIndex,
  },
};

function decisionOverview(stage: "screening" | "after_first_interview" | "after_second_interview") {
  return {
    assessmentStage: stage,
    assessment: { totalScore: 75, jobFitScore: 72, experienceScore: 78, educationScore: 70, qualificationStatus: "verified" },
    aiSummary: { recommendationLevel: "recommend", recommendationReason: "工程经历与岗位匹配", strengths: [], weaknesses: [], generationMode: "rule_fallback" },
    verificationFocus: { items: [], generationMode: "rule_fallback" },
    sourceSnapshotHash: "delivery-e2e", generatedAt: "2026-09-01T09:00:00+08:00",
  };
}

function assessmentChanges(stage: "after_first_interview" | "after_second_interview") {
  return {
    assessmentVersionId: `AAV_${stage}`, stage, previousAssessmentVersionId: "AAV_PREVIOUS",
    scoreChanges: [{ metric: "total", previousValue: 75, currentValue: 75, delta: 0 }],
    capabilityChanges: [{
      resultRef: "CAP_DELIVERY", capabilityId: "CAP_DELIVERY", previousScore: 72, currentScore: 72, delta: 0,
      reasonCode: "no_new_scoring_evidence", evidenceIds: ["EVD_001"],
    }],
    strengths: [], weaknesses: [], verificationFocus: [],
  };
}

function secondReviewView(mode: "review" | "final") {
  const isFinal = mode === "final";
  return {
    viewSchemaVersion: isFinal ? "final_review_v2" : "second_interview_review_v2",
    application: { ...application, status: isFinal ? "final_review" : "hr_second_review" },
    candidate, job, hardScreening, screening,
    firstEvidence: [], firstOriginalRecord: { recordedQuestions: [] }, secondOriginalRecord: undefined,
    reviewPackage: undefined, finalPackage: undefined, hrAssessment: undefined,
    afterFirstScoreSnapshot: { stage: "after_first_interview", overallScore: 75, jobCapabilityFitScore: 72, resumeExperienceScore: 78, educationBackgroundScore: 70 },
    afterSecondScoreSnapshot: isFinal ? { stage: "after_second_interview", overallScore: 75, jobCapabilityFitScore: 72, resumeExperienceScore: 78, educationBackgroundScore: 70 } : undefined,
    afterFirstAssessmentChanges: assessmentChanges("after_first_interview"),
    afterSecondAssessmentChanges: isFinal ? assessmentChanges("after_second_interview") : undefined,
    currentScoreSnapshot: undefined,
    decisionSummary: undefined, decisionOverview: decisionOverview(isFinal ? "after_second_interview" : "after_first_interview"),
    decisionSupport: {
      schemaVersion: "decision_support_v2_0", decisionFocus: { items: [] },
      assessmentCoverage: { planned: 1, fullyAssessed: 1, partiallyAssessed: 0, notAssessed: 0, coverageRate: 1 },
      stageHandoff: null, hrConditions: [],
    },
    nonCapabilityCard: { title: "非能力条件", description: "", items: [] },
    evidenceIndex, availableActions: isFinal ? ["offer"] : ["approve_second_interview"], recoveryActions: [], workflowStatus: {},
  };
}

function secondInterviewWorkspaceView() {
  return {
    ...secondReviewView("review"),
    viewSchemaVersion: "second_interview_workspace_v2",
    application: { ...application, status: "second_interview_in_progress" },
    progressDraft: undefined,
    firstOriginalRecord: {
      rawNotes: {
        content: "一面评价：候选人技术基础扎实，项目表达清晰。",
        authorName: "技术面试官",
        createdAt: "2026-09-01T10:00:00+08:00",
      },
      recordedQuestions: [{
        questionId: "Q_FIRST_001",
        questionText: "请介绍检索服务的实现方案",
        answerSummary: "候选人说明了向量检索、召回与重排流程。",
        interviewerNote: "方案完整，能够说明关键取舍。",
      }],
    },
    decisionSupport: {
      schemaVersion: "decision_support_v2_0",
      decisionFocus: { items: [] },
      assessmentCoverage: { planned: 1, fullyAssessed: 1, partiallyAssessed: 0, notAssessed: 0, coverageRate: 1 },
      stageHandoff: {
        recommendation: "建议进入二面",
        decisionReason: "一面确认候选人具备岗位要求的工程基础。",
        confirmedStrengths: ["技术基础扎实"],
        remainingItems: ["进一步确认岗位稳定性"],
      },
      hrConditions: [],
    },
    nonCapabilityCard: {
      title: "一面非能力信息",
      description: "一面记录中需要在二面继续确认的流程信息。",
      items: [{
        itemId: "NON_CAP_FIRST_001",
        label: "到岗时间",
        value: "两周内可到岗",
        reasonCode: "availability",
        status: "normal",
        sourceStage: "after_first_interview",
      }],
    },
    availableActions: ["save_second_interview_progress", "complete_second_interview"],
  };
}

function applicationListView(itemOverrides: Record<string, unknown> = {}) {
  const process = {
    workflowRunId: "WF_V3_RETRY", workflowType: "post_second_scoring_workflow",
    processStatus: "failed", currentStep: "publish_post_interview_assessment",
    currentStepLabel: "发布面试后评估", attemptCount: 1, maxAttempts: 1,
    pollCount: 0, maxPollAttempts: 0, nextAttemptAt: "",
    publicMessage: "当前步骤处理失败。", recoveryAction: "user_retry",
    recoveryActionLabel: "手动重试", updatedAt: "2026-09-01T09:00:00+08:00",
  };
  return {
    items: [{
      applicationId, jobId: job.jobId, mainRoute: `/applications/${applicationId}/final-review`,
      resumeSubmissionId: "RSUB_E2E", resumeFilename: "resume.pdf", documentStatus: "completed",
      candidateResolved: true, processingStage: "", processingError: "", canRetry: false,
      candidateName: candidate.displayName, anonymizedCode: candidate.anonymizedCode,
      currentTitle: candidate.currentTitle, age: 24, yearsOfExperience: candidate.yearsOfExperience,
      school: "测试大学", major: "计算机科学", highestDegree: "本科", jobTitle: job.title,
      jobMajorRequirement: job.majorRequirement, department: job.department, status: "final_review",
      resumeRebuildStatus: "idle", resumeRebuildMessage: "", rejectionStage: null,
      hardScreeningStatus: "passed", hardScreeningSummary: hardScreening.summary,
      hardScreeningPreview: { totalCount: 1, passedCount: 1, failedCount: 0, reviewCount: 0, reasons: [] },
      dueAt: application.dueAt, overdue: false, scoreStatus: "scored", screeningError: "",
      baseScore: 75, currentScore: 75, scoreStage: "after_second_interview",
      assessmentUpdateStatus: "failed", assessmentUpdateStage: "second",
      assessmentUpdateMessage: "系统未能完成评估更新，可重新计算", assessmentUpdateProcess: process,
      canRetryAssessmentUpdate: true, availableActions: ["retry_post_second_scoring"], recoveryActions: [],
      assessmentStages: [
        { stage: "v1", label: "初步筛选", status: "completed", resultAvailable: true, publishedAssessmentVersionId: "AAV_V1", viewAction: { action: "view_v1_result", label: "查看初步筛选", route: `/applications/${applicationId}/screening-review` } },
        { stage: "v2", label: "一面后评估", status: "completed", resultAvailable: true, publishedAssessmentVersionId: "AAV_V2", viewAction: { action: "view_v2_result", label: "查看一面后评估", route: `/applications/${applicationId}/hr-second-review` } },
        { stage: "v3", label: "二面后评估", status: "recoverable", resultAvailable: true, publishedAssessmentVersionId: "AAV_V3", viewAction: { action: "view_v3_result", label: "查看二面后评估", route: `/applications/${applicationId}/final-review` } },
      ],
      currentExecution: { stage: "v3", label: "二面后评估", process },
      interviewReviewRequired: false, interviewReviewCount: 0, qualificationGate: "verified",
      submittedAt: application.submittedAt, updatedAt: application.updatedAt,
      ...itemOverrides,
    }],
    page: 1, pageSize: 20, total: 1,
    groupCounts: { in_progress: 1, passed: 0, rejected: 0, cancelled: 0 },
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockWorkflowApi(
  page: Page,
  commandPaths: string[],
  listItemOverrides: Record<string, unknown> = {},
): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, hrUser);
    if (path.endsWith("/applications")) return json(route, applicationListView(listItemOverrides));
    if (path.endsWith(`/applications/${applicationId}/views/screening-review`)) {
      return json(route, {
        application, candidate, job, hardScreening,
        screeningResult: screening.screeningResultView,
        decisionOverview: decisionOverview("screening"), evidenceIndex,
        availableActions: ["approve_first_interview", "reject"], recoveryActions: [],
        workflowStatus: { applicationId, runStatus: "completed", applicationStatus: "department_review", stage: "screening", updatedAt: "2026-09-01T09:00:00+08:00" },
        viewSchemaVersion: "screening_review_v2",
      });
    }
    if (path.endsWith(`/applications/${applicationId}/views/first-interview-plan`) || path.endsWith(`/applications/${applicationId}/views/first-interview-workspace`)) {
      return json(route, {
        application: { ...application, status: "first_interview_planning" }, candidate, job, hardScreening,
        screening, plan: undefined, planningState: { status: "not_started" }, progressDraft: undefined,
        decisionSummary: undefined, decisionOverview: decisionOverview("screening"), decisionSupport: {
          schemaVersion: "decision_support_v2_0", decisionFocus: { items: [] }, assessmentCoverage: { planned: 0, fullyAssessed: 0, partiallyAssessed: 0, notAssessed: 0, coverageRate: 0 }, stageHandoff: null, hrConditions: [],
        }, evidenceIndex, availableActions: [], recoveryActions: [], workflowStatus: {},
        viewSchemaVersion: path.endsWith("first-interview-plan") ? "first_interview_plan_v3" : "first_interview_workspace_v2",
      });
    }
    if (path.endsWith(`/applications/${applicationId}/workflows/scoring/status`)) {
      return json(route, { applicationId, runStatus: "completed", applicationStatus: "department_review", stage: "screening", updatedAt: "2026-09-01T09:00:00+08:00" });
    }
    if (path.endsWith(`/applications/${applicationId}/views/hr-second-review`)) return json(route, secondReviewView("review"));
    if (path.endsWith(`/applications/${applicationId}/views/second-interview-workspace`)) return json(route, secondInterviewWorkspaceView());
    if (path.endsWith(`/applications/${applicationId}/views/final-review`)) return json(route, secondReviewView("final"));
    if (path.endsWith(`/applications/${applicationId}/documents`)) return json(route, { applicationId, documents: [], permissions: { canUpload: false } });
    if (request.method() === "POST" && path.endsWith("/actions/approve-first-interview")) {
      commandPaths.push(path);
      return json(route, { application_id: applicationId, status: "first_interview_planning", message: "已推进", next_route: `/applications/${applicationId}/interviews/first/plan` });
    }
    if (request.method() === "POST" && path.endsWith("/actions/approve-second-interview")) {
      commandPaths.push(path);
      return json(route, { application_id: applicationId, status: "second_interview_in_progress", message: "已推进", next_route: `/applications/${applicationId}/interviews/second/workspace` });
    }
    if (request.method() === "POST" && path.endsWith("/actions/final-decision")) {
      commandPaths.push(path);
      return json(route, { application_id: applicationId, status: "offer_process", message: "已进入 Offer", next_route: "/candidates" });
    }
    // App shell may request unrelated counters; an empty successful response keeps these tests scoped to the workbenches.
    return json(route, {});
  });
}

async function open(page: Page, route: string): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("recruit-ai-access-token", "delivery-e2e-token"));
  await page.goto(route);
}

test("初步筛选工作台显示结果并发送推进一面命令", async ({ page }) => {
  const commands: string[] = [];
  await mockWorkflowApi(page, commands);
  await open(page, `/applications/${applicationId}/screening-review`);

  await expect(page.getByRole("heading", { name: "交付测试候选人 · AI 应用开发工程师" })).toBeVisible();
  await expect(page.getByText("岗位硬筛", { exact: true })).toBeVisible();
  await expect(page.getByText("1 项硬性条件均已满足", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看全部 1 项" }).click();
  await expect(page.getByText(/英语等级：大学英语六级/)).toBeVisible();
  await expect(page.getByText("通过", { exact: true })).toBeVisible();
  await expect(page.getByText("工程交付能力", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "推进", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${applicationId}/interviews/first/plan$`));
  expect(commands).toEqual([`/api/v1/applications/${applicationId}/actions/approve-first-interview`]);
});

test("候选人列表只展示当前执行轨迹并保留所有已发布评估入口", async ({ page }) => {
  await mockWorkflowApi(page, []);
  await open(page, "/candidates");

  await page.getByRole("button", { name: `${candidate.displayName} 的更多操作` }).click();
  await expect(page.getByRole("menuitem", { name: "查看二面后评估执行轨迹" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "查看初步筛选" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "查看一面后评估" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "查看二面后评估", exact: true })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "查看初步筛选执行轨迹" })).toHaveCount(0);
});

test("候选人列表评估弹窗只读，不提供正常推进操作", async ({ page }) => {
  const commands: string[] = [];
  await mockWorkflowApi(page, commands);
  await open(page, "/candidates");

  await page.getByRole("button", { name: `${candidate.displayName} 的更多操作` }).click();
  await page.getByRole("menuitem", { name: "查看二面后评估", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "二面后评估" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "通过", exact: true })).toHaveCount(0);
  await expect(dialog.getByRole("button", { name: "不通过", exact: true })).toHaveCount(0);
  expect(commands).toEqual([]);
});

test("候选人列表不提供正常状态的面试后评估重算", async ({ page }) => {
  await mockWorkflowApi(page, [], {
    assessmentUpdateStatus: "completed",
    assessmentUpdateStage: "second",
    availableActions: ["retry_post_second_scoring"],
  });
  await open(page, "/candidates");

  await page.getByRole("button", { name: `${candidate.displayName} 的更多操作` }).click();
  await expect(page.getByRole("menuitem", { name: "重新计算二面后评估" })).toHaveCount(0);
});

test("岗位画像异常在候选人列表弹窗处理，不跳转岗位管理页", async ({ page }) => {
  await mockWorkflowApi(page, [], {
    recoveryActions: [{ action: "repair_job_profile", label: "处理岗位画像异常" }],
  });
  await open(page, "/candidates");

  await page.getByRole("button", { name: `${candidate.displayName} 的更多操作` }).click();
  await page.getByRole("menuitem", { name: "查看并处理岗位来源" }).click();

  await expect(page.getByRole("dialog", { name: "查看并处理岗位来源" })).toBeVisible();
  await expect(page).toHaveURL(/\/candidates$/);
});

test("V2 二面审核在无变化时仍显示 +0.0，并发送推进二面命令", async ({ page }) => {
  const commands: string[] = [];
  await mockWorkflowApi(page, commands);
  await open(page, `/applications/${applicationId}/hr-second-review`);

  await expect(page.getByRole("heading", { name: "交付测试候选人 · AI 应用开发工程师" })).toBeVisible();
  await expect(page.getByText("岗位硬筛", { exact: true })).toBeVisible();
  await expect(page.getByText("72.0 → 72.0（+0.0）", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "推进", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/applications/${applicationId}/interviews/second/workspace$`));
  expect(commands).toEqual([`/api/v1/applications/${applicationId}/actions/approve-second-interview`]);
});

test("二面执行工作台展示一面评价、题目记录和阶段交接信息", async ({ page }) => {
  await mockWorkflowApi(page, []);
  await open(page, `/applications/${applicationId}/interviews/second/workspace`);

  await expect(page.getByRole("heading", { name: "交付测试候选人 · HR 二面" })).toBeVisible();
  await expect(page.getByText("一面评价：候选人技术基础扎实，项目表达清晰。", { exact: true })).toBeVisible();
  await expect(page.getByText("一面有效题目记录（1）", { exact: true })).toBeVisible();
  await expect(page.getByText("一面非能力信息", { exact: true })).toBeVisible();
  await expect(page.getByText("一面确认候选人具备岗位要求的工程基础。", { exact: true })).toBeVisible();
});

test("V3 最终审核在无变化时仍显示 +0.0，并发送 Offer 决策", async ({ page }) => {
  const commands: string[] = [];
  await mockWorkflowApi(page, commands);
  await open(page, `/applications/${applicationId}/final-review`);

  await expect(page.getByRole("heading", { name: "交付测试候选人 · 最终人工决策" })).toBeVisible();
  await expect(page.getByText("岗位硬筛", { exact: true })).toBeVisible();
  await expect(page.getByText("72.0 → 72.0（+0.0）", { exact: true })).toBeVisible();
  // 最终审核只展示当前最新已发布评估；历史 V2/V3 的入口由候选人列表提供，
  // 不再在这个工作台内复制页签切换逻辑。
  await page.getByRole("button", { name: "通过", exact: true }).click();
  await expect(page).toHaveURL(/\/candidates$/);
  expect(commands).toEqual([`/api/v1/applications/${applicationId}/actions/final-decision`]);
});
