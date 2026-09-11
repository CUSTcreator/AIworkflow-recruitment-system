import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useAnalyticsReadModel } from "@/modules/analytics/hooks/useAnalyticsReadModel";
import { useAuth } from "@/modules/auth/AuthProvider";
import { PageHeader } from "@/shared/ui/PageHeader";
import { Section } from "@/shared/ui/Section";
import { StatCard } from "@/shared/ui/StatCard";
import { PageLoading } from "@/shared/ui/PageLoading";

const colors = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0f766e"];

export function AnalyticsScreen() {
  const { user } = useAuth();
  const { data, loading, error } = useAnalyticsReadModel();

  if (loading) return <PageLoading label="正在加载招聘概览" />;
  if (error) return <PageHeader title="招聘概览加载失败" description={error} />;

  const summary = data?.summary ?? { departmentCount: 0, candidateCount: 0, finalReviewCount: 0 };
  const departmentSummary = data?.departments ?? [];
  const currentStageDistribution = (data?.stages ?? []).map((item) => ({
    status: item.label,
    count: item.count
  }));

  return (
    <>
      <PageHeader
        eyebrow="招聘数据"
        title="部门招聘概览"
        description={user?.businessScope === "department" ? "查看本部门招聘进度与申请阶段分布。" : "按部门汇总候选人规模、当前流程阶段和最终招聘结果。"}
      />

      <div className="mb-5 grid max-w-5xl gap-4 md:grid-cols-3">
        <StatCard label={user?.businessScope === "department" ? "本部门" : "招聘部门"} value={summary.departmentCount} tone="blue" />
        <StatCard label="候选人总数" value={summary.candidateCount} tone="amber" />
        <StatCard label="最终决策中的申请" value={summary.finalReviewCount} tone="green" />
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Section title="各部门候选人分布" description="对比各部门当前纳入招聘流程的候选人数量。">
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={departmentSummary}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="department" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="total" name="候选人数" radius={[6, 6, 0, 0]}>
                  {departmentSummary.map((_, index) => (
                    <Cell key={index} fill={colors[index % colors.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <Section title="当前阶段分布" description="查看全部候选申请所处的招聘阶段。">
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={currentStageDistribution}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="status" tick={{ fontSize: 11 }} interval={0} angle={-12} textAnchor="end" height={60} />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Bar dataKey="count" fill="#059669" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Section>

        <div className="xl:col-span-2">
          <Section title="部门招聘明细" description="候选人规模按去重候选人统计，流程结果按候选申请统计。">
            {departmentSummary.length === 0 ? (
              <div className="rounded-md border border-dashed border-line p-8 text-center text-sm text-muted">
                暂无可汇总的候选申请。
              </div>
            ) : (
              <div className="overflow-x-auto rounded-md border border-line">
                <table className="data-table w-full min-w-[640px] border-collapse text-left text-sm">
                  <thead className="bg-slate-50 text-xs text-muted">
                    <tr>
                      <th className="px-4 py-3">部门</th>
                      <th className="data-table-number px-4 py-3">候选人总数</th>
                      <th className="data-table-number px-4 py-3">最终决策中的申请</th>
                      <th className="data-table-number px-4 py-3">通过申请</th>
                      <th className="data-table-number px-4 py-3">不通过申请</th>
                    </tr>
                  </thead>
                  <tbody>
                    {departmentSummary.map((department) => (
                      <tr key={department.department} className="border-t border-line bg-white">
                        <td className="px-4 py-3 font-medium text-ink">{department.department}</td>
                        <td className="data-table-number px-4 py-3">{department.total}</td>
                        <td className="data-table-number px-4 py-3">{department.finalReview}</td>
                        <td className="data-table-number px-4 py-3 text-emerald-700">{department.passed}</td>
                        <td className="data-table-number px-4 py-3 text-rose-700">{department.rejected}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}
