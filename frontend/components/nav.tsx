"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";

const NAV_COLLAPSED_KEY = "lp_console_nav_collapsed";

function Icon({
  className,
  children,
}: {
  className?: string;
  children: ReactNode;
}) {
  return (
    <svg
      className={clsx("h-5 w-5 shrink-0 opacity-95", className)}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {children}
    </svg>
  );
}

type NavItem = { href: string; label: string; icon: ReactNode };

const items: NavItem[] = [
  {
    href: "/setup",
    label: "首次配置 Setup",
    icon: (
      <Icon>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
      </Icon>
    ),
  },
  {
    href: "/dashboard",
    label: "总览 Dashboard",
    icon: (
      <Icon>
        <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        <polyline points="9 22 9 12 15 12 15 22" />
      </Icon>
    ),
  },
  {
    href: "/market",
    label: "市场分析",
    icon: (
      <Icon>
        <path d="M3 3v18h18" />
        <path d="M7 16l4-4 4 4 6-8" />
      </Icon>
    ),
  },
  {
    href: "/signals",
    label: "信号中心",
    icon: (
      <Icon>
        <path d="M22 12h-4l-3 9L9 3 6 12H2" />
      </Icon>
    ),
  },
  {
    href: "/backtest",
    label: "回测中心",
    icon: (
      <Icon>
        <path d="M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z" />
        <path d="M12 6v6l4 2" />
      </Icon>
    ),
  },
  {
    href: "/research",
    label: "研究中心",
    icon: (
      <Icon>
        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
      </Icon>
    ),
  },
  {
    href: "/trade",
    label: "交易面板",
    icon: (
      <Icon>
        <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
      </Icon>
    ),
  },
  {
    href: "/options",
    label: "期权交易",
    icon: (
      <Icon>
        <path d="M12 3v18" />
        <path d="M5 9h14M5 15h8" />
      </Icon>
    ),
  },
  {
    href: "/auto-trader",
    label: "Auto Trader",
    icon: (
      <Icon>
        <rect x="4" y="8" width="16" height="10" rx="2" />
        <path d="M9 8V6a3 3 0 0 1 6 0v2" />
        <circle cx="9" cy="14" r="1" fill="currentColor" stroke="none" />
        <circle cx="15" cy="14" r="1" fill="currentColor" stroke="none" />
      </Icon>
    ),
  },
  {
    href: "/notifications",
    label: "通知中心",
    icon: (
      <Icon>
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </Icon>
    ),
  },
];

export function Nav() {
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(NAV_COLLAPSED_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(NAV_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }, []);

  return (
    <aside
      className={clsx(
        "sticky top-4 h-fit shrink-0 overflow-hidden rounded-2xl border border-slate-700/60 bg-slate-900/90 shadow-[0_18px_40px_rgba(2,6,23,0.45)] backdrop-blur-sm transition-[width,padding] duration-300 ease-out",
        collapsed ? "w-[4.25rem] p-2" : "w-64 p-4"
      )}
    >
      <div
        className={clsx(
          "mb-3 flex items-center gap-2",
          collapsed ? "flex-col px-0" : "justify-between px-2"
        )}
      >
        <div
          className={clsx(
            "min-w-0 font-bold tracking-tight text-slate-100 transition-opacity duration-200",
            collapsed ? "text-center text-xs" : "text-base"
          )}
        >
          {collapsed ? (
            <>
              <span className="block" aria-hidden>
                LP
              </span>
              <span className="sr-only">LongPort Console</span>
            </>
          ) : (
            <>
              LongPort
              <span className="ml-1 text-sm font-medium text-slate-400">Console</span>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          title={collapsed ? "展开导航文字" : "收起仅显示图标"}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "展开导航" : "收起导航"}
          className={clsx(
            "rounded-lg border border-slate-600/80 bg-slate-800/60 p-2 text-slate-300 transition hover:border-cyan-500/40 hover:bg-slate-800 hover:text-cyan-200",
            collapsed ? "w-full" : "shrink-0"
          )}
        >
          <Icon className="mx-auto h-4 w-4">
            {/* 收起态：右箭头表示可展开；展开态：左箭头表示可收起 */}
            {collapsed ? (
              <path d="M15 18l-6-6 6-6" />
            ) : (
              <path d="M9 18l6-6-6-6" />
            )}
          </Icon>
        </button>
      </div>

      <nav className="space-y-1">
        {items.map((it) => {
          const active = pathname === it.href;
          return (
            <Link
              key={it.href}
              href={it.href}
              title={it.label}
              className={clsx(
                "flex items-center gap-3 rounded-lg text-sm transition-all duration-200",
                collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2.5",
                active
                  ? "bg-gradient-to-r from-cyan-500 to-indigo-500 text-white shadow-md shadow-cyan-500/30"
                  : "text-slate-300 hover:bg-slate-800/80 hover:text-slate-100"
              )}
            >
              {it.icon}
              <span className={clsx("min-w-0 flex-1 truncate", collapsed && "sr-only")}>{it.label}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
