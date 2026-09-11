export function toUserFacingText(value: string): string {
  return [
    ["结构化证据", "已记录信息"],
    ["HR 二面审核包", "一面结论"],
    ["HR二面审核包", "一面结论"],
    ["审核包", "面试结论"],
    ["证据缺口", "信息不足项"],
    ["workflow", "后台处理"],
    ["Workflow", "后台处理"]
  ].reduce((text, [source, target]) => text.split(source).join(target), value);
}
export function toUserFacingError(value: string): string {
  const text = value.trim();
  const mappings: Array<[RegExp, string]> = [
    [/work_unit_extraction_failed/i, "简历经历内容识别失败，请重新解析后再试。"],
    [/resume_project_assessment/i, "简历经历评估失败，请重新运行初步筛选。"],
    [/screening_foundation/i, "初步筛选准备失败，请重新运行初步筛选。"],
    [/document.*(?:parse|extract)|(?:parse|extract).*document|mineru/i, "文件内容解析失败，请确认文件清晰完整后重试。"],
    [/timeout|timed out/i, "外部服务响应超时，请稍后重试。"],
    [/rate.?limit|too many requests|status.?429/i, "智能分析服务当前繁忙，请稍后重试。"],
    [/json|schema|validation/i, "智能分析结果格式异常，请重新运行。"],
    [/parallel_stage_failed|BatchExecutionError/i, "系统处理失败，请重新运行。"]
  ];
  for (const [pattern, message] of mappings) {
    if (pattern.test(text)) return message;
  }
  if (/[a-z]+_[a-z_]+|[A-Za-z]+Error|Traceback|status.?[45]\d\d/.test(text)) {
    return "系统处理失败，请稍后重试；若仍失败请联系管理员。";
  }
  return toUserFacingText(text) || "系统处理失败，请稍后重试。";
}
