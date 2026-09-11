/** 同步命令错误的前端投影；与后端 ApiErrorResponse 保持同一合同。 */
export type CommandErrorAction = "none" | "refresh" | "retry";

export interface CommandErrorView {
  code?: string;
  message: string;
  retryable: boolean;
  action: CommandErrorAction;
  requestId?: string;
}

/**
 * 仅追加用户下一步可执行的动作，不展示 SQL、栈追踪或服务端内部异常。
 */
export function commandErrorMessage(error: CommandErrorView): string {
  const suffix = error.action === "refresh"
    ? "请刷新页面后重试。"
    : error.action === "retry"
      ? "请稍后重试。"
      : "";
  return suffix && !error.message.includes(suffix) ? `${error.message}${suffix}` : error.message;
}
