/** Thin client for the field API (/acs/{ac}/field/...). Amounts stay decimal strings. */
import { useMemo, useRef } from "react";
import { useSession } from "./session";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

type Params = Record<string, string | number | boolean | null | undefined>;

export function query(params?: Params): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

async function call<T>(method: string, url: string, token: string, body?: unknown, extra: Record<string, string> = {}): Promise<T> {
  const r = await fetch(url, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...extra },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, detail);
  }
  if (r.status === 204) return undefined as T;
  const type = r.headers.get("content-type") ?? "";
  return (type.includes("json") ? r.json() : r.text()) as Promise<T>;
}

export interface Api {
  base: string;
  get<T>(path: string, params?: Params): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
  /** POST that creates something, with an Idempotency-Key: a retry with the same key returns the first
   * result instead of creating a second row (pulse/api/idempotency.py). */
  create<T>(path: string, body: unknown, idempotencyKey: string): Promise<T>;
  put<T>(path: string, body?: unknown): Promise<T>;
  patch<T>(path: string, body?: unknown): Promise<T>;
  del<T>(path: string): Promise<T>;
  /** Downloads a file the API streams (CSV exports), with the bearer token. */
  download(path: string, params: Params, filename: string): Promise<void>;
}

export function makeApi(token: string, acNumber: number): Api {
  const base = `/acs/${acNumber}/field`;
  return {
    base,
    get: (path, params) => call("GET", base + path + query(params), token),
    post: (path, body) => call("POST", base + path, token, body ?? {}),
    create: (path, body, key) => call("POST", base + path, token, body ?? {}, { "Idempotency-Key": key }),
    put: (path, body) => call("PUT", base + path, token, body ?? {}),
    patch: (path, body) => call("PATCH", base + path, token, body ?? {}),
    del: (path) => call("DELETE", base + path, token),
    async download(path, params, filename) {
      const r = await fetch(base + path + query(params), { headers: { Authorization: `Bearer ${token}` } });
      if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => ({ detail: r.statusText }))).detail);
      const url = URL.createObjectURL(await r.blob());
      const a = Object.assign(document.createElement("a"), { href: url, download: filename });
      a.click();
      URL.revokeObjectURL(url);
    },
  };
}

/** One key per request: the same body keeps its key (a retry after a timeout or a double submit is
 * replayed by the server, not saved twice); a changed body or `done()` after success gets a new key. */
export function useIdempotencyKey(): { keyFor: (body: unknown) => string; done: () => void } {
  const ref = useRef<{ key: string; body: string } | null>(null);
  return useMemo(() => ({
    keyFor(body: unknown) {
      const b = JSON.stringify(body);
      if (!ref.current || ref.current.body !== b) ref.current = { key: crypto.randomUUID(), body: b };
      return ref.current.key;
    },
    done() { ref.current = null; },
  }), []);
}

export function useApi(): Api {
  const s = useSession();
  return useMemo(() => makeApi(s.token, s.acNumber), [s.token, s.acNumber]);
}
