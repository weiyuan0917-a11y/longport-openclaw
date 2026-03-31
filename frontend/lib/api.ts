const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
const LOCAL_API_FALLBACK = "http://127.0.0.1:8000";

type RequestOptions = {
  timeoutMs?: number;
  retries?: number;
  cacheTtlMs?: number;
};

const DEFAULT_TIMEOUT_MS = 30000;
const DEFAULT_RETRIES = 3;
const DEFAULT_GET_CACHE_TTL_MS = 15000;
const memoryGetCache = new Map<string, { expiresAt: number; data: unknown }>();

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const isLocalApiBase = /127\.0\.0\.1|localhost/i.test(API_BASE);

function candidateBases(): string[] {
  if (isLocalApiBase) return [API_BASE];
  return [API_BASE, LOCAL_API_FALLBACK];
}

async function requestJson<T>(
  path: string,
  init?: RequestInit,
  options?: RequestOptions
): Promise<T> {
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const retries = options?.retries ?? DEFAULT_RETRIES;
  const cacheTtlMs = Math.max(0, options?.cacheTtlMs ?? DEFAULT_GET_CACHE_TTL_MS);
  const method = String(init?.method || "GET").toUpperCase();
  let lastErr: unknown;
  const bases = candidateBases();

  for (const base of bases) {
    const url = `${base}${path}`;
    if (method === "GET" && cacheTtlMs > 0) {
      const cached = memoryGetCache.get(url);
      if (cached && cached.expiresAt > Date.now()) {
        return cached.data as T;
      }
    }
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(url, {
          ...init,
          cache: "no-store",
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (!res.ok) {
          throw new Error(await res.text());
        }
        const data = (await res.json()) as T;
        if (method === "GET" && cacheTtlMs > 0) {
          memoryGetCache.set(url, {
            expiresAt: Date.now() + cacheTtlMs,
            data,
          });
        }
        return data;
      } catch (err) {
        clearTimeout(timer);
        lastErr = err;
        if (attempt < retries) {
          const backoffMs = 500 * 2 ** attempt;
          const jitterMs = Math.floor(Math.random() * 300);
          await sleep(backoffMs + jitterMs);
        }
      }
    }
  }

  if (lastErr instanceof Error && lastErr.name === "AbortError") {
    throw new Error("请求超时，请稍后重试");
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr));
}

export async function apiGet<T>(path: string, options?: RequestOptions): Promise<T> {
  return requestJson<T>(path, undefined, options);
}

export async function apiPost<T>(
  path: string,
  body: unknown,
  options?: RequestOptions
): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    options
  );
}

export async function apiPut<T>(
  path: string,
  body: unknown,
  options?: RequestOptions
): Promise<T> {
  return requestJson<T>(
    path,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    options
  );
}

export async function apiDelete<T>(path: string, options?: RequestOptions): Promise<T> {
  return requestJson<T>(path, { method: "DELETE" }, options);
}
