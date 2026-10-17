"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api, fmtDate } from "@/lib/api";
import { useAction, useApi } from "@/lib/app";
import { Card, ErrorBox, Ext, Loading, PageHead, Spinner } from "@/components/ui";
import Feedback from "@/components/Feedback";

export default function Report() {
  const { id } = useParams<{ id: string }>();
  const session = useApi<any>(`/api/interviews/${id}`, [id]);
  const report = useApi<any>(`/api/interviews/${id}/report`, [id]);
  const { busy, run } = useAction();
  const [open, setOpen] = useState<string | null>(null);
  if (session.loading || report.loading) return <Loading rows={8} />;
  if (session.error) return <ErrorBox error={session.error} />;
  const s = session.data, r = report.data;
  const trendMax = 100;
  return (
    <>
      <PageHead title="Interview report" subtitle={<>{s.kind === "code_defense" ? "Code defense" : "Mock interview"} for {s.job ? <b>{s.job.title}</b> : "your code"} · {fmtDate(s.started_at)} · plan: {s.plan_generator}</>}
        actions={<>
          {s.status !== "completed" && <Link className="btn" href={`/interview/${id}`}>Back to room</Link>}
          <button className="btn danger" disabled={!!busy} onClick={() => run("dm", () => api.del(`/api/interviews/${id}/media`), (x: any) => `${x.deleted} recording(s) deleted`)}>{busy && <Spinner />}Delete recordings</button>
        </>} />
      <div className="grid g4">
        <div className="card stat"><div className="label">Overall (heuristic)</div><div className="value">{r.overall != null ? Math.round(r.overall) : "—"}</div><div className="hint">{r.answered} answered · {r.skipped} skipped</div></div>
        {Object.entries(r.dimension_averages || {}).slice(0, 3).map(([k, v]: any) => <div key={k} className="card stat"><div className="label">{k.replace("_", " ")}</div><div className="value">{v}/5</div></div>)}
      </div>
      <div className="faint xs mt8">{r.overall_formula}</div>
      <div className="grid g2 mt16">
        <Card title="Recurring gaps">
          {!(r.recurring_gaps || []).length ? <div className="muted small">None recorded.</div> : <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>{r.recurring_gaps.map((g: any) => <li key={g.point}>{g.point} <span className="faint">×{g.count}</span></li>)}</ul>}
        </Card>
        <Card title="Readiness trend (completed sessions)">
          {!(r.trend || []).length ? <div className="muted small">Complete more sessions to see a trend.</div> : (
            <div className="row" style={{ alignItems: "flex-end", gap: 8, height: 120 }}>
              {r.trend.map((t: any, i: number) => (
                <div key={i} className="col" style={{ alignItems: "center", gap: 4, flex: 1 }} title={fmtDate(t.ended_at)}>
                  <b className="xs">{t.overall != null ? Math.round(t.overall) : "—"}</b>
                  <div style={{ width: "100%", maxWidth: 36, height: `${((t.overall || 0) / trendMax) * 90}px`, background: t.session_id === id ? "var(--accent)" : "var(--border-strong)", borderRadius: 4 }} />
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <h2 className="mt24" style={{ marginBottom: 10 }}>Question by question</h2>
      <div className="stack">
        {s.questions.map((q: any, i: number) => (
          <Card key={q.id} title={<span>Q{i + 1} · {q.qtype.replaceAll("_", " ")} {q.parent_question_id && <span className="badge pink">follow-up</span>}</span>}
            actions={<span className="row">{q.answer?.evaluation ? <b>{Math.round(q.answer.evaluation.overall)}</b> : <span className="faint small">{q.status}</span>}<button className="btn sm" onClick={() => setOpen(open === q.id ? null : q.id)}>{open === q.id ? "Hide" : "Details"}</button></span>}>
            <div>{q.prompt}</div>
            <div className="xs faint mt4">Provenance: {q.provenance?.kind}{q.provenance?.quote ? ` · “${String(q.provenance.quote).slice(0, 100)}…”` : ""} {q.provenance?.permalink && <Ext href={q.provenance.permalink} className="xs">code</Ext>}</div>
            {open === q.id && q.answer && <div className="mt12"><Feedback answer={q.answer} question={q} /></div>}
          </Card>
        ))}
      </div>
      <div className="faint xs mt16">Scores are coaching signals from the rubric, not predictions of hiring outcomes. Interview performance is stored as interview/understanding evidence and never changes repository verdicts.</div>
    </>
  );
}
