import { lazy, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { createBrowserRouter } from "react-router-dom";
import { ProtectedApp } from "./ProtectedApp";
import { AdminGate } from "./guards/AdminGate";
import { HomeRoute } from "./HomeRoute";
import { PermissionGate } from "./guards/PermissionGate";
import { CanonicalApplicationRoute } from "./CanonicalApplicationRoute";
import { PageLoading } from "@/shared/ui/PageLoading";

function namedPage<T extends Record<string, ComponentType>>(load: () => Promise<T>, name: keyof T) {
  return lazy(async () => ({ default: (await load())[name] })) as LazyExoticComponent<ComponentType>;
}

function page(Page: LazyExoticComponent<ComponentType>) {
  return <Suspense fallback={<PageLoading />}><Page /></Suspense>;
}

const LoginPage = namedPage(() => import("@/pages/LoginPage"), "LoginPage");
const CandidateListPage = namedPage(() => import("@/pages/CandidateListPage"), "CandidateListPage");
const JobManagementPage = namedPage(() => import("@/pages/JobManagementPage"), "JobManagementPage");
const ScreeningReviewPage = namedPage(() => import("@/pages/ScreeningReviewPage"), "ScreeningReviewPage");

const FirstInterviewPlanPage = namedPage(() => import("@/pages/FirstInterviewPlanPage"), "FirstInterviewPlanPage");
const FirstInterviewWorkspacePage = namedPage(() => import("@/pages/FirstInterviewWorkspacePage"), "FirstInterviewWorkspacePage");
const HrSecondReviewPage = namedPage(() => import("@/pages/HrSecondReviewPage"), "HrSecondReviewPage");
const SecondInterviewWorkspacePage = namedPage(() => import("@/pages/SecondInterviewWorkspacePage"), "SecondInterviewWorkspacePage");
const FinalReviewPage = namedPage(() => import("@/pages/FinalReviewPage"), "FinalReviewPage");
const AnalyticsPage = namedPage(() => import("@/pages/AnalyticsPage"), "AnalyticsPage");
const SystemAdminPage = namedPage(() => import("@/pages/SystemAdminPage"), "SystemAdminPage");
const RecruitmentSettingsPage = namedPage(() => import("@/pages/RecruitmentSettingsPage"), "RecruitmentSettingsPage");

export const router = createBrowserRouter([
  { path: "/login", element: page(LoginPage) },
  {
    path: "/",
    element: <ProtectedApp />,
    children: [
      { index: true, element: <HomeRoute /> },
      { path: "candidates", element: page(CandidateListPage) },
      { path: "jobs", element: page(JobManagementPage) },
      { path: "applications/:applicationId/screening-review", element: page(ScreeningReviewPage) },

      { path: "applications/:applicationId/interviews/first/plan", element: page(FirstInterviewPlanPage) },
      { path: "applications/:applicationId/interviews/first/workspace", element: page(FirstInterviewWorkspacePage) },
      { path: "applications/:applicationId/interviews/first/in-progress", element: <CanonicalApplicationRoute target="interviews/first/workspace" /> },
      { path: "applications/:applicationId/interviews/first/evaluation", element: <CanonicalApplicationRoute target="interviews/first/workspace" /> },
      { path: "applications/:applicationId/hr-second-review", element: page(HrSecondReviewPage) },
      { path: "applications/:applicationId/interviews/second/workspace", element: page(SecondInterviewWorkspacePage) },
      { path: "applications/:applicationId/interviews/second/in-progress", element: <CanonicalApplicationRoute target="interviews/second/workspace" /> },
      { path: "applications/:applicationId/interviews/second/evaluation", element: <CanonicalApplicationRoute target="interviews/second/workspace" /> },
      { path: "applications/:applicationId/final-review", element: page(FinalReviewPage) },
      { path: "analytics", element: page(AnalyticsPage) },
      { path: "recruitment-settings", element: <PermissionGate permission={["hard_screening.catalog.manage", "interview_guide.manage"]}>{page(RecruitmentSettingsPage)}</PermissionGate> },
      { path: "admin", element: <AdminGate>{page(SystemAdminPage)}</AdminGate> }
    ]
  }
]);
