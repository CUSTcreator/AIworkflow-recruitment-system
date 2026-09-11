import { useEffect, useRef } from "react";

export function usePeriodicRefresh(
  refresh: () => void | Promise<unknown>,
  enabled: boolean,
  intervalMs = 5_000
) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | undefined;

    const schedule = () => {
      timer = window.setTimeout(async () => {
        await refreshRef.current();
        if (!cancelled) schedule();
      }, intervalMs);
    };

    schedule();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [enabled, intervalMs]);
}