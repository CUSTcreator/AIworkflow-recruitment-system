/** 简历处理页浏览器回归：API 全部由 Playwright 模拟，不写入开发数据库。 */
import { expect, test, type Page, type Route } from "@playwright/test";

type IntakeItem = Record<string, unknown>;

type CandidateIntakeMockOptions = {
  uploadDelayMs?: number;
  onUploadActivity?: (activeRequests: number) => void;
  failUploadAttempts?: number[];
  onUploadRequest?: (attempt: number, idempotencyKey: string) => void;
  failCandidateDeleteIds?: string[];
  onCandidateDelete?: (candidateId: string) => void;
};

type ApplicationListMockOptions = {
  failDeleteIds?: string[];
  filterOptions?: {
    departments: Array<{ departmentId: string; name: string }>;
    jobs: Array<{
      jobId: string;
      title: string;
      departmentId: string;
      departmentName: string;
    }>;
  };
  onApplicationQuery?: (params: URLSearchParams) => void;
};

function recoveryAction(
  action: string,
  label: string,
  requiresInput = false,
  retryScope?: string,
) {
  return { action, label, requiresInput, warning: "", retryScope };
}

const hrUser = {
  userId: "U_E2E_HR", username: "e2e_hr", displayName: "E2E 招聘 HR",
  role: "hr", roleId: "hr", roleName: "招聘 HR", departmentId: "DEPT_TECH",
  businessScope: "organization", permissions: ["resume.upload"],
  isSystemAdmin: true, mustChangePassword: false,
};

function item(overrides: Partial<IntakeItem> = {}): IntakeItem {
  return {
    submission_id: "RSUB_E2E_001", candidate_id: "CAND_E2E_001", candidate_name: "浏览器测试候选人",
    candidate_status: "active", candidate_major: "计算机科学与技术", resume_profile_id: "RPRO_E2E_001",
    filename: "browser-resume.pdf", bucket: "completed", stage: "简历处理完成", submission_status: "completed",
    intake_mode: "initial", review_kind: null, failure_kind: null, source_available: true,
    workflow_status: "completed", workflow_run_id: "WF_E2E_001", process: null, applications: [], application_count: 0,
    routing_status: "manual_selection_available", routing_reason: "可人工选择岗位", review_reason: null,
    available_actions: [
      recoveryAction("force_fresh_parse", "重新解析", false, "parse_resume_document"),
      recoveryAction("upload_replacement_resume", "重新上传", true, "upload_resume"),
      recoveryAction("correct_parsed_resume", "查看并校正", true, "structure_resume"),
      recoveryAction("create_applications", "人工选择岗位", true, "publish_applications"),
    ],
    created_at: "2026-08-24T00:00:00", updated_at: "2026-08-24T00:00:00", ...overrides,
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockCandidateIntakeApi(
  page: Page,
  items: IntakeItem[],
  options: CandidateIntakeMockOptions = {},
): Promise<void> {
  let uploadSequence = 0;
  let activeUploads = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, hrUser);
    if (path.endsWith("/applications")) return json(route, { items: [], total: 0, page: 1, pageSize: 30, groupCounts: { in_progress: 0, passed: 0, rejected: 0, cancelled: 0 } });
    if (path.endsWith("/resume-documents/job-options")) return json(route, []);
    if (path.endsWith("/candidate-intakes/summary")) return json(route, counts(items));
    if (path.endsWith("/candidate-intakes/read")) return json(route, { last_read_at: "2026-08-24T00:00:00" });
    if (path.endsWith("/candidate-intakes") && request.method() === "GET") {
      const status = new URL(request.url()).searchParams.get("status") ?? "all";
      const visible = status === "all" ? items : items.filter((value) => value.bucket === status);
      return json(route, { items: visible, total: visible.length, page: 1, page_size: 20, counts: counts(items) });
    }
    const candidateDeleteMatch = path.match(/\/candidates\/([^/]+)$/);
    if (candidateDeleteMatch && request.method() === "DELETE") {
      const candidateId = candidateDeleteMatch[1];
      options.onCandidateDelete?.(candidateId);
      if (options.failCandidateDeleteIds?.includes(candidateId)) {
        return json(route, { message: "候选人删除失败" }, 500);
      }
      const itemIndex = items.findIndex((value) => value.candidate_id === candidateId);
      if (itemIndex >= 0) items.splice(itemIndex, 1);
      return json(route, { candidate_id: candidateId, deleted: true });
    }
    if (path.endsWith("/rebuild") && request.method() === "POST") {
      Object.assign(items[0], { bucket: "processing", stage: "正在重新解析简历", submission_status: "queued", intake_mode: "reparse", resume_profile_id: "", available_actions: [], workflow_status: "pending" });
      return json(route, { submission_id: items[0].submission_id, workflow_run_id: "WF_E2E_REBUILD_001", status: "queued", mode: "reparse" }, 202);
    }
    if (path.endsWith("/confirm-structure") && request.method() === "POST") {
      Object.assign(items[0], {
        bucket: "completed", stage: "简历处理完成", submission_status: "completed",
        review_kind: null, source_available: true,
        available_actions: [
          recoveryAction("force_fresh_parse", "重新解析", false, "parse_resume_document"),
          recoveryAction("upload_replacement_resume", "重新上传", true, "upload_resume"),
          recoveryAction("create_applications", "人工选择岗位", true, "publish_applications"),
        ],
      });
      return json(route, { submission_id: items[0].submission_id, status: "completed", next_workflow_run_id: "WF_E2E_ROUTING_001", application_ids: [] });
    }
    if (path.endsWith("/candidate-intakes/uploads") && request.method() === "POST") {
      uploadSequence += 1;
      activeUploads += 1;
      options.onUploadActivity?.(activeUploads);
      options.onUploadRequest?.(
        uploadSequence,
        request.headers()["idempotency-key"] ?? "",
      );
      if (options.uploadDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.uploadDelayMs));
      }
      if (options.failUploadAttempts?.includes(uploadSequence)) {
        await json(route, {
          code: "upload_temporarily_unavailable",
          message: "上传服务暂时不可用，请稍后重试。",
          retryable: true,
          action: "retry",
        }, 503);
        activeUploads -= 1;
        options.onUploadActivity?.(activeUploads);
        return;
      }
      const suffix = String(uploadSequence).padStart(3, "0");
      const created = item({ submission_id: `RSUB_E2E_UPLOADED_${suffix}`, candidate_id: `CAND_E2E_UPLOADED_${suffix}`, candidate_name: "待解析候选人", filename: `upload-resume-${suffix}.pdf`, bucket: "processing", stage: "正在解析简历", submission_status: "queued", resume_profile_id: "", source_available: true, workflow_status: "pending", available_actions: [] });
      items.unshift(created);
      await json(route, { document_id: `DOC_E2E_UPLOADED_${suffix}`, submission_id: created.submission_id, workflow_run_id: `WF_E2E_UPLOADED_${suffix}`, status: "queued", reused: false, candidate_id: created.candidate_id, application_ids: [] }, 202);
      activeUploads -= 1;
      options.onUploadActivity?.(activeUploads);
      return;
    }
    // 初始“招聘流程”视图会发起通知等无关请求；空成功响应不影响本测试目标。
    return json(route, {});
  });
}

function counts(items: IntakeItem[]) {
  const count = (bucket: string) => items.filter((value) => value.bucket === bucket).length;
  return { processing_count: count("processing"), attention_count: count("review_required"), completed_count: count("completed"), total_count: items.length, unread_attention_count: 0 };
}

async function openIntake(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("recruit-ai-access-token", "e2e-token"));
  await page.goto("/candidates");
  await page.getByRole("button", { name: "简历处理" }).click();
  await expect(page.getByRole("heading", { name: "简历处理" })).toBeVisible();
}

function applicationItem(applicationId: string, candidateName: string) {
  return {
    applicationId,
    jobId: `JOB_${applicationId}`,
    mainRoute: `/applications/${applicationId}/screening-review`,
    resumeSubmissionId: `RSUB_${applicationId}`,
    resumeFilename: `${candidateName}.pdf`,
    documentStatus: "completed",
    candidateResolved: true,
    processingStage: "",
    processingError: "",
    canRetry: false,
    candidateName,
    anonymizedCode: applicationId,
    currentTitle: "后端开发工程师",
    yearsOfExperience: "2",
    school: "测试大学",
    major: "计算机科学与技术",
    highestDegree: "硕士",
    jobTitle: "Agent 开发实习生",
    department: "技术部",
    jobConfigurationStatus: "complete",
    missingJobAssignments: [],
    canConfigureJob: false,
    status: "submitted",
    resumeRebuildStatus: "idle",
    resumeRebuildMessage: "",
    rejectionStage: null,
    hardScreeningStatus: "passed",
    hardScreeningSummary: "通过",
    hardScreeningPreview: { totalCount: 0, passedCount: 0, failedCount: 0, reviewCount: 0, reasons: [] },
    dueAt: "2026-09-30T00:00:00",
    overdue: false,
    scoreStatus: "scored",
    screeningError: "",
    baseScore: 80,
    currentScore: 80,
    assessmentUpdateStatus: "idle",
    assessmentUpdateMessage: "",
    canRetryAssessmentUpdate: false,
    availableActions: [],
    recoveryActions: [],
    qualificationGate: "verified",
    submittedAt: "2026-09-01T00:00:00",
    updatedAt: "2026-09-01T00:00:00",
  };
}

async function mockApplicationListApi(
  page: Page,
  applications: Array<ReturnType<typeof applicationItem>>,
  deletedIds: string[],
  options: ApplicationListMockOptions = {},
): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) {
      return json(route, {
        ...hrUser,
        permissions: [...hrUser.permissions, "application.delete"],
      });
    }
    if (path.endsWith("/applications/filter-options")) {
      return json(route, options.filterOptions ?? { departments: [], jobs: [] });
    }
    if (path.endsWith("/applications") && request.method() === "GET") {
      options.onApplicationQuery?.(new URL(request.url()).searchParams);
      return json(route, {
        items: applications,
        total: applications.length,
        page: 1,
        pageSize: 30,
        groupCounts: {
          in_progress: applications.length,
          passed: 0,
          rejected: 0,
          cancelled: 0,
        },
      });
    }
    const applicationDeleteMatch = path.match(/\/applications\/([^/]+)$/);
    if (applicationDeleteMatch && request.method() === "DELETE") {
      const applicationId = applicationDeleteMatch[1];
      deletedIds.push(applicationId);
      if (options.failDeleteIds?.includes(applicationId)) {
        return json(route, { message: "岗位申请删除失败" }, 500);
      }
      const itemIndex = applications.findIndex(
        (value) => value.applicationId === applicationId,
      );
      if (itemIndex >= 0) applications.splice(itemIndex, 1);
      return json(route, { applicationId, deleted: true });
    }
    if (path.endsWith("/resume-documents/job-options")) return json(route, []);
    if (path.endsWith("/candidate-intakes/summary")) {
      return json(route, {
        processing_count: 0,
        attention_count: 0,
        completed_count: 0,
        total_count: 0,
        unread_attention_count: 0,
      });
    }
    return json(route, {});
  });
}

test("简历处理页可选择当前页候选人并批量删除", async ({ page }) => {
  const items = [
    item({
      submission_id: "RSUB_E2E_DELETE_001",
      candidate_id: "CAND_E2E_DELETE_001",
      candidate_name: "批量候选人甲",
      filename: "candidate-a.pdf",
    }),
    item({
      submission_id: "RSUB_E2E_DELETE_002",
      candidate_id: "CAND_E2E_DELETE_002",
      candidate_name: "批量候选人乙",
      filename: "candidate-b.pdf",
    }),
  ];
  const deletedIds: string[] = [];
  await mockCandidateIntakeApi(page, items, {
    onCandidateDelete: (candidateId) => deletedIds.push(candidateId),
  });
  await openIntake(page);

  await page.getByRole("checkbox", { name: "选择批量候选人甲" }).check();
  await page.getByRole("checkbox", { name: "选择批量候选人乙" }).check();
  await expect(page.getByText("已选择 2 位候选人")).toBeVisible();
  await page.getByRole("button", { name: "删除所选" }).click();

  const dialog = page.getByRole("dialog", { name: "删除 2 位候选人" });
  await expect(dialog.getByText(/同时移除其全部岗位申请/)).toBeVisible();
  await dialog.getByRole("button", { name: "删除 2 位候选人" }).click();

  await expect(page.getByText("已删除2位候选人")).toBeVisible();
  expect(deletedIds.sort()).toEqual([
    "CAND_E2E_DELETE_001",
    "CAND_E2E_DELETE_002",
  ]);
  await expect(page.getByText("批量候选人甲")).toHaveCount(0);
  await expect(page.getByText("批量候选人乙")).toHaveCount(0);
});

test("招聘流程批量删除时保留失败申请供再次操作", async ({ page }) => {
  const applications = [
    applicationItem("APP_E2E_DELETE_001", "申请候选人甲"),
    applicationItem("APP_E2E_DELETE_002", "申请候选人乙"),
  ];
  const deletedIds: string[] = [];
  await mockApplicationListApi(
    page,
    applications,
    deletedIds,
    { failDeleteIds: ["APP_E2E_DELETE_002"] },
  );
  await page.addInitScript(() =>
    window.localStorage.setItem("recruit-ai-access-token", "e2e-token"),
  );
  await page.goto("/candidates");
  await expect(page.getByText("申请候选人甲", { exact: true })).toBeVisible();

  await page
    .getByRole("checkbox", { name: "选择申请候选人甲的Agent 开发实习生申请" })
    .check();
  await page
    .getByRole("checkbox", { name: "选择申请候选人乙的Agent 开发实习生申请" })
    .check();
  await page.getByRole("button", { name: "删除所选" }).click();

  const dialog = page.getByRole("dialog", { name: "删除 2 份岗位申请" });
  await expect(dialog).toContainText("候选人档案及其其他岗位申请不受影响");
  await dialog.getByRole("button", { name: "删除 2 份申请" }).click();

  await expect(page.getByText("已删除1份申请，1份删除失败")).toBeVisible();
  expect(deletedIds.sort()).toEqual([
    "APP_E2E_DELETE_001",
    "APP_E2E_DELETE_002",
  ]);
  await expect(page.getByText("申请候选人甲", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("checkbox", {
      name: "选择申请候选人乙的Agent 开发实习生申请",
    }),
  ).toBeChecked();
  await expect(page.getByText("已选择 1 份申请")).toBeVisible();
});

test("招聘流程支持部门岗位联动并应用高级筛选", async ({ page }) => {
  const queries: URLSearchParams[] = [];
  await mockApplicationListApi(
    page,
    [applicationItem("APP_E2E_FILTER_001", "筛选候选人")],
    [],
    {
      filterOptions: {
        departments: [
          { departmentId: "DEPT_TECH", name: "技术部" },
          { departmentId: "DEPT_SALES", name: "销售部" },
        ],
        jobs: [
          {
            jobId: "JOB_AGENT",
            title: "Agent 开发实习生",
            departmentId: "DEPT_TECH",
            departmentName: "技术部",
          },
          {
            jobId: "JOB_BACKEND",
            title: "后端开发工程师",
            departmentId: "DEPT_TECH",
            departmentName: "技术部",
          },
          {
            jobId: "JOB_SALES",
            title: "销售专员",
            departmentId: "DEPT_SALES",
            departmentName: "销售部",
          },
        ],
      },
      onApplicationQuery: (params) => queries.push(params),
    },
  );
  await page.addInitScript(() =>
    window.localStorage.setItem("recruit-ai-access-token", "e2e-token"),
  );
  await page.goto("/candidates");

  const jobFilter = page.getByLabel("岗位筛选");
  await expect(jobFilter.locator("option")).toHaveCount(4);
  await page.getByLabel("部门筛选").selectOption("DEPT_TECH");
  await expect(jobFilter.locator("option")).toHaveText([
    "该部门全部岗位",
    "Agent 开发实习生",
    "后端开发工程师",
  ]);
  await jobFilter.selectOption("JOB_AGENT");

  await page.getByRole("button", { name: "更多筛选" }).click();
  await page.getByLabel("学历").selectOption("硕士");
  await page.getByLabel("专业").fill("计算机");
  await page.getByLabel("仅看逾期").check();
  await page.getByRole("button", { name: "应用筛选" }).click();

  await expect(page.getByText("学历：硕士", { exact: true })).toBeVisible();
  await expect(page.getByText("专业：计算机", { exact: true })).toBeVisible();
  await expect(page.getByText("仅看逾期", { exact: true })).toBeVisible();
  await expect.poll(() => {
    const latest = queries.at(-1);
    return latest?.get("departmentId") === "DEPT_TECH"
      && latest.get("jobId") === "JOB_AGENT"
      && latest.get("highestDegree") === "硕士"
      && latest.get("majorKeyword") === "计算机"
      && latest.get("overdueOnly") === "true";
  }).toBe(true);

  await page.getByRole("button", { name: "移除筛选：专业：计算机" }).click();
  await expect(page.getByText("专业：计算机", { exact: true })).toHaveCount(0);
  await expect.poll(() => queries.at(-1)?.has("majorKeyword") ?? true).toBe(false);
});

test("简历完成后把人工选岗显示为下游操作并保留校正入口", async ({ page }) => {
  const items = [item()];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);

  await expect(page.getByText("简历处理完成", { exact: true })).toBeVisible();
  await expect(page.getByText("待选择投递岗位", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "人工选择岗位" })).toBeVisible();
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "查看并校正" })).toBeVisible();
});

test("硬筛拒绝申请在简历处理页直接展示硬筛结果", async ({ page }) => {
  const items = [item({
    applications: [{
      application_id: "APP_E2E_HARD_REJECTED",
      job_id: "JOB_AGENT",
      job_title: "Agent 开发实习生",
      department_id: "DEPT_TECH",
      department_name: "技术部",
      status: "closed_rejected",
      rejection_stage: "hard_screening",
      main_route: "/candidates",
      primary_action: {
        type: "view_hard_screening_result",
        label: "查看硬筛结果",
      },
      submitted_at: "2026-09-06T10:00:00",
      adopted_resume_submission_id: "RSUB_E2E_001",
      uses_current_resume: true,
    }],
    application_count: 1,
  })];
  let hardScreeningRequests = 0;
  let finalReviewRequests = 0;
  await mockCandidateIntakeApi(page, items);
  await page.route("**/api/v1/applications/APP_E2E_HARD_REJECTED/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/hard-screening-result")) {
      hardScreeningRequests += 1;
      return json(route, {
        applicationId: "APP_E2E_HARD_REJECTED",
        status: "failed",
        summary: "未满足最低学历要求。",
        ruleResults: [{
          rule_id: "RULE_DEGREE",
          name: "最低学历",
          requirementText: "学历至少为硕士",
          status: "failed",
          reason: "候选人最高学历为本科。",
          source_quotes: ["某大学 软件工程 本科"],
        }],
      });
    }
    if (path.includes("/views/final-review")) finalReviewRequests += 1;
    return json(route, {});
  });
  await openIntake(page);

  await page.getByRole("button", { name: "已创建 1 个申请" }).click();
  await expect(page.getByRole("button", { name: "查看硬筛结果" })).toBeVisible();
  await page.getByRole("button", { name: "查看硬筛结果" }).click();

  const dialog = page.getByRole("dialog", { name: "硬筛结果" });
  await expect(dialog.getByText("未满足最低学历要求。")).toBeVisible();
  await expect(dialog.getByText("学历至少为硕士")).toBeVisible();
  await expect(dialog.getByText("候选人最高学历为本科。")).toBeVisible();
  await expect(dialog.getByText("某大学 软件工程 本科")).toBeVisible();
  expect(hardScreeningRequests).toBe(1);
  expect(finalReviewRequests).toBe(0);
});

test("完成态简历可提交重新解析，并立即显示处理中", async ({ page }) => {
  const items = [item()];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await page.getByRole("menuitem", { name: "重新解析" }).click();
  await expect(page.getByText("已提交重新解析")).toBeVisible();
  await expect(page.getByText("正在重新解析简历")).toBeVisible();
});

test("结构化待确认提供校正和重新解析，不允许直接确认", async ({ page }) => {
  const items = [item({
    bucket: "review_required", stage: "简历结构待确认", submission_status: "review_required",
    review_kind: "structure", source_available: true,
    available_actions: [
      recoveryAction("correct_parsed_resume", "查看并校正", true, "structure_resume"),
      recoveryAction("force_fresh_parse", "重新解析", false, "parse_resume_document"),
      recoveryAction("upload_replacement_resume", "重新上传", true, "upload_resume"),
    ],
  })];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "查看并校正" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "查看解析文本" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "确认并继续分发" })).toHaveCount(0);
  await page.getByRole("menuitem", { name: "重新解析" }).click();
  await expect(page.getByText("正在重新解析简历")).toBeVisible();
});

test("校正页面回显全部正式画像字段及其原文来源", async ({ page }) => {
  const items = [item({
    bucket: "review_required", stage: "简历结构待确认", submission_status: "review_required",
    review_kind: "structure", source_available: true,
    available_actions: [
      recoveryAction("correct_parsed_resume", "查看并校正", true, "structure_resume"),
    ],
  })];
  await mockCandidateIntakeApi(page, items);
  const blocks = [
    { block_id: "B_EDU_1", text: "A大学 计算机科学 硕士 2026" },
    { block_id: "B_EDU_2", text: "B大学 软件工程 本科 2023" },
    { block_id: "B_TITLE", text: "智能招聘系统" },
    { block_id: "B_CTX", text: "技术栈：Python、FastAPI" },
    { block_id: "B_WORK", text: "实现工作流异常恢复并完成上线" },
    { block_id: "B_SKILL", text: "专业技能：Python、FastAPI" },
  ];
  await page.route("**/api/v1/candidate-intakes/RSUB_E2E_001/correction-draft", (route) => json(route, {
    submission_id: "RSUB_E2E_001",
    source_blocks: blocks,
    candidate_facts: {
      education_records: [
        { school: "A大学", degree: "硕士", major: "计算机科学", graduation_year: 2026, source_refs: [{ block_id: "B_EDU_1" }] },
        { school: "B大学", degree: "本科", major: "软件工程", graduation_year: 2023, source_refs: [{ block_id: "B_EDU_2" }] },
      ],
      highest_degree: { value: "硕士", source_refs: [{ block_id: "B_EDU_1" }] },
      relevant_experience_years: { value: 0, source_refs: [{ block_id: "B_WORK" }] },
    },
    experience_units: [{
      experience_unit_id: "EXP_1", title: "智能招聘系统",
      context_items: [{
        context_id: "CTX_1", context_type: "tech_stack",
        text: "技术栈：Python、FastAPI", source_refs: [{ block_id: "B_CTX" }],
      }],
      source_bullets: [{
        source_bullet_id: "SB_1", text: "实现工作流异常恢复并完成上线",
        source_refs: [{ block_id: "B_WORK" }],
      }],
      title_source_refs: [{ block_id: "B_TITLE" }],
      context_source_refs: [{ block_id: "B_CTX" }],
      work_source_refs: [{ block_id: "B_WORK" }],
    }],
    skill_claims: [{
      skill_name: "Python开发", details: ["Python", "FastAPI"],
      source_refs: [{ block_id: "B_SKILL" }],
    }],
  }));

  await openIntake(page);
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await page.getByRole("menuitem", { name: "查看并校正" }).click();
  const dialog = page.getByRole("dialog", { name: "查看并校正结构化结果" });
  await expect(dialog.getByText("结构化字段", { exact: true })).toBeVisible();
  await expect(dialog.locator("details[open]")).toHaveCount(0);
  await expect(dialog.getByText("教育经历 1")).toBeVisible();
  await expect(dialog.getByText("教育经历 2")).toBeVisible();
  await expect(dialog.getByPlaceholder("学校").nth(0)).toHaveValue("A大学");
  await expect(dialog.getByPlaceholder("学校").nth(1)).toHaveValue("B大学");
  await expect(dialog.getByPlaceholder("相关经验年限")).toHaveValue("0");
  await expect(dialog.getByPlaceholder("项目标题")).toHaveValue("智能招聘系统");
  const projectPreview = dialog.getByTestId("structured-project-preview");
  await expect(projectPreview.getByText("技术栈：Python、FastAPI")).toBeVisible();
  await expect(projectPreview.getByText("实现工作流异常恢复并完成上线")).toBeVisible();
  await expect(dialog.getByText("B_CTX")).toHaveCount(0);
  await expect(dialog.getByText("B_WORK")).toHaveCount(0);
  await expect(dialog.getByText("EXP_1", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("CTX_1", { exact: true })).toHaveCount(0);
  await expect(dialog.getByText("SB_1", { exact: true })).toHaveCount(0);
  await expect(dialog.getByPlaceholder("技能主题")).toHaveValue("Python开发");
  // 最高学历由教育经历派生，不再是可单独校正的来源字段：
  // 2 条教育 + 相关经验年限 + 结构化项目的标题/上下文/证据 + 技能 = 7 条。
  await expect(dialog.locator('input[type="checkbox"]:checked')).toHaveCount(7);
});

test("上传后候选人主行立即进入处理中列表", async ({ page }) => {
  const items: IntakeItem[] = [];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "上传简历" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles({ name: "upload-resume.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 browser test") });
  await dialog.getByRole("button", { name: /上传1份简历/ }).click();
  await expect(page.getByText("1份简历已接收，正在后台解析")).toBeVisible();
  await expect(page.getByText("待解析候选人")).toBeVisible();
  await expect(page.getByText("正在解析简历")).toBeVisible();
});

test("可拖入嵌套文件夹并自动忽略非PDF文件", async ({ page }) => {
  const items: IntakeItem[] = [];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "上传简历" }).click();
  const dialog = page.getByRole("dialog");
  const dropZone = dialog.getByTestId("resume-drop-zone");

  await dropZone.evaluate((element) => {
    const entryFile = (name: string, fullPath: string, type: string) => {
      const file = new File(["%PDF-1.4 browser test"], name, { type });
      return {
        isFile: true,
        isDirectory: false,
        name,
        fullPath,
        file: (callback: (value: File) => void) => callback(file),
      };
    };
    const directory = (name: string, fullPath: string, batches: unknown[][]) => ({
      isFile: false,
      isDirectory: true,
      name,
      fullPath,
      createReader: () => {
        let index = 0;
        return {
          readEntries: (callback: (values: unknown[]) => void) => {
            const values = batches[index] ?? [];
            index += 1;
            callback(values);
          },
        };
      },
    });

    const firstPdf = entryFile("first.pdf", "/resumes/first.pdf", "application/pdf");
    const secondPdf = entryFile("second.pdf", "/resumes/nested/second.pdf", "application/pdf");
    const textFile = entryFile("notes.txt", "/resumes/notes.txt", "text/plain");
    const nested = directory("nested", "/resumes/nested", [[secondPdf], []]);
    const root = directory("resumes", "/resumes", [[firstPdf], [nested, textFile], []]);
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: {
        items: [{ kind: "file", webkitGetAsEntry: () => root }],
        files: [],
      },
    });
    element.dispatchEvent(drop);
  });

  await expect(dialog.getByText("已加入2份", { exact: false })).toBeVisible();
  await expect(dialog.getByText("resumes/first.pdf")).toBeVisible();
  await expect(dialog.getByText("resumes/nested/second.pdf")).toBeVisible();
  await expect(dialog.getByText("已忽略1个非PDF文件", { exact: false })).toBeVisible();
  await dialog.getByRole("button", { name: "确认上传2份简历" }).click();
  await expect(page.getByText("2份简历已接收，正在后台解析")).toBeVisible();
});

test("批量上传最多同时发起3个请求", async ({ page }) => {
  const items: IntakeItem[] = [];
  let maximumActiveUploads = 0;
  await mockCandidateIntakeApi(page, items, {
    uploadDelayMs: 80,
    onUploadActivity: (activeRequests) => {
      maximumActiveUploads = Math.max(maximumActiveUploads, activeRequests);
    },
  });
  await openIntake(page);
  await page.getByRole("button", { name: "上传简历" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(
    Array.from({ length: 7 }, (_, index) => ({
      name: `resume-${index + 1}.pdf`,
      mimeType: "application/pdf",
      buffer: Buffer.from(`%PDF-1.4 browser test ${index + 1}`),
    })),
  );
  await dialog.getByRole("button", { name: "确认上传7份简历" }).click();
  await expect(page.getByText("7份简历已接收，正在后台解析")).toBeVisible();
  expect(maximumActiveUploads).toBe(3);
});

test("单次选择超过20份时明确提示并限制加入数量", async ({ page }) => {
  const items: IntakeItem[] = [];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "上传简历" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles(
    Array.from({ length: 21 }, (_, index) => ({
      name: `limit-${index + 1}.pdf`,
      mimeType: "application/pdf",
      buffer: Buffer.from(`%PDF-1.4 limit test ${index + 1}`),
    })),
  );

  await expect(dialog.getByText("已加入20份", { exact: false })).toBeVisible();
  await expect(dialog.getByText("单次最多上传20份，另有1份未加入", { exact: false })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "确认上传20份简历" })).toBeEnabled();
});

test("临时失败后使用同一幂等键重试单份文件", async ({ page }) => {
  const items: IntakeItem[] = [];
  const idempotencyKeys: string[] = [];
  await mockCandidateIntakeApi(page, items, {
    failUploadAttempts: [1],
    onUploadRequest: (_attempt, idempotencyKey) => {
      idempotencyKeys.push(idempotencyKey);
    },
  });
  await openIntake(page);
  await page.getByRole("button", { name: "上传简历" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: "retry-resume.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 retry test"),
  });

  await dialog.getByRole("button", { name: "确认上传1份简历" }).click();
  await expect(dialog.getByText("1份上传失败。", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "重试1份" }).click();
  await expect(page.getByText("1份简历已接收，正在后台解析")).toBeVisible();
  expect(idempotencyKeys).toHaveLength(2);
  expect(idempotencyKeys[0]).toBeTruthy();
  expect(idempotencyKeys[1]).toBe(idempotencyKeys[0]);
});

test("重新解析命令回滚时展示可追踪错误并刷新真实状态", async ({ page }) => {
  const items = [item({
    bucket: "review_required", stage: "简历结构待确认", submission_status: "review_required",
    review_kind: "structure", source_available: true,
    available_actions: [
      recoveryAction("force_fresh_parse", "重新解析", false, "parse_resume_document"),
    ],
  })];
  await mockCandidateIntakeApi(page, items);
  let idempotencyKey = "";
  await page.route("**/api/v1/candidate-intakes/RSUB_E2E_001/rebuild", async (route) => {
    idempotencyKey = route.request().headers()["idempotency-key"] ?? "";
    await json(route, {
      code: "command_persistence_failed", message: "操作未保存，系统数据契约异常。",
      retryable: true, action: "retry", requestId: "REQ_E2E_001", context: {},
    }, 500);
  });
  await openIntake(page);
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await page.getByRole("menuitem", { name: "重新解析" }).click();
  await expect(page.getByText("请稍后重试。")).toBeVisible();
  await expect(page.getByText("简历结构待确认")).toBeVisible();
  expect(idempotencyKey).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
});

test("原始文件缺失时只显示后端提供的重新上传动作", async ({ page }) => {
  const items = [item({
    bucket: "failed", stage: "原始简历文件缺失", submission_status: "failed",
    source_available: false, workflow_run_id: null,
    available_actions: [
      recoveryAction("upload_replacement_resume", "重新上传", true, "upload_resume"),
    ],
  })];
  await mockCandidateIntakeApi(page, items);
  await openIntake(page);
  await page.getByRole("button", { name: "浏览器测试候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "查看解析文本" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "重新上传" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "重新解析" })).toHaveCount(0);
});
