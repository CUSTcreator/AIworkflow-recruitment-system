/** Backend-owned recovery command shown for the current user and business state. */
export interface RecoveryAction {
  action: string;
  label: string;
  requiresInput: boolean;
  warning: string;
  retryScope?: string;
}

export function recoveryAction(
  actions: RecoveryAction[],
  action: string,
): RecoveryAction | undefined {
  return actions.find((item) => item.action === action);
}

export function hasRecoveryAction(
  actions: RecoveryAction[],
  action: string,
): boolean {
  return Boolean(recoveryAction(actions, action));
}
