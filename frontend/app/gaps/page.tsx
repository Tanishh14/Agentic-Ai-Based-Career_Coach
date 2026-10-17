"use client";
import Link from "next/link";
import { useMemo, useState } from "react";
import { FileSearch, GitCommit, ListPlus, RefreshCw, Sparkles } from "lucide-react";
import { api, Assessment, AssessmentRow, fmtDate, shortSha, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState, useEventRefresh } from "@/lib/app";
import { Alert, Card, Drawer, Empty, ErrorBox, Ext, Loading, PageHead, PriorityBadge, Progress, SourceBadge, Spinner, VerdictBadge } from "@/components/ui";
import EvidenceDrawer from "@/components/EvidenceDrawer";

function Signals({ s }: { s: any }) {
  if (!s) return null;
  const out = [];
  if (s.claims?.length) out.push(<span key="c" className="badge sq violet" title={s.claims.map((c: any) => c.text).join("\n")}>{s.claims[0].evidence_class === "CLAIMED_PROFILE" ? "Profile" : "Resume"} claim</span>);
  if (s.leetcode) out.push(<span key="l" className="badge sq orange" title={s.leetcode.note}>LeetCode {s.leetcode.solved_total ?? ""}</span>);
  if (s.understanding) out.push(<span key="u" className="badge sq pink" title={s.understanding.note}>{s.understanding.label}</span>);
  if (s.runtime) out.push(<span key="r" className="badge sq ok" title={s.runtime.note}>Tests ran: {s.runtime.passed ?? "?"} passed</span>);
  return <div className="row wrap" style={{ gap: 4 }}>{out}</div>;
}

function RowDrawer({ row, onClose, onEvidence, assessment }: { row: AssessmentRow; onClose: () => void; onEvidence: (id: string) => void; assessment: Assessment }) {
  const { busy, run } = useAction();
  const t = row.search_trace || {};
  const repos = assessment.scopes || [];
  const [repoId, setRepoId] = useState(repos[0]?.repository_id || "");
  return (
    <Drawer title={row.requirement.display_name} subtitle={<span className="row"><PriorityBadge p={row.requirement.priority} /><VerdictBadge v={row.verdict} /></span>} onClose={onClose}>
      <div><h4>Job requirement (verbatim)</h4><div className="quote mt8">“{row.requirement.quote}”</div>
        <div className="faint xs mt4">{row.requirement.source_url ? <Ext href={row.requirement.source_url}>job source</Ext> : "user-supplied text"} · chars {row.requirement.char_start}–{row.requirement.char_end} · {row.requirement.extraction_method}</div></div>
      <div><h4>Verdict explanation</h4><p className="mt8">{row.rationale}</p><p className="faint small mt4">Confidence {Math.round(row.confidence * 100)}% — {row.confidence_note}</p>
        {row.carried_from_row_id && <p className="faint xs mt4">Unchanged since {fmtDate(row.evaluated_at)}: no evidence for this skill changed in later commits.</p>}</div>
      <div><h4>Evidence ({row.evidence.length})</h4>
        {row.evidence.length === 0 ? <p className="muted small mt8">No accepted evidence in the analysed scope.</p> : (
          <div className="col mt8">{row.evidence.map((e) => (
            <button key={e.id} className="ev-chip" onClick={() => onEvidence(e.id)} style={{ justifyContent: "space-between" }}>
              <span>{e.repo}:{e.path} L{e.start_line}-{e.end_line} @ {shortSha(e.commit_sha)}</span><span className="row" style={{ gap: 4 }}><SourceBadge type={e.source_type} /><span className="badge sq">{e.depth}</span></span>
            </button>
          ))}</div>
        )}</div>
      <div><h4>Scope searched</h4>
        <ul className="small muted mt8" style={{ paddingLeft: 18 }}>
          {(t.repositories || []).map((r: any) => <li key={r.scope_id}>{r.repo}@{r.branch} <span className="mono">{shortSha(r.commit_sha)}</span> — {r.files_scanned} files{r.include?.length ? ` (include ${r.include.join(", ")})` : ""}</li>)}
          <li>Detector: {t.detector || "none available for this skill"} · candidates: {t.accepted} accepted, {t.ambiguous} ambiguous, {t.rejected} rejected</li>
        </ul>
        {t.rejected_examples?.length > 0 && <details className="mt8"><summary className="small">Rejected candidates</summary><ul className="small muted">{t.rejected_examples.map((x: any, i: number) => <li key={i}><Ext href={x.permalink}>{x.path}:{x.lines}</Ext> — {x.why}</li>)}</ul></details>}
        {t.ambiguous_examples?.length > 0 && <details className="mt8"><summary className="small">Ambiguous candidates</summary><ul className="small muted">{t.ambiguous_examples.map((x: any, i: number) => <li key={i}><Ext href={x.permalink}>{x.path}:{x.lines}</Ext> — {x.why}</li>)}</ul></details>}
      </div>
      <div><h4>Other signals (never change the code verdict)</h4>
        <div className="col mt8 small">
          {row.signals?.claims?.map((c: any) => <div key={c.claim_id}><SourceBadge type={c.evidence_class} /> “{c.text}” <span className="faint">({c.document}{c.page ? `, p.${c.page}` : ""})</span></div>)}
          {row.signals?.leetcode && <div><SourceBadge type="PROBLEM_SOLVING_SIGNAL" /> {row.signals.leetcode.username}: {row.signals.leetcode.solved_total} solved; relevant tags {Object.entries(row.signals.leetcode.relevant_tags || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none"} <span className="faint">({row.signals.leetcode.provider}, {timeAgo(row.signals.leetcode.retrieved_at)})</span></div>}
          {row.signals?.understanding && <div><SourceBadge type={row.signals.understanding.type} /> {row.signals.understanding.label} (score {row.signals.understanding.rubric_score})</div>}
          {row.signals?.runtime && <div><SourceBadge type="RUNTIME_EXECUTION" /> {row.signals.runtime.command}: exit {row.signals.runtime.exit_code}, {row.signals.runtime.passed} passed / {row.signals.runtime.failed} failed @ {shortSha(row.signals.runtime.commit_sha)}</div>}
          {!row.signals?.claims?.length && !row.signals?.leetcode && !row.signals?.understanding && !row.signals?.runtime && <span className="faint">None.</span>}
        </div></div>
      {row.recommendation && (
        <div className="card sunken" style={{ padding: 14 }}>
          <div className="row"><SourceBadge type="RECOMMENDED" /><span className="faint xs">Recommendation — not an achievement</span></div>
          <p className="mt8 small">{row.recommendation.replace(/^RECOMMENDED:\s*/, "")}</p>
          {repos.length > 0 && <div className="row wrap mt12">
            <select className="select" style={{ maxWidth: 260 }} value={repoId} onChange={(e) => setRepoId(e.target.value)}>{repos.map((r: any) => <option key={r.repository_id} value={r.repository_id}>{r.repo}</option>)}</select>
            <button className="btn primary" disabled={!!busy} onClick={() => run("task", () => api.post("/api/tasks/propose", { assessment_row_id: row.id, repository_id: repoId }), "Gap-to-proof task drafted — review it on the Tasks page")}>{busy ? <Spinner /> : <ListPlus size={14} />}Draft gap-to-proof task</button>
          </div>}
        </div>
      )}
    </Drawer>
  );
}

export default function Gaps() {
  const { trackedJob, status } = useAppState();
  const a = useApi<Assessment | null>(trackedJob ? `/api/assessments/job/${trackedJob.id}/latest` : null, [trackedJob?.id]);
  const [openRow, setOpenRow] = useState<AssessmentRow | null>(null);
  const [openEv, setOpenEv] = useState<string | null>(null);
  const [banner, setBanner] = useState<any>(null);
  const [filter, setFilter] = useState("all");
  const { busy, run } = useAction();
  useEventRefresh(["assessment_created"], (_, p) => {
    if (trackedJob && p.job_posting_id === trackedJob.id) { setBanner(p); a.reload(true); }
  });
  const rows = useMemo(() => (a.data?.rows || []).filter((r) => filter === "all" || r.verdict === filter || (filter === "must" && r.requirement.priority === "must_have")), [a.data, filter]);
  if (!trackedJob) return <><PageHead title="Evidence & skill gaps" /><Card><Empty icon={<FileSearch size={20} />} title="Select a target job" action={<Link className="btn primary" href="/jobs">Add a job</Link>}>The gap matrix maps every requirement of a real posting to cited evidence in your repositories.</Empty></Card></>;
  const d = a.data;
  return (
    <>
      <PageHead title="Evidence & skill gaps"
        subtitle={<>Requirement → verdict → cited evidence → explanation, for <b>{trackedJob.title}</b>{trackedJob.company ? ` at ${trackedJob.company}` : ""} {trackedJob.source_url && <Ext href={trackedJob.source_url}>(source)</Ext>}</>}
        actions={<><Link className="btn" href="/history">History & diffs</Link><button className="btn" disabled={!!busy} onClick={() => run("rc", async () => { await api.post(`/api/assessments/job/${trackedJob.id}/recompute`); a.reload(true); }, "Recomputed from current evidence")}>{busy ? <Spinner /> : <RefreshCw size={14} />}Recompute</button></>} />
      {banner && (
        <div className="mb16" style={{ marginBottom: 16 }}>
          <Alert tone="ok" icon={<Sparkles size={16} />}><b>Assessment v{banner.version} created automatically</b> after a {banner.trigger_type}{banner.commit_sha ? <> for commit <span className="mono">{shortSha(banner.commit_sha)}</span></> : ""} — {banner.changes} row(s) changed. <Link href="/history">See the diff →</Link></Alert>
        </div>
      )}
      {a.loading ? <Loading rows={8} /> : a.error ? <ErrorBox error={a.error} onRetry={() => a.reload()} /> : !d ? (
        <Card><Empty title="No assessment yet" action={<Link className="btn primary" href="/repos">Add a repository</Link>}>Requirements are ready. Add a repository scope so the agent can collect evidence.</Empty></Card>
      ) : (
        <>
          <div className="grid g3">
            <Card>
              <div className="row between"><span className="faint small">Coverage indicator</span><b style={{ fontSize: 22 }}>{d.summary.coverage_indicator}%</b></div>
              <Progress value={d.summary.coverage_indicator} tone={d.summary.coverage_indicator >= 70 ? "ok" : d.summary.coverage_indicator >= 40 ? "warn" : "bad"} />
              <div className="faint xs mt8">{d.summary.disclaimer}</div>
            </Card>
            <Card>
              <div className="faint small">Assessment</div>
              <div className="bold mt4">Version {d.version} · {d.trigger_type.replaceAll("_", " ")}</div>
              <div className="faint xs mt4">{fmtDate(d.created_at)}{d.trigger_commit_sha ? <> · commit <Ext href={d.trigger_commit_url}>{shortSha(d.trigger_commit_sha)}</Ext></> : ""}</div>
              <div className="faint xs">{d.summary.recomputed_rows} row(s) re-evaluated, {d.summary.carried_rows ?? 0} carried unchanged</div>
            </Card>
            <Card>
              <div className="faint small">Declared scope</div>
              {(d.scopes || []).length === 0 ? <div className="muted small mt4">No repositories analysed — every code verdict is “not demonstrated in this scope”.</div> :
                (d.scopes || []).map((s: any) => <div key={s.scope_id} className="small mt4 row"><GitCommit size={13} /><b>{s.repo}</b>@{s.branch} <span className="mono faint">{shortSha(s.commit_sha)}</span> <span className="faint xs">{s.files_scanned} files</span></div>)}
            </Card>
          </div>
          <div className="row wrap mt16" style={{ marginBottom: 10 }}>
            {[["all", "All"], ["must", "Must-have"], ["DEMONSTRATED", "Demonstrated"], ["PARTIAL", "Partial"], ["NOT_DEMONSTRATED_IN_SCOPE", "Not demonstrated"], ["CLAIMED_ONLY", "Claimed only"]].map(([k, l]) => (
              <button key={k} className={`btn sm ${filter === k ? "primary" : ""}`} onClick={() => setFilter(k)}>{l}{k !== "all" && k !== "must" ? ` (${d.summary.counts[k] || 0})` : ""}</button>
            ))}
            <div className="grow" />
            {!status?.worker?.running && <span className="badge warn">Worker offline — automatic reassessment paused</span>}
          </div>
          <Card pad={false}>
            <div className="table-wrap"><table className="tbl">
              <thead><tr><th style={{ width: "26%" }}>Requirement & JD quote</th><th>Verdict</th><th style={{ width: "24%" }}>Evidence (repo · file · lines · commit)</th><th style={{ width: "28%" }}>Explanation</th><th>Other signals</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} className={`clickable ${r.changed && banner ? "changed" : ""}`} onClick={() => setOpenRow(r)}>
                  <td><div className="row" style={{ gap: 6 }}><b>{r.requirement.display_name}</b><PriorityBadge p={r.requirement.priority} />{r.requirement.out_of_scope && <span className="badge sq">out of scope</span>}</div>
                    <div className="xs muted mt4">“{r.requirement.quote.slice(0, 130)}{r.requirement.quote.length > 130 ? "…" : ""}”</div></td>
                  <td><VerdictBadge v={r.verdict} /><div className="xs faint mt4">{Math.round(r.confidence * 100)}% conf.</div></td>
                  <td onClick={(e) => e.stopPropagation()}>
                    {r.evidence.length === 0 ? <span className="faint small">—</span> : <div className="col" style={{ gap: 4 }}>
                      {r.evidence.slice(0, 3).map((e) => <button key={e.id} className="ev-chip" onClick={() => setOpenEv(e.id)} title={e.reason}><span>{e.path.split("/").slice(-2).join("/")}:{e.start_line}-{e.end_line}</span><span className="faint">@{shortSha(e.commit_sha)}</span></button>)}
                      {r.evidence.length > 3 && <span className="xs faint">+{r.evidence.length - 3} more</span>}
                    </div>}
                  </td>
                  <td className="small muted">{r.rationale.length > 200 ? r.rationale.slice(0, 200) + "…" : r.rationale}
                    {r.recommendation && <div className="mt4"><span className="badge sq warn">Recommended</span></div>}</td>
                  <td><Signals s={r.signals} /></td>
                </tr>
              ))}</tbody>
            </table></div>
          </Card>
        </>
      )}
      {openRow && d && <RowDrawer row={openRow} assessment={d} onClose={() => setOpenRow(null)} onEvidence={(id) => setOpenEv(id)} />}
      {openEv && <EvidenceDrawer id={openEv} onClose={() => setOpenEv(null)} />}
    </>
  );
}
