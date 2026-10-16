"use client";
import React, { useEffect } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, Info, Loader2, X, XCircle } from "lucide-react";
import { ApiError, Verdict, VERDICT_LABEL } from "@/lib/api";

export function PageHead({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {actions && <div className="row wrap">{actions}</div>}
    </div>
  );
}

export function Card({ title, icon, actions, children, pad = true, className = "" }: { title?: React.ReactNode; icon?: React.ReactNode; actions?: React.ReactNode; children?: React.ReactNode; pad?: boolean; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          <h3>{icon}{title}</h3>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      {pad ? <div className="card-body">{children}</div> : children}
    </section>
  );
}

export function Stat({ label, value, hint, icon }: { label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="card stat">
      <div className="label">{icon}{label}</div>
      <div className="value">{value}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function VerdictBadge({ v }: { v: Verdict | string }) {
  const icon = v === "DEMONSTRATED" ? <CheckCircle2 size={13} /> : v === "PARTIAL" ? <AlertTriangle size={13} /> : v === "CLAIMED_ONLY" ? <Info size={13} /> : <XCircle size={13} />;
  return <span className={`verdict ${v}`}>{icon}{VERDICT_LABEL[v as Verdict] || v}</span>;
}

const SOURCE_STYLE: Record<string, [string, string]> = {
  CODE_IMPLEMENTATION: ["info", "Code"],
  CODE_TEST: ["teal", "Test code"],
  CODE_CONFIG: ["accent", "Config"],
  DEPENDENCY_DECLARATION: ["", "Dependency only"],
  CLAIMED_RESUME: ["violet", "Resume claim"],
  CLAIMED_PROFILE: ["violet", "Profile claim"],
  PROBLEM_SOLVING_SIGNAL: ["orange", "LeetCode signal"],
  INTERVIEW_EVIDENCE: ["pink", "Interview"],
  UNDERSTANDING_EVIDENCE: ["pink", "Code defense"],
  RUNTIME_EXECUTION: ["ok", "Runtime run"],
  RECOMMENDED: ["warn", "Recommendation"],
};
export function SourceBadge({ type }: { type: string }) {
  const [cls, label] = SOURCE_STYLE[type] || ["", type];
  return <span className={`badge sq ${cls}`}>{label}</span>;
}

export function PriorityBadge({ p }: { p: string }) {
  const cls = p === "must_have" ? "bad" : p === "preferred" ? "info" : "";
  return <span className={`badge sq ${cls}`}>{p === "must_have" ? "Must-have" : p === "preferred" ? "Preferred" : "Unclear"}</span>;
}

export function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = { accepted: "ok", ambiguous: "warn", rejected: "bad", ok: "ok", running: "info", queued: "", failed: "bad", dead: "bad", succeeded: "ok" };
  return <span className={`dot ${map[status] || ""}`} />;
}

export function Spinner({ label }: { label?: string }) {
  return <span className="row muted small"><span className="spinner" />{label}</span>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="col" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => <div key={i} className="skeleton" style={{ height: 18, width: `${90 - i * 12}%` }} />)}
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: ApiError | Error | null; onRetry?: () => void }) {
  if (!error) return null;
  const e = error as ApiError;
  const tone = e.code === "not_configured" ? "warn" : "bad";
  return (
    <div className={`alert ${tone}`} role="alert">
      <AlertTriangle size={16} />
      <div className="grow">
        <b>{e.code === "not_configured" ? "Not configured" : e.code === "provider_rate_limited" ? "Rate limited" : "Something went wrong"}</b>
        <div>{e.message}</div>
        {e.action && <div className="small mt4">{e.action}</div>}
        {e.retryAfter ? <div className="small mt4">Retry after ~{e.retryAfter}s.</div> : null}
      </div>
      {onRetry && <button className="btn sm" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function Empty({ icon, title, children, action }: { icon?: React.ReactNode; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="icon">{icon || <Info size={20} />}</div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Alert({ tone = "info", children, icon }: { tone?: "info" | "warn" | "bad" | "ok"; children: React.ReactNode; icon?: React.ReactNode }) {
  return <div className={`alert ${tone}`}>{icon || (tone === "ok" ? <CheckCircle2 size={16} /> : tone === "info" ? <Info size={16} /> : <AlertTriangle size={16} />)}<div className="grow">{children}</div></div>;
}

export function Ext({ href, children, className = "" }: { href?: string | null; children: React.ReactNode; className?: string }) {
  if (!href) return <span className={className}>{children}</span>;
  return <a href={href} target="_blank" rel="noreferrer noopener" className={`row ${className}`} style={{ display: "inline-flex", gap: 4 }}>{children}<ExternalLink size={12} /></a>;
}

export function Overlay({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return <div className="overlay" onClick={onClose} />;
}

export function Drawer({ title, subtitle, onClose, children }: { title: React.ReactNode; subtitle?: React.ReactNode; onClose: () => void; children: React.ReactNode }) {
  return (
    <>
      <Overlay onClose={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div className="grow"><h2>{title}</h2>{subtitle && <div className="muted small mt4">{subtitle}</div>}</div>
          <button className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={18} /></button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>
  );
}

export function Modal({ title, onClose, children, footer }: { title: React.ReactNode; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <>
      <Overlay onClose={onClose} />
      <div className="modal" role="dialog" aria-modal="true">
        <div className="modal-head"><h2>{title}</h2><button className="btn ghost icon" onClick={onClose} aria-label="Close"><X size={18} /></button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </>
  );
}

export function CodeView({ code, startLine = 1, highlight, header }: { code: string; startLine?: number; highlight?: [number, number]; header?: React.ReactNode }) {
  const lines = (code || "").replace(/\r/g, "").split("\n");
  return (
    <div className="code">
      {header && <div className="code-head">{header}</div>}
      <table><tbody>
        {lines.map((l, i) => {
          const n = startLine + i;
          const hl = highlight && n >= highlight[0] && n <= highlight[1];
          return <tr key={i} className={hl ? "hl" : ""}><td className="ln">{n}</td><td className="src">{l || " "}</td></tr>;
        })}
      </tbody></table>
    </div>
  );
}

export function DiffView({ diff }: { diff: string }) {
  return (
    <div className="code"><pre style={{ margin: 0, padding: 12 }}>
      {diff.split("\n").map((l, i) => (
        <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "diff-add" : l.startsWith("-") && !l.startsWith("---") ? "diff-del" : l.startsWith("@@") ? "diff-hunk" : ""}>{l || " "}</div>
      ))}
    </pre></div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { key: string; label: React.ReactNode }[]; value: string; onChange: (k: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => <button key={t.key} role="tab" aria-selected={value === t.key} className={`tab ${value === t.key ? "active" : ""}`} onClick={() => onChange(t.key)}>{t.label}</button>)}
    </div>
  );
}

export function Switch({ checked, onChange, label, help, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: React.ReactNode; help?: React.ReactNode; disabled?: boolean }) {
  return (
    <label className="row start" style={{ gap: 12, cursor: disabled ? "not-allowed" : "pointer" }}>
      <span className="switch"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span /></span>
      <span className="col" style={{ gap: 2 }}><span className="bold small">{label}</span>{help && <span className="faint small">{help}</span>}</span>
    </label>
  );
}

export function Busy({ on, children }: { on: boolean; children: React.ReactNode }) {
  return <>{on ? <Loader2 size={14} className="spin" style={{ animation: "spin .7s linear infinite" }} /> : null}{children}</>;
}

export function Progress({ value, tone }: { value: number; tone?: "ok" | "warn" | "bad" }) {
  return <div className={`progress ${tone || ""}`}><div style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div>;
}
