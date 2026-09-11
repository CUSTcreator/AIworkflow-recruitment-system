export interface WorkspaceRouteState {
  returnTo?: unknown;
}

export function workspaceEntryState(pathname: string, search = ""): WorkspaceRouteState {
  return { returnTo: `${pathname}${search}` };
}

export function workspaceReturnPath(state: unknown): string {
  const returnTo = (state as WorkspaceRouteState | null)?.returnTo;
  // Only accept an in-app absolute path. Directly opened workspaces return to the recruitment flow.
  return typeof returnTo === "string" && returnTo.startsWith("/") && !returnTo.startsWith("//")
    ? returnTo
    : "/candidates";
}
