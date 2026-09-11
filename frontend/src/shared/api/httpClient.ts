import { commandErrorMessage, type CommandErrorAction } from "./commandError";

export const API_BASE =
  ((import.meta as unknown as { env?: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL ??
    "http://127.0.0.1:8000/api/v1");

export const API_ORIGIN = API_BASE.replace(/\/api\/v1\/?$/, "");

export interface ApiErrorPayload {
  detail?: string;
  message?: string;
  code?: string;
  context?: Record<string, unknown>;
  service?: string;
  retryable?: boolean;
  action?: CommandErrorAction;
  requestId?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly context?: Record<string, unknown>;
  readonly service?: string;
  readonly retryable: boolean;
  readonly action: CommandErrorAction;
  readonly requestId?: string;

  constructor(status: number, payload: ApiErrorPayload = {}) {
    const rawMessage = payload.message ?? payload.detail ?? (status === 0 ? "无法连接服务器。" : `请求失败：${status}`);
    const action = payload.action ?? (status >= 500 ? "retry" : "none");
    super(commandErrorMessage({
      code: payload.code, message: rawMessage, retryable: payload.retryable ?? status >= 500,
      action, requestId: payload.requestId,
    }));
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.context = payload.context;
    this.service = payload.service;
    this.retryable = payload.retryable ?? status >= 500;
    this.action = action;
    this.requestId = payload.requestId;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

export function toErrorMessage(error: unknown, fallback = "请求失败") {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function toAbsoluteApiUrl(url: string | undefined): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("/api/v1")) return API_ORIGIN + url;
  if (url.startsWith("/")) return API_ORIGIN + url;
  return url;
}

function newIdempotencyKey(): string {
  // 一个用户操作生成一次；调用方若显式传入 Header，便可在人工重试时复用同一键。
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `web:${crypto.randomUUID()}`;
  }
  return `web:${Date.now().toString(36)}:${Math.random().toString(36).slice(2)}`;
}

function authorizedHeaders(token: string | null, init: RequestInit): Headers {
  const headers = new Headers(init.headers);
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const method = (init.method ?? "GET").toUpperCase();
  // 所有 HTTP 写命令都有幂等键；上传 FormData 同样只加 Header，不改 Content-Type。
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && !headers.has("Idempotency-Key")) {
    headers.set("Idempotency-Key", newIdempotencyKey());
  }
  return headers;
}

async function checkedFetch(token: string | null, url: string, init: RequestInit = {}): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(url, { ...init, headers: authorizedHeaders(token, init) });
  } catch {
    // 网络/CORS 层没有 Response 时也投影成与后端一致的可重试错误，而非原生 Failed to fetch。
    throw new ApiError(0, {
      code: "network_request_failed", message: "无法连接服务器。", retryable: true, action: "retry",
    });
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as ApiErrorPayload;
    throw new ApiError(response.status, payload);
  }
  return response;
}

export async function requestJson<T>(token: string | null, path: string, init: RequestInit = {}): Promise<T> {
  const response = await checkedFetch(token, API_BASE + path, init);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function requestBlob(token: string | null, path: string, init: RequestInit = {}): Promise<Blob> {
  const response = await checkedFetch(token, API_BASE + path, init);
  return response.blob();
}

export async function requestResponse(token: string | null, path: string, init: RequestInit = {}): Promise<Response> {
  return checkedFetch(token, API_BASE + path, init);
}

export async function requestUrlResponse(token: string | null, url: string, init: RequestInit = {}): Promise<Response> {
  return checkedFetch(token, url, init);
}
// 兼容既有异步 Hook；所有错误已在 ApiError 构造阶段转为安全提示。
export const apiErrorMessage = toErrorMessage;
