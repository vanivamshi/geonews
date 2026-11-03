"use client";

const TOKEN_KEY = "geonews.token";
const ADMIN_KEY = "geonews.adminToken";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: session-only */
  }
}

export const auth = {
  token: () => read(TOKEN_KEY),
  setToken: (t: string | null) => write(TOKEN_KEY, t),
  adminToken: () => read(ADMIN_KEY),
  setAdminToken: (t: string | null) => write(ADMIN_KEY, t),
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit & { admin?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = auth.token();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.admin) headers.set("X-Admin-Token", auth.adminToken() ?? "");
  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

export type ChatEvent = { event: string; data: Record<string, unknown> };

/** POST /chat and yield server-sent events as they arrive. */
export async function* streamChat(question: string, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${auth.token() ?? ""}` },
    body: JSON.stringify({ question }),
    signal,
  });
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const raw = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
    }
  }
}

export function logEvent(body: Record<string, unknown>) {
  api("/events", { method: "POST", body: JSON.stringify(body) }).catch(() => {});
}
