"use client";
import Link from "next/link";
import { fmtDate } from "@/lib/api";
import { useApi } from "@/lib/app";
import { Card, Empty, Loading, PageHead } from "@/components/ui";

export default function Reports() {
  const sessions = useApi<any[]>("/api/interviews");
  const done = (sessions.data || []).filter((s) => s.status === "completed");
  const max = 100;
  return (
    <>
      <PageHead title="Interview reports" subtitle="Completed mock and code-defense sessions with per-question feedback and your trend over time." />
      {sessions.loading ? <Loading /> : !done.length ? <Card><Empty title="No completed interviews" action={<Link href="/interview" className="btn primary">Start a mock interview</Link>} /></Card> : (
        <>
          <Card title="Trend">
            <div className="row" style={{ alignItems: "flex-end", gap: 10, height: 140 }}>
              {[...done].reverse().map((s) => (
                <Link key={s.id} href={`/interview/${s.id}/report`} className="col" style={{ alignItems: "center", gap: 4, flex: 1, color: "inherit" }} title={`${s.job?.title || s.kind} · ${fmtDate(s.ended_at)}`}>
                  <b className="xs">{s.overall != null ? Math.round(s.overall) : "—"}</b>
                  <div style={{ width: "100%", maxWidth: 40, height: `${((s.overall || 0) / max) * 100}px`, background: s.kind === "code_defense" ? "var(--pink)" : "var(--accent)", borderRadius: 4 }} />
                  <span className="xs faint">{new Date(s.ended_at || s.created_at).toLocaleDateString()}</span>
                </Link>
              ))}
            </div>
            <div className="faint xs mt8">Indigo = mock interview, pink = code defense. Heuristic coaching score (mean rubric × 20).</div>
          </Card>
          <Card pad={false} className="mt16">
            <div className="table-wrap"><table className="tbl"><thead><tr><th>Session</th><th>Type</th><th>Completed</th><th>Score</th><th /></tr></thead>
              <tbody>{done.map((s) => <tr key={s.id}><td className="bold">{s.job?.title || "Your code"}{s.job?.company ? ` · ${s.job.company}` : ""}</td><td>{s.kind === "code_defense" ? "Code defense" : "Mock interview"}</td><td className="small">{fmtDate(s.ended_at)}</td><td className="bold">{s.overall != null ? Math.round(s.overall) : "—"}</td><td><Link className="btn sm" href={`/interview/${s.id}/report`}>Open</Link></td></tr>)}</tbody></table></div>
          </Card>
        </>
      )}
    </>
  );
}
