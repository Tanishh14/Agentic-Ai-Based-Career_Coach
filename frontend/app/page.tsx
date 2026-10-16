"use client";
import Link from "next/link";
import { Activity, ArrowRight, CheckCircle2, Circle, FolderGit2, Gauge, GitCommit, Radio, Target } from "lucide-react";
import { useApi, useAppState, useEventRefresh } from "@/lib/app";
import { Assessment, fmtDate, shortSha, timeAgo } from "@/lib/api";
import { Alert, Card, Empty, ErrorBox, Loading, PageHead, Progress, Stat, VerdictBadge } from "@/components/ui";

function Check({ ok, title, children, href, cta }: { ok: boolean; title: string; children?: React.ReactNode; href: string; cta: string }) {
  return (
    <div className="row start" style={{ gap: 12, padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      {ok ? <CheckCircle2 size={18} color="var(--ok)" /> : <Circle size={18} color="var(--text-3)" />}
      <div className="grow">
        <div className="bold small">{title}</div>
        {children && <div className="faint small">{children}</div>}
      </div>
      {!ok && <Link href={href} className="btn sm">{cta}</Link>}
    </div>
  );
}

export default function Overview() {
  const { status, trackedJob, error } = useAppState();
  const latest = useApi<Assessment | null>(trackedJob ? `/api/assessments/job/${trackedJob.id}/latest` : null, [trackedJob?.id]);
  const scopes = useApi<any[]>("/api/github/scopes");
  const runs = useApi<any[]>("/api/agent/runs?limit=8");
  const roadmap = useApi<any>(trackedJob ? `/api/insights/roadmap/${trackedJob.id}` : null, [trackedJob?.id]);
  useEventRefresh(["assessment_created", "agent_run_finished", "scan_progress"], () => { latest.reload(true); scopes.reload(true); runs.reload(true); roadmap.reload(true); });

  const a = latest.data;
  const counts = a?.summary?.counts || {};
  const cov = a?.summary?.coverage_indicator;
  const monitoring = (scopes.data || []).length > 0 && status?.worker?.running;

  return (
    <>
      <PageHead title="Readiness summary" subtitle="What your real GitHub work demonstrates for your target job — kept current automatically when you push code." />
      {error && <ErrorBox error={error} />}
      <div className="grid g4">
        <Stat icon={<Gauge size={14} />} label="Requirement coverage" value={cov != null ? `${cov}%` : "—"} hint={a ? `assessment v${a.version} · ${timeAgo(a.created_at)}` : "no assessment yet"} />
        <Stat icon={<CheckCircle2 size={14} />} label="Demonstrated" value={counts.DEMONSTRATED ?? "—"} hint={a ? `of ${a.summary.requirements} requirements` : undefined} />
        <Stat icon={<FolderGit2 size={14} />} label="Repositories monitored" value={status ? (scopes.data || []).length : "—"} hint={status?.worker?.running ? `polling every ${status.worker.poll_interval_seconds}s` : "worker offline"} />
        <Stat icon={<Activity size={14} />} label="Agent runs" value={runs.data ? runs.data.length : "—"} hint={runs.data?.[0] ? `last ${timeAgo(runs.data[0].started_at)}` : undefined} />
      </div>

      <div className="grid g-side mt16">
        <div className="stack">
          <Card title="Target job readiness" icon={<Target size={16} />} actions={trackedJob && <Link className="btn sm" href="/gaps">Open gap matrix <ArrowRight size={14} /></Link>}>
            {!trackedJob ? (
              <Empty title="No target job yet" action={<Link className="btn primary" href="/jobs">Find or add a real job</Link>}>
                Pick a real internship/job posting (URL, paste, upload or live search). Requirements are extracted with verbatim quotes.
              </Empty>
            ) : latest.loading ? <Loading /> : latest.error ? <ErrorBox error={latest.error} onRetry={() => latest.reload()} /> : !a ? (
              <Alert>Requirements are extracted. Add a repository scope to compute the first assessment.</Alert>
            ) : (
              <div className="stack">
                <div className="row between wrap">
                  <div>
                    <div className="bold">{trackedJob.title} {trackedJob.company && <span className="muted">· {trackedJob.company}</span>}</div>
                    <div className="faint small">Source: {trackedJob.source_url ? <a href={trackedJob.source_url} target="_blank" rel="noreferrer">{trackedJob.platform}</a> : "pasted text"} · retrieved {fmtDate(trackedJob.retrieved_at)}</div>
                  </div>
                  <div style={{ width: 260 }}>
                    <div className="row between small"><span className="muted">Coverage indicator</span><b>{cov}%</b></div>
                    <Progress value={cov || 0} tone={cov >= 70 ? "ok" : cov >= 40 ? "warn" : "bad"} />
                  </div>
                </div>
                <div className="row wrap">
                  {Object.entries(counts).map(([k, v]) => <span key={k} className="row small" style={{ gap: 6 }}><VerdictBadge v={k} /> <b>{String(v)}</b></span>)}
                </div>
                <div className="faint xs">{a.summary.disclaimer} Formula: {a.summary.coverage_formula}</div>
                <div className="table-wrap card flat">
                  <table className="tbl"><thead><tr><th>Requirement</th><th>Verdict</th><th>Why</th></tr></thead>
                    <tbody>{a.rows.slice(0, 6).map((r) => (
                      <tr key={r.id}><td className="bold">{r.requirement.display_name}</td><td><VerdictBadge v={r.verdict} /></td><td className="muted small">{r.rationale.slice(0, 140)}{r.rationale.length > 140 ? "…" : ""}</td></tr>
                    ))}</tbody></table>
                </div>
              </div>
            )}
          </Card>

          <Card title="Next best actions" icon={<ArrowRight size={16} />} actions={<Link href="/tasks" className="btn sm">Roadmap</Link>}>
            {!roadmap.data?.items?.length ? <div className="muted small">{trackedJob ? "No open gaps for the target job — or no assessment yet." : "Select a target job first."}</div> : (
              <div className="col" style={{ gap: 10 }}>
                {roadmap.data.items.slice(0, 3).map((it: any) => (
                  <div key={it.row_id} className="card flat sunken" style={{ padding: 12 }}>
                    <div className="row between"><b>{it.skill}</b><span className="row"><VerdictBadge v={it.verdict} /><span className="badge sq">effort {it.effort}</span></span></div>
                    <div className="small muted mt4">{it.recommendation}</div>
                  </div>
                ))}
                <div className="faint xs">{roadmap.data.disclaimer}</div>
              </div>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card title="Setup">
            <Check ok={!!trackedJob} title="Real target job" href="/jobs" cta="Add job">{trackedJob ? `${trackedJob.title}` : "URL, paste, upload, or live search"}</Check>
            <Check ok={!!status?.github?.token_present} title="GitHub token" href="/settings" cta="Connect">{status?.github?.config?.login ? `@${status.github.config.login}` : "Optional for public repos; required for private repos, issues, webhooks"}</Check>
            <Check ok={(scopes.data || []).length > 0} title="Repository scope analysed" href="/repos" cta="Add repo">{(scopes.data || []).map((s) => s.repository.full_name).join(", ") || "Select repositories, branch and paths"}</Check>
            <Check ok={!!status?.worker?.running} title="Background worker running" href="/settings" cta="How to">{status?.worker?.running ? "Automatic change detection active" : "Run `python run.py` to start API + worker + UI"}</Check>
            <Check ok={!!status?.llm?.configured} title="LLM provider" href="/settings" cta="Configure">{status?.llm?.configured ? status.llm.providers.map((p: any) => p.model).join(" → ") : "Optional: richer extraction, interview questions, evaluation"}</Check>
            <Check ok={!!status?.transcription?.available} title="Speech-to-text" href="/settings" cta="Configure">{status?.transcription?.available ? `${status.transcription.provider} (${status.transcription.model})` : "Typed answers still work"}</Check>
          </Card>

          <Card title="Change detection" icon={<Radio size={16} />}>
            {scopes.loading ? <Loading /> : !(scopes.data || []).length ? <div className="muted small">No repositories monitored yet.</div> : (
              <div className="col" style={{ gap: 10 }}>
                {(scopes.data || []).map((s) => (
                  <div key={s.id} className="col" style={{ gap: 2 }}>
                    <div className="row between"><b className="small">{s.repository.full_name}</b><span className="badge sq">{s.branch}</span></div>
                    <div className="faint xs row"><GitCommit size={12} /> analysed {shortSha(s.last_analyzed_sha)} · polled {timeAgo(s.last_polled_at)}{s.last_event_kind ? ` · last event: ${s.last_event_kind} ${timeAgo(s.last_event_at)}` : ""}</div>
                    {s.last_poll_error && <div className="xs" style={{ color: "var(--bad)" }}>{s.last_poll_error}</div>}
                  </div>
                ))}
                <div className="faint xs">{monitoring ? "Pushes are picked up by webhook (if configured) or by SHA polling — no manual refresh needed." : "Start the worker to enable automatic reassessment."}</div>
              </div>
            )}
          </Card>

          <Card title="Recent agent runs" icon={<Activity size={16} />} actions={<Link href="/activity" className="btn sm">All</Link>}>
            {runs.loading ? <Loading /> : !(runs.data || []).length ? <div className="muted small">No agent activity yet.</div> : (
              <div className="col" style={{ gap: 8 }}>
                {(runs.data || []).slice(0, 6).map((r) => (
                  <Link key={r.id} href={`/activity?run=${r.id}`} className="row start" style={{ gap: 8, color: "inherit" }}>
                    <span className={`dot ${r.status === "succeeded" ? "ok" : r.status === "failed" ? "bad" : "info"}`} style={{ marginTop: 6 }} />
                    <div className="grow"><div className="small bold ellipsis">{r.trigger}</div><div className="faint xs ellipsis">{r.summary || r.error_summary || r.status} · {timeAgo(r.started_at)}</div></div>
                  </Link>
                ))}
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
