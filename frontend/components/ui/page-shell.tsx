"use client";

import type { ReactNode } from "react";

export function PageShell({ children }: { children: ReactNode }) {
  return <div className="dashboard-shell">{children}</div>;
}
