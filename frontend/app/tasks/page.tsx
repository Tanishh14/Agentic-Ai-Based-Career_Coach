"use client";
import Link from "next/link";
import { useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, FlaskConical, GitPullRequest, ListChecks, Map, TrendingUp } from "lucide-react";
import { api, fmtDate, shortSha, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState, useEventRefresh } from "@/lib/app";
import { Alert, Card, Empty, ErrorBox, Ext, Loading, Modal, PageHead, Spinner, Tabs, VerdictBadge } from "@/components/ui";

function IssueModal({ task, onClose, onDone }: { task: any; onClose: () => void; onDone: () => void }) {
  const [typed, setTyped] = useState("");
  const { busy, run } = useAction();
  const repo = task.repository?.full_name;
  return (
    <Modal title="Create GitHub issue — confirmation required" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Cancel</button>
      <button className="btn primary" disabled={typed !== repo || !!busy} onClick={() => run("issue", async () => { await api.post(`/api/tasks/${task.id}/issue`, { confirm: true, repository_full_name: repo }); onDone(); onClose(); }, "Issue created on GitHub")}>{busy && <Spinner />}Create issue on {repo}</button>
    </>}>
      <Alert tone="warn">This performs a real write to GitHub. Required permission: {task.required_permission}</Alert>
      <div className="field"><label>Target repository</label><div className="mono">{repo}</div></div>
      <div className="field"><label>Title</label><div className="bold">{task.title}</div></div>
      <div className="field"><label>Body (exact text that will be posted)</label><pre className="plain">{task.body}</pre></div>
      <div className="field"><label>Type <span className="mono">{repo}</span> to confirm</label><input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} /></div>
    </Modal>
  );
}

function Criteria({ v }: { v: any }) {
  if (!v) return <span className="faint small">Not verified yet</span>;
  return (
    <div className="col" style={{ gap: 4 }}>
      {v.criteria.map((c: any) => (
        <div key={c.id} className="row start small" style={{ gap: 6 }}>
          {c.passed ? <CheckCircle2 size={14} color="var(--ok)" /> : <CircleDashed size={14} color="var(--text-3)" />}
          <div className="grow">{c.description}{c.optional && <span className="faint"> (optional)</span>} <span className="faint xs">— {c.detail}</span>
            {c.citations?.length > 0 && <div className="row wrap">{c.citations.map((x: any) => <Ext key={x.id} href={x.permalink} className="xs mono">{x.path}</Ext>)}</div>}</div>
        </div>
      ))}
      <div className="faint xs">Checked {timeAgo(v.checked_at)}{v.commit_sha ? ` against ${shortSha(v.commit_sha)}` : ""}</div>
    </div>
  );
}

function TasksTab() {
  const tasks = useApi<any[]>("/api/tasks");
  const [issueFor, setIssueFor] = useState<any>(null);
  const { busy, run } = useAction();
  useEventRefresh(["gap_task_updated", "assessment_created"], () => tasks.reload(true));
  const live = (tasks.data || []).filter((t) => t.status !== "dismissed");
  if (tasks.loading) return <Loading />;
  if (!live.length) return <Card><Empty icon={<ListChecks size={20} />} title="No gap-to-proof tasks" action={<Link className="btn primary" href="/gaps">Open the gap matrix</Link>}>Open a gap row and choose “Draft gap-to-proof task”. Tasks have machine-checkable acceptance criteria that are verified automatically when you push.</Empty></Card>;
  return (
    <div className="stack">
      {live.map((t) => (
        <Card key={t.id} title={t.title} actions={<span className={`badge ${t.status === "criteria_met" ? "ok" : t.status === "closed" ? "" : t.status === "issue_created" ? "info" : "warn"}`}>{t.status.replaceAll("_", " ")}</span>}>
          <div className="grid g2">
            <div className="stack">
              <div className="small muted">{t.objective}</div>
              <div className="small"><b>Repository:</b> {t.repository?.full_name || "—"} · <b>Effort:</b> {t.effort_estimate || "?"} (estimate) · <span className="faint">{t.generator}</span></div>
              {t.suggested_files?.length > 0 && <div className="small"><b>Suggested files:</b> <span className="mono xs">{t.suggested_files.join(", ")}</span></div>}
              {t.risks?.length > 0 && <div className="small"><b>Risks:</b> {t.risks.join("; ")}</div>}
              <details><summary className="small">Issue body</summary><pre className="plain mt8">{t.body}</pre></details>
            </div>
            <div className="stack">
              <h4>Acceptance criteria</h4>
              <Criteria v={t.last_verification} />
            </div>
          </div>
          <div className="row wrap mt12">
            {t.github_issue_url ? <Ext href={t.github_issue_url} className="btn sm">Issue #{t.github_issue_number}</Ext> : t.status !== "closed" && <button className="btn sm primary" onClick={() => setIssueFor(t)}><GitPullRequest size={13} />Preview & create GitHub issue</button>}
            <button className="btn sm" disabled={!!busy} onClick={() => run(`v-${t.id}`, async () => { await api.post(`/api/tasks/${t.id}/verify`); tasks.reload(true); }, "Criteria re-checked against the latest analysed commit")}>{busy === `v-${t.id}` && <Spinner />}Verify now</button>
            {t.status === "criteria_met" && t.github_issue_number && <button className="btn sm" onClick={() => { if (confirm("Close the GitHub issue with a verification comment?")) run("close", async () => { await api.post(`/api/tasks/${t.id}/close`, { confirm: true }); tasks.reload(true); }, "Issue closed with verification comment"); }}>Close issue (verified)</button>}
            {t.status !== "closed" && <button className="btn sm ghost" onClick={() => run("dis", async () => { await api.del(`/api/tasks/${t.id}`); tasks.reload(true); })}>Dismiss</button>}
          </div>
        </Card>
      ))}
      {issueFor && <IssueModal task={issueFor} onClose={() => setIssueFor(null)} onDone={() => tasks.reload(true)} />}
    </div>
  );
}

function RoadmapTab() {
  const { trackedJob } = useAppState();
  const rm = useApi<any>(trackedJob ? `/api/insights/roadmap/${trackedJob.id}` : null, [trackedJob?.id]);
  const { busy, run } = useAction();
  if (!trackedJob) return <Empty title="Select a target job first" />;
  if (rm.loading) return <Loading />;
  if (rm.error) return <ErrorBox error={rm.error} />;
  const d = rm.data;
  if (!d?.items?.length) return <Card><Empty title="Nothing to do here">{d?.note || "All in-scope requirements are demonstrated in the analysed scope."}</Empty></Card>;
  return (
    <div className="stack">
      <Alert tone="info"><b>Ranking:</b> {d.formula}. {d.disclaimer}</Alert>
      {d.items.map((it: any, i: number) => (
        <Card key={it.row_id} title={<span>{i + 1}. {it.skill}</span>} actions={<span className="row"><VerdictBadge v={it.verdict} /><span className="badge sq">score {it.score}</span><span className="badge sq">effort {it.effort}</span>{it.market_share > 0 && <span className="badge sq orange">in {Math.round(it.market_share * 100)}% of searched postings</span>}</span>}>
          <dl className="kv">
            <dt>Supports requirement</dt><dd className="small">“{it.supports_requirement}” {it.source_url && <Ext href={it.source_url} className="xs">source</Ext>}</dd>
            <dt>Missing evidence</dt><dd className="small muted">{it.missing_evidence}</dd>
            <dt>Would demonstrate it</dt><dd className="small">{it.demonstrating_artifacts}</dd>
            <dt>Automated checks</dt><dd className="small">{it.automated_checks}</dd>
          </dl>
          <div className="row mt12">{it.task ? <span className="badge info">task: {it.task.status}</span> :
            <button className="btn sm" disabled={!!busy} onClick={() => run(`p-${it.row_id}`, async () => { await api.post("/api/tasks/propose", { assessment_row_id: it.row_id }); rm.reload(true); }, "Task drafted")}>{busy === `p-${it.row_id}` && <Spinner />}Draft task</button>}</div>
        </Card>
      ))}
    </div>
  );
}

function SandboxTab() {
  const st = useApi<any>("/api/sandbox/status");
  const scopes = useApi<any[]>("/api/github/scopes");
  const runs = useApi<any[]>("/api/sandbox/runs");
  const [prop, setProp] = useState<any>(null);
  const [repoId, setRepoId] = useState("");
  const [consent, setConsent] = useState(false);
  const { busy, run } = useAction();
  useEventRefresh(["sandbox_status"], () => runs.reload(true));
  return (
    <div className="stack">
      {st.data && (st.data.available ? <Alert tone="ok">Docker {st.data.server_version} available. Runs use a disposable container: no network by default, read-only source, non-root, all capabilities dropped, CPU/memory/PID/time limits.</Alert>
        : <Alert tone="warn"><b>Sandbox disabled.</b> {st.data.reason} Untrusted repository code is never executed in the backend process.</Alert>)}
      <Card title="Propose a test run" icon={<FlaskConical size={16} />}>
        <div className="row wrap">
          <select className="select" style={{ maxWidth: 320 }} value={repoId} onChange={(e) => { setRepoId(e.target.value); setProp(null); }}>
            <option value="">Select repository…</option>{(scopes.data || []).map((s) => <option key={s.id} value={s.repository.id}>{s.repository.full_name}</option>)}</select>
          <button className="btn" disabled={!repoId || !!busy} onClick={() => run("prop", async () => setProp(await api.get(`/api/sandbox/propose/${repoId}`)))}>Detect test command</button>
        </div>
        {prop && <div className="stack mt12">
          <dl className="kv"><dt>Pinned commit</dt><dd className="mono xs">{prop.commit_sha}</dd><dt>Image</dt><dd className="mono">{prop.image}</dd><dt>Command</dt><dd><code>{prop.command}</code></dd></dl>
          {prop.needs_network_for_install && <div className="faint xs">Dependencies must be installed inside the container; with network disabled (default) installs fail and tests may not run. You can allow network in Settings.</div>}
          <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />I authorise running this exact command on this exact commit of my repository in an isolated sandbox.</label>
          <div><button className="btn primary" disabled={!consent || !!busy} onClick={() => run("run", async () => { await api.post("/api/sandbox/runs", { repository_id: repoId, command: prop.command, image: prop.image, commit_sha: prop.commit_sha, consent }); runs.reload(true); }, "Sandbox run recorded")}>Run in sandbox</button></div>
        </div>}
      </Card>
      <Card title="Runs" pad={false}>
        {!(runs.data || []).length ? <div className="card-body muted small">No runs.</div> : <div className="table-wrap"><table className="tbl"><thead><tr><th>Repository / commit</th><th>Command</th><th>Status</th><th>Result</th><th>When</th></tr></thead>
          <tbody>{(runs.data || []).map((r) => <tr key={r.id}><td className="small">{r.repository}<div className="mono xs faint">{shortSha(r.commit_sha)}</div></td><td className="mono xs">{r.command}</td><td><span className={`badge ${r.status === "completed" ? "ok" : r.status === "disabled" || r.status === "failed" ? "bad" : "info"}`}>{r.status}</span>{r.error && <div className="xs faint">{r.error}</div>}</td>
            <td className="small">{r.exit_code != null ? `exit ${r.exit_code}` : "—"}{r.passed != null && ` · ${r.passed} passed`}{r.failed != null && ` · ${r.failed} failed`}{r.output_excerpt && <details><summary className="xs">output</summary><pre className="plain">{r.output_excerpt}</pre></details>}</td><td className="small faint">{fmtDate(r.created_at)}</td></tr>)}</tbody></table></div>}
      </Card>
    </div>
  );
}

function MarketTab() {
  const m = useApi<any>("/api/insights/market");
  const fr = useApi<any[]>("/api/insights/freshness?status=open");
  const { run } = useAction();
  const max = m.data?.skills?.[0]?.postings || 1;
  return (
    <div className="grid g2">
      <Card title="Market demand (your searched postings)" icon={<TrendingUp size={16} />}>
        {m.loading ? <Loading /> : !m.data?.sample_size ? <Empty title="No sample yet">Run a job search to collect real postings.</Empty> : (
          <div className="stack">
            <div className="small">Sample: <b>{m.data.sample_size}</b> postings · providers {Object.entries(m.data.providers).map(([k, v]) => `${k} (${v})`).join(", ")}{m.data.date_range ? ` · ${fmtDate(m.data.date_range[0])} – ${fmtDate(m.data.date_range[1])}` : ""}</div>
            {m.data.warning && <Alert tone="warn">{m.data.warning}</Alert>}
            <div className="chart-bars">{m.data.skills.slice(0, 18).map((s: any) => (
              <div key={s.skill_key} className="meter-row"><span className="ellipsis">{s.skill}</span><div className="progress"><div style={{ width: `${(s.postings / max) * 100}%` }} /></div><b>{Math.round(s.share * 100)}%</b></div>
            ))}</div>
            <div className="faint xs">{m.data.method}</div>
          </div>
        )}
      </Card>
      <Card title="Freshness & contradictions" icon={<AlertTriangle size={16} />}>
        {fr.loading ? <Loading /> : !(fr.data || []).length ? <div className="muted small">No open items.</div> : (
          <div className="col" style={{ gap: 10 }}>
            {(fr.data || []).map((e) => (
              <div key={e.id} className="card flat sunken" style={{ padding: 10 }}>
                <div className="row between"><span className={`badge ${e.kind === "contradiction" ? "violet" : e.kind === "permission" ? "bad" : "warn"}`}>{e.kind === "contradiction" ? "needs review" : e.kind.replace("_", " ")}</span><span className="faint xs">{timeAgo(e.created_at)}</span></div>
                <div className="small mt4">{e.reason}</div>
                <div className="row mt8"><button className="btn sm" onClick={() => run("r", async () => { await api.post(`/api/insights/freshness/${e.id}`, { status: "resolved" }); fr.reload(true); })}>Resolve</button>
                  <button className="btn sm ghost" onClick={() => run("d", async () => { await api.post(`/api/insights/freshness/${e.id}`, { status: "dismissed" }); fr.reload(true); })}>Dismiss</button>
                  {e.kind === "contradiction" && <Link className="btn sm ghost" href="/repos">Add a repo / change scope</Link>}</div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function Tasks() {
  const [tab, setTab] = useState("tasks");
  return (
    <>
      <PageHead title="Gap-to-proof & roadmap" subtitle="Turn a gap into a small, verifiable task. Issues are created only after you confirm; criteria are checked against real commits by the same webhook/poll loop — never closed on a model’s word." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "tasks", label: <span className="row"><ListChecks size={14} />Tasks</span> }, { key: "roadmap", label: <span className="row"><Map size={14} />Roadmap</span> },
        { key: "sandbox", label: <span className="row"><FlaskConical size={14} />Sandbox runs</span> }, { key: "market", label: <span className="row"><TrendingUp size={14} />Market & freshness</span> }]} />
      {tab === "tasks" && <TasksTab />}
      {tab === "roadmap" && <RoadmapTab />}
      {tab === "sandbox" && <SandboxTab />}
      {tab === "market" && <MarketTab />}
    </>
  );
}

