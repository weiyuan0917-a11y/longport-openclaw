import "./globals.css";
import type { Metadata } from "next";
import { Nav } from "@/components/nav";
import { BackendConnectionBanner } from "@/components/backend-connection-banner";

export const metadata: Metadata = {
  title: "LongPort UI Console",
  description: "Trading dashboard and controls",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen">
        <div className="relative min-h-screen bg-[radial-gradient(ellipse_70%_70%_at_15%_-15%,rgba(56,189,248,0.2),transparent),radial-gradient(ellipse_70%_70%_at_100%_-10%,rgba(124,58,237,0.2),transparent)]">
          <div className="relative mx-auto flex min-h-screen max-w-[1440px] gap-6 p-6">
            <Nav />
            <main className="flex-1 min-w-0">
              <BackendConnectionBanner />
              {children}
            </main>
          </div>
        </div>
      </body>
    </html>
  );
}
