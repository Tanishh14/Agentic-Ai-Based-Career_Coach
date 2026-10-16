"use client";
import { useParams } from "next/navigation";
import { useState } from "react";
import { fmtDate, shortSha } from "@/lib/api";
import { useApi, useEventRefresh } from "@/lib/app";
import { Card, ErrorBox, Ext, Loading, PageHead, StatusDot, Tabs } from "@/components/ui";
import EvidenceDrawer from "@/components/EvidenceDrawer";

export default function ScopeManifest() {
  const { scopeId } = useParams<{ scopeId: string }>();
  const scope = useApi<any>(`/api/github/scopes/${scopeId}`, [scopeId]);
  const ev = useApi<any[]>(`/api/github/evidence?scope_id=${scopeId}&status=all`, [scopeId]);
  const [tab, setTab] = useState("manifest");
  const [open, setOpen] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  useEventRefresh(["scan_progress"], () => { scope.reload(true); ev.reload(true); });
  if (scope.loading) return <Loading rows={6} />;
  if (scope.error) return <ErrorBox error={scope.error} />;
  const s = scope.data;
  const snap = s.snapshot;
  const evs = (ev.data || []).filter((e) => !filter || e.skill_key.includes(filter) || e.path.includes(filter) || e.verification_status === filter);
  return (
    <>
      <PageHead title={`Scope manifest · ${s.repository.full_name}`} subtitle={`Branch ${s.branch} · scope version ${s.version}. Everything below is pinned to the analysed commit.`} />
      {!snap ? <Card><div className="muted">Scan {s.scan_status}. {s.scan_error}</div></Card> : (
        <>
          <div className="grid g4">
            <div className="card stat"><div className="label">Commit</div><div className="value mono" style={{ fontSize: 20 }}><Ext href={snap.commit_url}>{shortSha(snap.commit_sha)}</Ext></div><div className="hint">{fmtDate(snap.commit_date)}</div></div>
            <div className="card stat"><div className="label">Files analysed</div><div className="value">{snap.scanned_file_count}</div><div className="hint">of {snap.tree_file_count} in tree</div></div>
            <div className="card stat"><div className="label">Ignored</div><div className="value">{snap.ignored_count}</div><div className="hint">with recorded reasons</div></div>
            <div className="card stat"><div className="label">Scan</div><div className="value" style={{ fontSize: 20 }}>{snap.mode}</div><div className="hint">{snap.trigger} · {fmtDate(snap.created_at)}</div></div>
          </div>
          <Tabs value={tab} onChange={setTab} tabs={[{ key: "manifest", label: "Manifest" }, { key: "files", label: `Files (${snap.files?.length || 0})` }, { key: "ignored", label: `Ignored (${snap.ignored_count})` }, { key: "evidence", label: `Evidence (${(ev.data || []).length})` }, { key: "history", label: "Commits analysed" }]} />
          {tab === "manifest" && (
            <div className="grid g2">
              <Card title="Scope">
                <dl className="kv">
                  <dt>Repository</dt><dd><Ext href={s.repository.html_url}>{s.repository.full_name}</Ext> <span className="faint xs">id {s.repository.github_id}</span></dd>
                  <dt>Branch</dt><dd>{s.branch}</dd>
                  <dt>Commit SHA</dt><dd className="mono xs">{snap.commit_sha}</dd>
                  <dt>Message</dt><dd className="small">{snap.commit_message}</dd>
                  <dt>Include</dt><dd className="mono xs">{s.include_patterns?.join(", ") || "(whole repository)"}</dd>
                  <dt>Exclude</dt><dd className="mono xs">{s.exclude_patterns?.join(", ") || "—"}</dd>
                  <dt>Languages</dt><dd>{Object.entries(snap.languages || {}).map(([k, v]) => `${k} (${v})`).join(", ")}</dd>
                </dl>
              </Card>
              <Card title="Detectors run">
                <ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>{(snap.detectors || []).map((d: string) => <li key={d}>{d}</li>)}</ul>
                <div className="faint xs mt8">Static analysis is approximate: call-graph reachability is name-based and does not prove runtime execution.</div>
              </Card>
            </div>
          )}
          {tab === "files" && <Card pad={false}><div className="table-wrap" style={{ maxHeight: 600 }}><table className="tbl"><thead><tr><th>Path</th><th>Language</th><th>Kind</th><th>Blob</th></tr></thead>
            <tbody>{(snap.files || []).map((f: any) => <tr key={f.path}><td className="mono xs"><Ext href={`${s.repository.html_url}/blob/${snap.commit_sha}/${f.path}`}>{f.path}</Ext></td><td className="small">{f.language}</td><td className="small">{f.kind}</td><td className="mono xs faint">{f.blob_sha.slice(0, 8)}</td></tr>)}</tbody></table></div></Card>}
          {tab === "ignored" && <Card pad={false}><div className="table-wrap" style={{ maxHeight: 600 }}><table className="tbl"><thead><tr><th>Path</th><th>Reason</th></tr></thead>
            <tbody>{(snap.ignored || []).map((f: any, i: number) => <tr key={i}><td className="mono xs">{f.path}</td><td className="small muted">{f.reason}</td></tr>)}</tbody></table></div>
            {snap.ignored_count > (snap.ignored || []).length && <div className="card-body faint xs">Showing first {(snap.ignored || []).length} of {snap.ignored_count}.</div>}</Card>}
          {tab === "evidence" && (
            <Card pad={false} title="All evidence records (current and historical)" actions={<input className="input" style={{ width: 240 }} placeholder="filter: skill, path, accepted…" value={filter} onChange={(e) => setFilter(e.target.value)} />}>
              <div className="table-wrap" style={{ maxHeight: 640 }}><table className="tbl"><thead><tr><th>Skill</th><th>Location</th><th>Detector</th><th>Depth</th><th>Critic</th><th>Status</th></tr></thead>
                <tbody>{evs.map((e) => (
                  <tr key={e.id} className="clickable" onClick={() => setOpen(e.id)}>
                    <td className="bold small">{e.skill_key}</td><td className="mono xs">{e.path}:{e.start_line}-{e.end_line}<div className="faint">{shortSha(e.commit_sha)}</div></td>
                    <td className="xs">{e.detector}</td><td className="small">{e.depth}</td>
                    <td className="small"><span className="row"><StatusDot status={e.verification_status} />{e.verification_status}</span></td>
                    <td className="small">{e.status}{e.superseded_reason && <div className="xs faint">{e.superseded_reason}</div>}</td>
                  </tr>
                ))}</tbody></table></div>
            </Card>
          )}
          {tab === "history" && <Card pad={false}><div className="table-wrap"><table className="tbl"><thead><tr><th>Commit</th><th>Message</th><th>Mode</th><th>Trigger</th><th>Changed paths</th><th>When</th></tr></thead>
            <tbody>{(s.history || []).map((h: any) => <tr key={h.id}><td className="mono xs"><Ext href={h.commit_url}>{shortSha(h.commit_sha)}</Ext></td><td className="small">{h.message}</td><td>{h.mode}</td><td>{h.trigger}</td><td>{h.changed}</td><td className="small faint">{fmtDate(h.created_at)}</td></tr>)}</tbody></table></div></Card>}
        </>
      )}
      {open && <EvidenceDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}
