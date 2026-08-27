import type { ApiErrorShape, PublicConfig, SessionPayload, SseEnvelope } from "../types";

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
    payload?.error.code ?? "NETWORK_ERROR",
    payload?.error.message ?? "请求失败，请稍后重试。",
    response.status,
  );
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  return parseResponse<T>(response);
}

export async function apiDelete<T>(path: string, csrfToken: string): Promise<T> {
  return apiMutation<T>(path, csrfToken, { method: "DELETE" });
}

export async function apiMutation<T>(
  path: string,
  csrfToken: string,
  init: Omit<RequestInit, "credentials"> = {},
): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      ...(init.headers ?? {}),
    },
  });
  return parseResponse<T>(response);
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

export async function streamRunEvents(
  path: string,
  options: {
    signal: AbortSignal;
    afterEventId?: number;
    onEvent: (event: SseEnvelope) => void;
  },
): Promise<void> {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    signal: options.signal,
    headers: {
      Accept: "text/event-stream",
      ...(options.afterEventId ? { "Last-Event-ID": String(options.afterEventId) } : {}),
    },
  });
  if (!response.ok) {
    await parseResponse<never>(response);
  }
  if (!response.body) {
    throw new ApiClientError("STREAM_UNAVAILABLE", "浏览器无法读取回答流。", 503);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
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
        options.onEvent(JSON.parse(dataLines.join("\n")) as SseEnvelope);
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

export async function bootstrap(): Promise<{ config: PublicConfig; session: SessionPayload }> {
  const [config, session] = await Promise.all([
    apiGet<PublicConfig>("/config/public"),
    apiGet<SessionPayload>("/auth/session"),
  ]);
  return { config, session };
}
