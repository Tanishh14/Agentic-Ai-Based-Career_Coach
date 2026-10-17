"use client";
import { useSearchParams } from "next/navigation";
import { Fragment, Suspense, useEffect, useState } from "react";
import { Bot, CheckCircle2, CircleAlert, Cog, Eye, ListTree, Radio, ShieldCheck, Wrench } from "lucide-react";
import { api, fmtDate, timeAgo } from "@/lib/api";
import { useAction, useApi, useEventRefresh, useEvents } from "@/lib/app";
import { Card, Empty, Loading, PageHead, Tabs } from "@/components/ui";

const ICON: Record<string, any> = { observe: Eye, plan: ListTree, tool_call: Wrench, verify: ShieldCheck };

function RunDetail({ id }: { id: string }) {
  const r = useApi<any>(`/api/agent/runs/${id}`, [id]);
  useEventRefresh(["agent_action", "agent_run_finished"], (_, p) => { if (p.run_id === id) r.reload(true); });
  if (r.loading) return <Loading rows={6} />;
  const d = r.data;
  if (!d) return null;
  return (
    <Card title={d.trigger} actions={<span className={`badge ${d.status === "succeeded" ? "ok" : d.status === "failed" ? "bad" : "info"}`}>{d.status}</span>}>
      <div className="small muted">Goal: {d.goal}</div>
      <div className="faint xs mt4">{fmtDate(d.started_at)} → {d.ended_at ? fmtDate(d.ended_at) : "running"} · {d.actions.length} step(s) · {d.llm_calls} LLM call(s)</div>
      {d.summary && <div className="quote mt8">{d.summary}</div>}
      {d.error_summary && <div className="alert bad mt8">{d.error_summary}</div>}
      <div className="timeline mt12">
        {d.actions.map((a: any) => {
          const Icon = ICON[a.action] || Cog;
          return (
            <div key={a.seq} className="tl-item">
              <span className={`tl-icon ${a.status === "ok" ? (a.action === "tool_call" ? "info" : "") : a.status === "error" || a.status === "rejected" ? "bad" : "warn"}`}><Icon size={11} /></span>
              <div>
                <div className="small"><b>{a.tool || a.action}</b> {a.input_summary && <span className="muted">· {a.input_summary}</span>} <span className="faint xs">{a.duration_ms ? `${a.duration_ms} ms` : ""}</span></div>
                <div className="small muted">{a.result_summary}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="faint xs mt8">User-safe action trace: tools, inputs at a high level, results and errors. Model reasoning is never stored or shown.</div>
    </Card>
  );
}

function RunsTab() {
  const params = useSearchParams();
  const runs = useApi<any[]>("/api/agent/runs?limit=80");
  const [sel, setSel] = useState<string | null>(params.get("run"));
  useEventRefresh(["agent_run_started", "agent_run_finished"], () => runs.reload(true));
  useEffect(() => { if (!sel && runs.data?.[0]) setSel(runs.data[0].id); }, [runs.data, sel]);
  return (
    <div className="grid" style={{ gridTemplateColumns: "360px 1fr" }}>
      <Card pad={false} title="Runs">
        {runs.loading ? <div className="card-body"><Loading /></div> : !(runs.data || []).length ? <div className="card-body"><Empty icon={<Bot size={20} />} title="No agent runs yet" /></div> : (
          <div style={{ maxHeight: 720, overflow: "auto" }}>{(runs.data || []).map((r) => (
            <button key={r.id} onClick={() => setSel(r.id)} className={`nav-link ${sel === r.id ? "active" : ""}`} style={{ width: "100%", border: 0, borderRadius: 0, background: sel === r.id ? undefined : "transparent", cursor: "pointer", textAlign: "left", alignItems: "flex-start" }}>
              {r.status === "succeeded" ? <CheckCircle2 size={14} color="var(--ok)" /> : r.status === "failed" ? <CircleAlert size={14} color="var(--bad)" /> : <span className="spinner" style={{ width: 12, height: 12 }} />}
              <span className="grow" style={{ minWidth: 0 }}><span className="ellipsis" style={{ display: "block" }}>{r.trigger}</span><span className="xs faint">{timeAgo(r.started_at)} · {r.step_count} steps</span></span>
            </button>
          ))}</div>
        )}
      </Card>
      <div>{sel ? <RunDetail id={sel} /> : <Empty title="Select a run" />}</div>
    </div>
  );
}

function JobsTab() {
  const jobs = useApi<any>("/api/agent/jobs");
  const { run } = useAction();
  useEventRefresh(["job_status"], () => jobs.reload(true));
  if (jobs.loading) return <Loading />;
  return (
    <Card title="Durable background queue (SQLite)" pad={false} actions={<span className="row wrap">{Object.entries(jobs.data?.stats || {}).map(([k, v]) => <span key={k} className="badge">{k}: {String(v)}</span>)}</span>}>
      <div className="table-wrap"><table className="tbl"><thead><tr><th>Type</th><th>Status</th><th>Attempts</th><th>Next run</th><th>Error</th><th /></tr></thead>
        <tbody>{(jobs.data?.recent || []).map((j: any) => (
          <tr key={j.id}><td className="mono xs">{j.job_type}</td><td><span className={`badge ${j.status === "succeeded" ? "ok" : j.status === "dead" ? "bad" : j.status === "running" ? "info" : ""}`}>{j.status}</span></td>
            <td>{j.attempts}/{j.max_attempts}</td><td className="small faint">{j.status === "queued" ? fmtDate(j.next_run_at) : "—"}</td><td className="xs muted" style={{ maxWidth: 380 }}>{j.error_summary}</td>
            <td>{j.status === "dead" && <button className="btn sm" onClick={() => run("r", async () => { await api.post(`/api/agent/jobs/${j.id}/retry`); jobs.reload(true); }, "Re-queued")}>Retry</button>}</td></tr>
        ))}</tbody></table></div>
    </Card>
  );
}

function StreamTab() {
  const recent = useApi<any[]>("/api/agent/events/recent?limit=150");
  const { connected } = useEvents();
  useEventRefresh(["agent_action", "scan_progress", "assessment_created", "change_detected", "job_status", "interview_answer", "gap_task_updated"], () => recent.reload(true));
  return (
    <Card title={<span className="row"><Radio size={15} />Live event stream</span>} actions={<span className="row small"><span className={`dot ${connected ? "live" : "bad"}`} />{connected ? "SSE connected" : "reconnecting"}</span>} pad={false}>
      <div className="table-wrap" style={{ maxHeight: 700 }}><table className="tbl"><tbody>{(recent.data || []).map((e) => (
        <tr key={e.seq}><td className="mono xs faint">#{e.seq}</td><td className="small bold">{e.type}</td><td className="xs muted mono" style={{ maxWidth: 680, overflowWrap: "anywhere" }}>{JSON.stringify(e.payload).slice(0, 260)}</td><td className="xs faint">{timeAgo(e.created_at)}</td></tr>
      ))}</tbody></table></div>
    </Card>
  );
}

function AuditTab() {
  const a = useApi<any[]>("/api/audit?limit=200");
  const tools = useApi<any>("/api/agent/tools");
  return (
    <div className="grid g2">
      <Card title="Audit trail (consent, external writes, deletions)" pad={false}>
        {!(a.data || []).length ? <div className="card-body muted small">No audit events.</div> : <div className="table-wrap" style={{ maxHeight: 640 }}><table className="tbl"><tbody>{(a.data || []).map((e) => (
          <tr key={e.id}><td className="small bold">{e.kind}</td><td className="xs mono muted" style={{ overflowWrap: "anywhere" }}>{JSON.stringify(e.detail).slice(0, 200)}</td><td className="xs faint">{fmtDate(e.created_at)}</td></tr>
        ))}</tbody></table></div>}
      </Card>
      <Card title="Typed tools available to the agent">
        <div className="small muted">Standing goal: {tools.data?.standing_goal}</div>
        <dl className="kv mt12">{Object.entries(tools.data?.tools || {}).map(([k, v]: any) => <Fragment key={k}><dt className="mono xs">{k}</dt><dd className="small">{v}</dd></Fragment>)}</dl>
      </Card>
    </div>
  );
}

function ActivityInner() {
  const [tab, setTab] = useState("runs");
  return (
    <>
      <PageHead title="Agent activity & audit trail" subtitle="Every autonomous action — webhook verified, commit compared, detectors run, evidence accepted/rejected, rows reassessed, diff persisted — is recorded with timestamps." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "runs", label: "Agent runs" }, { key: "jobs", label: "Background jobs" }, { key: "stream", label: "Event stream" }, { key: "audit", label: "Audit & tools" }]} />
      {tab === "runs" && <RunsTab />}
      {tab === "jobs" && <JobsTab />}
      {tab === "stream" && <StreamTab />}
      {tab === "audit" && <AuditTab />}
    </>
  );
}

export default function Activity() {
  return <Suspense fallback={<Loading />}><ActivityInner /></Suspense>;
}
