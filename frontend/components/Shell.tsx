"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { useEffect, useState } from "react";
import {
  Activity, BadgeCheck, Briefcase, Code2, FileSearch, FolderGit2, GitCompare, Gauge, ListChecks, Mic, Moon, Search, Settings,
  Sun, UserSquare2, ClipboardList,
} from "lucide-react";
import { AppStateProvider, EventsProvider, ToastProvider, useAppState, useEvents } from "@/lib/app";
import { timeAgo } from "@/lib/api";

const NAV: { group: string; items: { href: string; label: string; icon: any }[] }[] = [
  { group: "Overview", items: [{ href: "/", label: "Readiness summary", icon: Gauge }] },
  { group: "Phase 1 · Core loop", items: [
    { href: "/jobs", label: "Job discovery", icon: Search },
    { href: "/jobs/target", label: "Job & requirements", icon: Briefcase },
    { href: "/repos", label: "Repositories & scope", icon: FolderGit2 },
    { href: "/gaps", label: "Evidence & skill gaps", icon: FileSearch },
    { href: "/history", label: "History & commit diffs", icon: GitCompare },
  ]},
  { group: "Phase 2 · Signals & interviews", items: [
    { href: "/profile", label: "Profile claims & LeetCode", icon: UserSquare2 },
    { href: "/interview", label: "Mock interview", icon: Mic },
    { href: "/interview/reports", label: "Interview reports", icon: ClipboardList },
  ]},
  { group: "Phase 3 · Proof", items: [
    { href: "/defense", label: "Code defense", icon: Code2 },
    { href: "/tasks", label: "Gap-to-proof & roadmap", icon: ListChecks },
    { href: "/passport", label: "Skill passport", icon: BadgeCheck },
  ]},
  { group: "System", items: [
    { href: "/activity", label: "Agent activity & audit", icon: Activity },
    { href: "/settings", label: "Integrations & privacy", icon: Settings },
  ]},
];

function Sidebar() {
  const path = usePathname() || "/";
  const isActive = (href: string) => href === "/" ? path === "/" : path === href || (path.startsWith(href + "/") && !NAV.some((g) => g.items.some((i) => i.href !== href && i.href.startsWith(href + "/") && path.startsWith(i.href))));
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <div className="brand-mark">CC</div>
        <div><div className="brand-name">AI Career Coach</div><div className="brand-sub">Evidence-driven · W-PS-2</div></div>
      </div>
      {NAV.map((g) => (
        <div key={g.group} className="nav-group">
          <div className="nav-label">{g.group}</div>
          {g.items.map((i) => {
            const Icon = i.icon;
            return <Link key={i.href} href={i.href} className={`nav-link ${isActive(i.href) ? "active" : ""}`}><Icon size={16} />{i.label}</Link>;
          })}
        </div>
      ))}
      <SidebarFoot />
    </nav>
  );
}

function SidebarFoot() {
  const { status } = useAppState();
  const w = status?.worker;
  return (
    <div className="sidebar-foot col" style={{ gap: 6 }}>
      <div className="row"><span className={`dot ${w?.running ? "ok" : "bad"}`} />Worker {w?.running ? `· heartbeat ${timeAgo(w.last_heartbeat)}` : "not running"}</div>
      <div className="row"><span className={`dot ${status?.llm?.configured ? "ok" : "warn"}`} />LLM {status?.llm?.configured ? status.llm.providers[0].model : "not configured"}</div>
      <div className="row"><span className={`dot ${status?.github?.token_present ? "ok" : "warn"}`} />GitHub {status?.github?.config?.login ? `@${status.github.config.login}` : status?.github?.token_present ? "token (env)" : "anonymous"}</div>
    </div>
  );
}

function Topbar() {
  const { trackedJob, status, error } = useAppState();
  const { connected, lastEvent } = useEvents();
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => { try { setTheme(localStorage.getItem("theme")); } catch { /* ignore */ } }, []);
  useEffect(() => {
    if (!theme) { document.documentElement.removeAttribute("data-theme"); return; }
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* ignore */ }
  }, [theme]);
  const dark = theme === "dark" || (!theme && typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  return (
    <header className="topbar">
      <div className="target">
        <Briefcase size={15} />
        {trackedJob ? <>Target job: <b>{trackedJob.title || "Untitled"}{trackedJob.company ? ` · ${trackedJob.company}` : ""}</b></> : <span>No target job selected</span>}
      </div>
      <div className="spacer" />
      {error && <span className="badge bad">Backend unreachable</span>}
      {status && !status.worker?.running && <span className="badge warn" title="Start the worker to enable automatic reassessment">Worker offline</span>}
      <span className="row small muted" title={lastEvent ? `${lastEvent.type} ${timeAgo(lastEvent.at)}` : ""}>
        <span className={`dot ${connected ? "live" : "bad"}`} />{connected ? "Live" : "Reconnecting"}
      </span>
      <button className="btn ghost icon" aria-label="Toggle theme" onClick={() => setTheme(dark ? "light" : "dark")}>{dark ? <Sun size={16} /> : <Moon size={16} />}</button>
    </header>
  );
}

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  if (path.startsWith("/p/")) {
    return <ToastProvider>{children}</ToastProvider>;
  }
  return (
    <ToastProvider>
      <EventsProvider>
        <AppStateProvider>
          <div className="shell">
            <Sidebar />
            <div className="main">
              <Topbar />
              <main className="content">{children}</main>
            </div>
          </div>
        </AppStateProvider>
      </EventsProvider>
    </ToastProvider>
  );
}

