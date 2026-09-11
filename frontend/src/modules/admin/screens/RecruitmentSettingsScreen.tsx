import { ListFilter, MessageSquareText } from "lucide-react";
import { useEffect, useState } from "react";

import { InterviewGuideTemplatePanel } from "@/modules/admin/components/InterviewGuideTemplatePanel";
import { HardScreeningOptionManagement } from "@/modules/admin/screens/SystemAdminScreen";
import { useAuth } from "@/modules/auth/AuthProvider";
import { hasBusinessPermission } from "@/modules/auth/permissions";
import { PageHeader } from "@/shared/ui/PageHeader";

type RecruitmentSettingsSection = "hard-screening" | "interview-guides";

export function RecruitmentSettingsScreen() {
  const { token, user } = useAuth();
  const canManageHardScreening = hasBusinessPermission(user, "hard_screening.catalog.manage");
  const canManageInterviewGuides = hasBusinessPermission(user, "interview_guide.manage");
  const [section, setSection] = useState<RecruitmentSettingsSection>(
    canManageHardScreening ? "hard-screening" : "interview-guides"
  );

  useEffect(() => {
    if (section === "hard-screening" && !canManageHardScreening && canManageInterviewGuides) {
      setSection("interview-guides");
    }
    if (section === "interview-guides" && !canManageInterviewGuides && canManageHardScreening) {
      setSection("hard-screening");
    }
  }, [canManageHardScreening, canManageInterviewGuides, section]);

  return (
    <>
      <PageHeader title="招聘配置" />
      <div className="mx-auto grid max-w-6xl gap-4 lg:grid-cols-[190px_minmax(0,1fr)]">
        <aside className="h-fit rounded-md border border-line bg-white p-2 shadow-sm">
          <nav className="grid gap-1">
            {canManageHardScreening ? (
              <button
                type="button"
                className={`flex items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm font-medium transition ${
                  section === "hard-screening" ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
                onClick={() => setSection("hard-screening")}
              >
                <ListFilter size={17} />全局硬筛条件库
              </button>
            ) : null}
            {canManageInterviewGuides ? (
              <button
                type="button"
                className={`flex items-center gap-2 rounded-md px-3 py-2.5 text-left text-sm font-medium transition ${
                  section === "interview-guides" ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
                onClick={() => setSection("interview-guides")}
              >
                <MessageSquareText size={17} />通用面试题单
              </button>
            ) : null}
          </nav>
        </aside>
        <main className="min-w-0">
          {section === "hard-screening" && canManageHardScreening ? <HardScreeningOptionManagement /> : null}
          {section === "interview-guides" && canManageInterviewGuides ? <InterviewGuideTemplatePanel token={token} /> : null}
        </main>
      </div>
    </>
  );
}
