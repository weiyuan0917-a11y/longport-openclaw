"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
const LOCAL_API_FALLBACK = "http://127.0.0.1:8000";
const HEALTH_PROBE_TIMEOUT_MS = 15000;
const HEALTH_PROBE_INTERVAL_MS = 15000;
const HEALTH_OFFLINE_FAIL_THRESHOLD = 6;
const HEALTH_OFFLINE_MIN_SILENCE_MS = 90000;

type Status = "checking" | "online" | "offline";

export function BackendConnectionBanner() {
  const [status, setStatus] = useState<Status>("checking");
  const [checking, setChecking] = useState(false);
  const mountedRef = useRef(true);
  const inFlightRef = useRef(false);
  const lastOkTsRef = useRef<number>(Date.now());
  const failCountRef = useRef(0);

  const healthUrls = useMemo(() => {
    if (/127\.0\.0\.1|localhost/i.test(API_BASE)) {
      return [`${API_BASE}/health`];
    }
    return [`${API_BASE}/health`, `${LOCAL_API_FALLBACK}/health`];
  }, []);

  const probe = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setChecking(true);
    try {
      let ok = false;
      for (const url of healthUrls) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), HEALTH_PROBE_TIMEOUT_MS);
        try {
          const res = await fetch(url, {
            method: "GET",
            cache: "no-store",
            signal: ctrl.signal,
          });
          clearTimeout(timer);
          if (res.ok) {
            ok = true;
            break;
          }
        } catch {
          clearTimeout(timer);
        }
      }
      if (!mountedRef.current) return;
      if (ok) {
        failCountRef.current = 0;
        lastOkTsRef.current = Date.now();
        setStatus("online");
      } else {
        failCountRef.current += 1;
        const silentForMs = Date.now() - lastOkTsRef.current;
        if (
          failCountRef.current >= HEALTH_OFFLINE_FAIL_THRESHOLD &&
          silentForMs >= HEALTH_OFFLINE_MIN_SILENCE_MS
        ) {
          setStatus("offline");
        }
      }
    } catch {
      if (!mountedRef.current) return;
      failCountRef.current += 1;
      const silentForMs = Date.now() - lastOkTsRef.current;
      if (
        failCountRef.current >= HEALTH_OFFLINE_FAIL_THRESHOLD &&
        silentForMs >= HEALTH_OFFLINE_MIN_SILENCE_MS
      ) {
        setStatus("offline");
      }
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current) setChecking(false);
    }
  }, [healthUrls]);

  useEffect(() => {
    mountedRef.current = true;
    failCountRef.current = 0;
    probe();
    const onVisibilityChange = () => {
      if (!document.hidden) {
        void probe();
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    const id = setInterval(() => {
      if (!document.hidden) {
        void probe();
      }
    }, HEALTH_PROBE_INTERVAL_MS);
    return () => {
      mountedRef.current = false;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      clearInterval(id);
    };
  }, [probe]);

  if (status !== "offline") return null;

  return (
    <div className="status-banner sticky top-0 z-50 mb-4 rounded-xl px-4 py-3 text-sm shadow-lg">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="font-semibold">后端连接已中断</div>
          <div className="text-xs opacity-80">
            当前无法访问 API：<span className="font-mono">{API_BASE}</span>。请确认后端已启动，再点击“一键重连”。
          </div>
        </div>
        <div className="flex gap-2">
          <button
            className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-60"
            onClick={probe}
            disabled={checking}
          >
            {checking ? "检测中..." : "一键重连"}
          </button>
          <button
            className="rounded-lg border border-rose-300 px-3 py-1.5 text-xs font-semibold text-rose-700 hover:bg-rose-100"
            onClick={() => window.location.reload()}
          >
            刷新页面
          </button>
        </div>
      </div>
    </div>
  );
}

