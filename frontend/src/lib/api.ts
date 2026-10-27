import type { ApiErrorShape, PublicConfig, SessionPayload, SseEnvelope } from "../types";
import { isMobileOnline, isNativeMobile, mobileApiBase, mobileTokens } from "./mobile";

const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

export class ApiClientError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }
  let payload: ApiErrorShape | undefined;
  try {
    payload = (await response.json()) as ApiErrorShape;
  } catch {
    payload = undefined;
  }
  throw new ApiClientError(
    payload?.error?.code ?? "NETWORK_ERROR",
    payload?.error?.message ?? (isNativeMobile() && response.status === 401 ? "登录已过期，请重新登录。" : "请求失败，请稍后重试。"),
    response.status,
  );
}

export async function apiGet<T>(path: string): Promise<T> {
  return requestJson<T>(path);
}

export async function apiDelete<T>(path: string, csrfToken: string): Promise<T> {
  return apiMutation<T>(path, csrfToken, { method: "DELETE" });
}

export async function apiMutation<T>(
  path: string,
  csrfToken: string,
  init: Omit<RequestInit, "credentials"> = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (!isNativeMobile()) headers.set("X-CSRF-Token", csrfToken);
  return requestJson<T>(path, { ...init, headers });
}

export function apiUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) throw new ApiClientError("INVALID_PATH", "接口地址无效。", 400);
  return `${isNativeMobile() ? mobileApiBase() : API_BASE}${path}`;
}

function requestLifetime(signal: AbortSignal | null | undefined, timeout: number) {
  const controller = new AbortController();
  let expired = false;
  let timer: ReturnType<typeof setTimeout>;
  const reset = () => {
    clearTimeout(timer);
    timer = setTimeout(() => { expired = true; controller.abort(); }, timeout);
  };
  const abort = () => controller.abort();
  if (signal?.aborted) abort();
  else signal?.addEventListener("abort", abort, { once: true });
  reset();
  return { signal: controller.signal, reset,
    error: (error: unknown): Error => {
      if (expired) return new ApiClientError("REQUEST_TIMEOUT", "连接超时，请检查网络后重试。", 408);
      if (signal?.aborted) return new DOMException("已取消请求", "AbortError");
      if (error instanceof ApiClientError) return error;
      return new ApiClientError("NETWORK_ERROR", "网络连接失败，请检查网络后重试。", 0);
    },
    close: () => { clearTimeout(timer); signal?.removeEventListener("abort", abort); },
  };
}

async function send(path: string, init: RequestInit, token: string | null | undefined, invalidate: boolean): Promise<Response> {
  const native = isNativeMobile();
  if (native && !isMobileOnline()) throw new ApiClientError("OFFLINE", "当前离线，请连接网络后重试。", 0);
  const value = token === undefined ? mobileTokens.value : token;
  const headers = new Headers(init.headers);
  if (native) {
    headers.delete("X-CSRF-Token");
    headers.delete("Authorization");
    if (value) headers.set("Authorization", `Bearer ${value}`);
  }
  const response = await fetch(apiUrl(path), { ...init, headers, credentials: native ? "omit" : "include", redirect: native ? "error" : "follow" });
  if (native && invalidate && response.status === 401 && value && value === mobileTokens.value) {
    const payload = await response.clone().json().catch(() => null) as ApiErrorShape | null;
    if (value === mobileTokens.value) {
      const clearing = mobileTokens.clear();
      // Advance the login lifecycle before awaiting Keystore; a late event must
      // never cancel a new account that signs in while native storage is busy.
      window.dispatchEvent(new CustomEvent("xiaowo:mobile-auth-expired", { detail: { message: payload?.error?.message ?? "登录已过期，请重新登录。" } }));
      await clearing;
    }
  }
  return response;
}

async function requestJson<T>(path: string, init: RequestInit = {}, token?: string | null, invalidate = true): Promise<T> {
  const lifetime = requestLifetime(init.signal, 30000);
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && init.body !== null) headers.set("Content-Type", "application/json");
  try { return await parseResponse<T>(await send(path, { ...init, headers, signal: lifetime.signal }, token, invalidate)); }
  catch (error) { throw lifetime.error(error); }
  finally { lifetime.close(); }
}

/** Explicit token isolates login/logout from the active session and from stale 401 events. */
export function mobileAuthRequest<T>(path: string, init: RequestInit = {}, token: string | null = null): Promise<T> {
  return requestJson<T>(path, init, token, false);
}

export async function streamRunEvents(
  path: string,
  options: {
    signal: AbortSignal;
    afterEventId?: number;
    onEvent: (event: SseEnvelope) => void;
  },
): Promise<void> {
  const lifetime = requestLifetime(options.signal, 45000);
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  try {
  const response = await send(path, {
    signal: lifetime.signal,
    headers: {
      Accept: "text/event-stream",
      ...(options.afterEventId ? { "Last-Event-ID": String(options.afterEventId) } : {}),
    },
  }, undefined, true);
  if (!response.ok) {
    await parseResponse<never>(response);
  }
  if (!response.body) {
    throw new ApiClientError("STREAM_UNAVAILABLE", "浏览器无法读取回答流。", 503);
  }

  reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    lifetime.reset();
    buffer += decoder.decode(value, { stream: !done });
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary).replace(/\r/g, "");
      buffer = buffer.slice(boundary + 2);
      const dataLines = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trimStart());
      if (dataLines.length) {
        // 坏帧容错：单帧畸形数据不应让整条流失败（run 本身可能正常完成）
        try {
          options.onEvent(JSON.parse(dataLines.join("\n")) as SseEnvelope);
        } catch {
          console.warn("丢弃无法解析的 SSE 帧");
        }
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
  } catch (error) { throw lifetime.error(error); }
  finally { lifetime.close(); await reader?.cancel().catch(() => {}); reader?.releaseLock(); }
}

export async function bootstrap(): Promise<{ config: PublicConfig; session: SessionPayload }> {
  const [config, session] = await Promise.all([
    apiGet<PublicConfig>("/config/public"),
    apiGet<SessionPayload>("/auth/session"),
  ]);
  return { config, session };
}
