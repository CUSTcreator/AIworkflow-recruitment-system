/** 浏览器权限投影：前端只消费后端授权结果，不能通过直达路由或本地角色猜测绕过。 */
import { expect, test, type Page, type Route } from "@playwright/test";

const viewOnlyUser = {
  userId: "U_E2E_VIEWER", username: "e2e_viewer", displayName: "只读查看人",
  role: "department_manager", roleId: "department_manager", roleName: "部门主管",
  departmentId: "DEPT_TECH", businessScope: "department",
  permissions: [], isSystemAdmin: false, mustChangePassword: false,
};

const noResponsibilityUser = {
  ...viewOnlyUser,
  userId: "U_E2E_NO_RESPONSIBILITY",
  username: "e2e_no_responsibility",
  displayName: "无招聘职责账号",
  permissions: [],
};

function applicationList(itemOverrides: Record<string, unknown> = {}) {
  return {
    items: [{
      applicationId: "APP_VIEWER", jobId: "JOB_VIEWER", mainRoute: "/applications/APP_VIEWER/screening-review",
      resumeSubmissionId: "RSUB_VIEWER", resumeFilename: "viewer-resume.pdf", documentStatus: "completed",
      candidateResolved: true, processingStage: "", processingError: "", canRetry: false,
      candidateName: "权限投影候选人", anonymizedCode: "ANON_VIEWER", currentTitle: "后端工程师",
      age: 24, yearsOfExperience: "2 年", school: "测试大学", major: "计算机科学", highestDegree: "本科",
      jobTitle: "测试岗位", jobMajorRequirement: "", department: "技术部", status: "waiting_job_profile",
      resumeRebuildStatus: "idle", resumeRebuildMessage: "",
      preScreeningProcess: {
        status: "initial_assessment_failed", label: "初步筛选启动失败",
        message: "可重新启动初步筛选任务。", tone: "danger", workflowRunId: "WF_VIEWER",
      },
      hardScreeningStatus: "not_configured", hardScreeningSummary: "",
      hardScreeningPreview: { totalCount: 0, passedCount: 0, failedCount: 0, reviewCount: 0, reasons: [] },
      dueAt: "2026-09-01T09:00:00Z", overdue: false, scoreStatus: "failed", screeningError: "",
      assessmentUpdateStatus: "idle", assessmentUpdateMessage: "", canRetryAssessmentUpdate: false,
      // 这是后端已经根据“数据可见”公式投影出的唯一操作；前端不得猜测并展示审核或删除。
      availableActions: [],
      recoveryActions: [{
        action: "retry_initial_assessment", label: "重新启动初步筛选",
        requiresInput: false, warning: "", retryScope: "initial_assessment",
      }],
      assessmentStages: [], currentExecution: null, qualificationGate: "verified",
      submittedAt: "2026-09-01T09:00:00Z", updatedAt: "2026-09-01T09:00:00Z",
      ...itemOverrides,
    }],
    page: 1, pageSize: 30, total: 1,
    groupCounts: { in_progress: 1, passed: 0, rejected: 0, cancelled: 0 },
  };
}

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

async function mockApi(
  page: Page,
  user: typeof viewOnlyUser,
  commands: string[] = [],
  itemOverrides: Record<string, unknown> = {},
): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/auth/me")) return json(route, user);
    if (path.endsWith("/navigation-notifications")) {
      return json(route, { task_unread_count: 0, candidate_unread_count: 0 });
    }
    if (path.endsWith("/navigation-notifications/read")) {
      return json(route, { channel: "candidate_application", last_read_at: "2026-09-01T09:00:00Z" });
    }
    if (path.endsWith("/tasks/my")) {
      return json(route, { items: [], total: 0, overdueCount: 0, page: 1, pageSize: 30 });
    }
    if (path.endsWith("/jobs")) return json(route, []);
    if (path.endsWith("/applications") && request.method() === "GET") return json(route, applicationList(itemOverrides));
    if (path.endsWith("/applications/APP_VIEWER/documents/primary-resume/images")) {
      return json(route, {
        pages: ["/api/v1/applications/APP_VIEWER/documents/primary-resume/pages/page-1.png"],
      });
    }
    if (path.endsWith("/applications/APP_VIEWER/documents/primary-resume/pages/page-1.png")) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(
          "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
          "base64",
        ),
      });
    }
    if (path.endsWith("/applications/APP_VIEWER/documents")) {
      return json(route, {
        applicationId: "APP_VIEWER",
        candidateId: "CAND_VIEWER",
        documents: [{
          documentId: "primary-resume",
          scope: "application",
          displayName: "候选人简历",
          filename: "viewer-resume.pdf",
          primary: true,
          category: "resume",
          sourceStage: "import",
          status: "ready",
          imagesUrl: "/api/v1/applications/APP_VIEWER/documents/primary-resume/images",
          canRename: false,
          canDelete: false,
          uploadedAt: "2026-09-01T09:00:00Z",
          uploadedBy: "U_HR",
          uploadedByName: "HR",
        }],
        permissions: { canUpload: false },
      });
    }
    if (path.endsWith("/applications/APP_VIEWER/initial-assessment/retry") && request.method() === "POST") {
      commands.push(path);
      return json(route, { application_id: "APP_VIEWER", status: "waiting_job_profile", message: "已重新启动" });
    }
    return json(route, {});
  });
}

async function open(page: Page, path: string): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("recruit-ai-access-token", "permission-e2e-token"));
  await page.goto(path);
}

test("无招聘职责的有效账号仍可打开候选人列表", async ({ page }) => {
  await mockApi(page, noResponsibilityUser);
  await open(page, "/candidates");

  await expect(page).toHaveURL(/\/candidates$/);
  await page.getByRole("button", { name: "展开导航" }).click();
  await expect(page.getByRole("link", { name: "候选人列表" })).toHaveCount(1);
});

test("只读查看账号只看到后端投影的恢复动作且只能调用该接口", async ({ page }) => {
  const commands: string[] = [];
  await mockApi(page, viewOnlyUser, commands);
  await open(page, "/candidates");

  await expect(page.getByText("权限投影候选人")).toBeVisible();
  await page.getByRole("button", { name: "权限投影候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "重新启动初步筛选" })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: "审核初步筛选" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "删除候选人申请" })).toHaveCount(0);

  await page.getByRole("menuitem", { name: "重新启动初步筛选" }).click();
  await expect.poll(() => commands).toEqual([
    "/api/v1/applications/APP_VIEWER/initial-assessment/retry",
  ]);
});

test("列表状态本身不会生成工作台入口", async ({ page }) => {
  await mockApi(page, viewOnlyUser, [], {
    status: "department_review",
  });
  await open(page, "/candidates");

  await page.getByRole("button", { name: "权限投影候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "查看初步筛选审核" })).toHaveCount(0);
});

test("列表只展示后端操作码指定的工作台入口", async ({ page }) => {
  await mockApi(page, viewOnlyUser, [], {
    status: "department_review",
    workspaceAction: {
      action: "open_screening_workspace",
      label: "查看初步筛选审核",
      route: "/applications/APP_VIEWER/screening-review",
    },
  });
  await open(page, "/candidates");

  await page.getByRole("button", { name: "权限投影候选人 的更多操作" }).click();
  await expect(page.getByRole("menuitem", { name: "查看初步筛选审核" })).toBeVisible();
});

test("招聘流程的候选人材料抽屉可以预览 PDF", async ({ page }) => {
  await mockApi(page, viewOnlyUser);
  await open(page, "/candidates");

  await page.getByRole("button", { name: "权限投影候选人 的更多操作" }).click();
  await page.getByRole("menuitem", { name: "查看材料" }).click();
  await expect(page.getByRole("heading", { name: "候选人材料" })).toBeVisible();

  await page.getByRole("button", { name: "打开" }).click();
  await expect(page.getByRole("img", { name: "候选人简历第1页" })).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭预览" })).toBeVisible();
});
