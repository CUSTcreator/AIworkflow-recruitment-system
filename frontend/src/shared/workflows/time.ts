/**
 * Workflow 的后端时间统一是 UTC。
 *
 * 新接口会返回带 Z 的 ISO-8601；这里仍兼容历史任务的无时区时间字符串，避免
 * 浏览器把旧的 UTC 时间误当成本地时间，导致执行轨迹少八小时。
 */
export function parseWorkflowUtc(value: string): Date {
  const normalized = value && !/[zZ]$|[+-]\d\d:\d\d$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  return new Date(normalized);
}

export function formatWorkflowTime(
  value: string,
  options: Intl.DateTimeFormatOptions = { hour12: false },
): string {
  const date = parseWorkflowUtc(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", options);
}
