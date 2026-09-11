import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

export type ToastTone = "success" | "error" | "warning" | "info";

export interface ToastItem {
  id: string;
  message: string;
  tone: ToastTone;
}

interface ToastValue {
  items: ToastItem[];
  show: (message: string, tone?: ToastTone) => string;
  success: (message: string) => string;
  error: (message: string) => string;
  warning: (message: string) => string;
  info: (message: string) => string;
  dismiss: (id: string) => void;
}

const ToastContext = createContext<ToastValue | undefined>(undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: string) => {
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);
  const show = useCallback((message: string, tone: ToastTone = "info") => {
    const id = crypto.randomUUID();
    setItems((current) => [...current.slice(-3), { id, message, tone }]);
    window.setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4500);
    return id;
  }, [dismiss]);
  const value = useMemo<ToastValue>(() => ({
    items,
    show,
    success: (message) => show(message, "success"),
    error: (message) => show(message, "error"),
    warning: (message) => show(message, "warning"),
    info: (message) => show(message, "info"),
    dismiss,
  }), [dismiss, items, show]);
  return <ToastContext.Provider value={value}>{children}</ToastContext.Provider>;
}

export function useToast(): ToastValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used within ToastProvider");
  return value;
}
