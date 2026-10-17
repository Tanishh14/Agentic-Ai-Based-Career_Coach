"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight, GitCompare, Minus } from "lucide-react";
import { fmtDate, shortSha, timeAgo } from "@/lib/api";
import { useApi, useAppState, useEventRefresh } from "@/lib/app";
import { Card, Empty, ErrorBox, Ext, Loading, PageHead, VerdictBadge } from "@/components/ui";
import EvidenceDrawer from "@/components/EvidenceDrawer";

const DIR: Record<string, [any, string, string]> = {
  improved: [ArrowUpRight, "ok", "Improved"], regressed: [ArrowDownRight, "bad", "Regressed"], evidence_changed: [Minus, "info", "Evidence changed"], new: [ArrowRight, "", "New"],
};

function Compare({ fromId, toId, onEvidence }: { fromId: string; toId: string; onEvidence: (id: string) => void }) {
  const c = useApi<any>(`/api/assessments/compare/${fromId}/${toId}`, [fromId, toId]);
  if (c.loading) return <Loading rows={6} />;
  if (c.error) return <ErrorBox error={c.error} />;
  const d = c.data;
  const ev = (row: any) => !row ? <span className="faint small">—</span> : row.evidence.length === 0 ? <span className="faint small">no evidence</span> :
    <div className="col" style={{ gap: 3 }}>{row.evidence.slice(0, 3).map((e: any) => <button key={e.id} className="ev-chip" onClick={() => onEvidence(e.id)}><span>{e.path.split("/").slice(-2).join("/")}:{e.start_line}</span><span className="faint">@{shortSha(e.commit_sha)}</span></button>)}</div>;
  return (
    <Card title={<span className="row"><GitCompare size={16} />v{d.from.version} → v{d.to.version}</span>} pad={false}
      actions={<span className="faint xs">{d.to.trigger_type}{d.to.trigger_commit_sha ? ` · ${shortSha(d.to.trigger_commit_sha)}` : ""} · coverage {d.from.summary.coverage_indicator}% → {d.to.summary.coverage_indicator}%</span>}>
      <div className="table-wrap"><table className="tbl">
        <thead><tr><th>Requirement</th><th>Before (v{d.from.version})</th><th>Evidence before</th><th>After (v{d.to.version})</th><th>Evidence after</th></tr></thead>
        <tbody>{d.rows.map((r: any) => (
          <tr key={r.requirement.id} className={r.changed ? "changed" : ""}>
            <td><b>{r.requirement.display_name}</b>{r.carried_unchanged && <div className="xs faint">unchanged · same evidence IDs</div>}</td>
            <td>{r.before ? <VerdictBadge v={r.before.verdict} /> : "—"}</td><td>{ev(r.before)}</td>
            <td><VerdictBadge v={r.after.verdict} /></td><td>{ev(r.after)}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </Card>
  );
}

export default function History() {
  const { trackedJob } = useAppState();
  const versions = useApi<any[]>(trackedJob ? `/api/assessments/job/${trackedJob.id}/versions` : null, [trackedJob?.id]);
  const diffs = useApi<any[]>(trackedJob ? `/api/assessments/job/${trackedJob.id}/diffs` : null, [trackedJob?.id]);
  const [pair, setPair] = useState<[string, string] | null>(null);
  const [ev, setEv] = useState<string | null>(null);
  useEventRefresh(["assessment_created"], () => { versions.reload(true); diffs.reload(true); });
  useEffect(() => {
    const v = versions.data || [];
    if (!pair && v.length >= 2) setPair([v[1].id, v[0].id]);
  }, [versions.data, pair]);
  if (!trackedJob) return <><PageHead title="Assessment history & commit diffs" /><Card><Empty title="No target job" action={<Link href="/jobs" className="btn primary">Add a job</Link>} /></Card></>;
  const v = versions.data || [];
  return (
    <>
      <PageHead title="Assessment history & commit diffs" subtitle="Assessments are immutable versions. Each new version records what triggered it (webhook delivery, SHA poll, scope change), the real commit, the changed paths and exactly which rows moved." />
      {versions.loading ? <Loading /> : versions.error ? <ErrorBox error={versions.error} /> : v.length < 2 ? (
        <Card><Empty title={v.length ? "Only one version so far" : "No assessments yet"}>Push a real commit to a monitored repository (e.g. add SQL queries or tests). The webhook or the SHA poller will trigger a reassessment and a diff will appear here automatically.</Empty></Card>
      ) : (
        <>
          <Card title="Compare versions side by side" actions={
            <div className="row">
              <select className="select" value={pair?.[0] || ""} onChange={(e) => setPair([e.target.value, pair?.[1] || v[0].id])}>{v.map((x) => <option key={x.id} value={x.id}>v{x.version} · {x.trigger_type}</option>)}</select>
              <ArrowRight size={14} />
              <select className="select" value={pair?.[1] || ""} onChange={(e) => setPair([pair?.[0] || v[1].id, e.target.value])}>{v.map((x) => <option key={x.id} value={x.id}>v{x.version} · {x.trigger_type}</option>)}</select>
            </div>} pad={false}>
            <div />
          </Card>
          {pair && pair[0] !== pair[1] && <div className="mt12"><Compare fromId={pair[0]} toId={pair[1]} onEvidence={setEv} /></div>}
        </>
      )}
      <h2 className="mt24" style={{ marginBottom: 10 }}>Change log</h2>
      {diffs.loading ? <Loading /> : !(diffs.data || []).length ? <div className="muted small">No diffs yet.</div> : (
        <div className="stack">
          {(diffs.data || []).map((d) => (
            <Card key={d.id} title={<span>v{d.from_version} → v{d.to_version} <span className="faint small">· {d.trigger_type.replaceAll("_", " ")}</span></span>}
              actions={<span className="faint xs">{fmtDate(d.created_at)}</span>}>
              <div className="row wrap small">
                {d.commit_sha && <span>Commit <Ext href={d.commit_url} className="mono">{shortSha(d.commit_sha)}</Ext></span>}
                {d.trigger_ref && <span className="faint">ref {String(d.trigger_ref).slice(0, 18)}</span>}
                <span className="faint">{d.changed_paths.length} changed path(s)</span>
                <span className="faint">affected skills: {d.affected_skills.length ? d.affected_skills.join(", ") : "none"}</span>
              </div>
              {d.row_deltas.length === 0 ? <div className="muted small mt8">No verdict or evidence changes — unrelated evidence IDs and verdict timestamps stayed unchanged.</div> : (
                <div className="col mt12" style={{ gap: 8 }}>
                  {d.row_deltas.map((r: any, i: number) => {
                    const [Icon, tone, label] = DIR[r.direction] || DIR.new;
                    return (
                      <div key={i} className="row start" style={{ gap: 10 }}>
                        <span className={`badge ${tone}`}><Icon size={12} />{label}</span>
                        <div className="grow"><div className="row wrap"><b>{r.skill}</b>{r.from_verdict && <VerdictBadge v={r.from_verdict} />}<ArrowRight size={13} /><VerdictBadge v={r.to_verdict} /></div>
                          <div className="small muted mt4">{r.rationale}</div>
                          <div className="xs faint">+{r.added_evidence.length} / −{r.removed_evidence.length} evidence</div></div>
                      </div>
                    );
                  })}
                </div>
              )}
              {d.changed_paths.length > 0 && <details className="mt8"><summary className="small muted">Changed paths</summary>
                <ul className="mono xs" style={{ paddingLeft: 18 }}>{d.changed_paths.slice(0, 80).map((p: any, i: number) => <li key={i}>{p.status} {p.previous_path ? `${p.previous_path} → ` : ""}{p.path}</li>)}</ul></details>}
            </Card>
          ))}
        </div>
      )}
      {ev && <EvidenceDrawer id={ev} onClose={() => setEv(null)} />}
      <div className="faint xs mt16">Versions: {v.map((x) => `v${x.version} (${x.trigger_type}, ${timeAgo(x.created_at)})`).join(" · ")}</div>
    </>
  );
}
