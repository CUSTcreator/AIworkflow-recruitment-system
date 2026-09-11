import { requestJson } from "@/shared/api/httpClient";

export type NavigationNotificationChannel = "task" | "candidate_application";

export interface NavigationNotificationCounts {
  taskUnreadCount: number;
  candidateUnreadCount: number;
}

export async function getNavigationNotificationCounts(
  token: string
): Promise<NavigationNotificationCounts> {
  const result = await requestJson<{
    task_unread_count: number;
    candidate_unread_count: number;
  }>(token, "/navigation-notifications");
  return {
    taskUnreadCount: result.task_unread_count,
    candidateUnreadCount: result.candidate_unread_count
  };
}

export function markNavigationNotificationRead(
  token: string,
  channel: NavigationNotificationChannel
): Promise<{ channel: NavigationNotificationChannel; last_read_at: string }> {
  return requestJson(token, `/navigation-notifications/read?channel=${channel}`, {
    method: "POST"
  });
}

export function announceNavigationNotificationChange(): void {
  window.dispatchEvent(new Event("navigation-notifications-changed"));
}
