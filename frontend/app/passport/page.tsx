"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { BadgeCheck, Download, ShieldCheck, ShieldX } from "lucide-react";
import { api, Assessment, fmtDate } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, Empty, Loading, Modal, PageHead, Spinner, Switch, VerdictBadge } from "@/components/ui";

function Create({ onDone }: { onDone: () => void }) {
  const { trackedJob } = useAppState();
  const a = useApi<Assessment | null>(trackedJob ? `/api/assessments/job/${trackedJob.id}/latest` : null, [trackedJob?.id]);
  const [rows, setRows] = useState<string[]>([]);
  const [title, setTitle] = useState("");
  const [vis, setVis] = useState("private");
  const [links, setLinks] = useState(true);
  const [name, setName] = useState(false);
  const [days, setDays] = useState(180);
  const { busy, run } = useAction();
  useEffect(() => {
    if (a.data) { setRows(a.data.rows.filter((r) => r.verdict === "DEMONSTRATED").map((r) => r.id)); setTitle(`Verified skills for ${trackedJob?.title || "target role"}`); }
  }, [a.data, trackedJob]);
  if (!trackedJob) return <Empty title="Select a target job first" action={<Link href="/jobs" className="btn primary">Add a job</Link>} />;
  if (a.loading) return <Loading />;
  if (!a.data) return <Empty title="No assessment yet" />;
  return (
    <Card title="Issue a signed passport" icon={<BadgeCheck size={16} />}>
      <div className="grid g2">
        <div className="stack">
          <div className="field"><label>Title</label><input className="input" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="field"><label>Visibility</label><select className="select" value={vis} onChange={(e) => setVis(e.target.value)}><option value="private">Private (only you)</option><option value="public">Public link</option></select></div>
          <div className="field"><label>Valid for (days)</label><input type="number" className="input" min={1} max={730} value={days} onChange={(e) => setDays(Number(e.target.value))} /></div>
          <Switch checked={links} onChange={setLinks} label="Include repository / file / line permalinks" help="Private repositories are always redacted to “private repository”." />
          <Switch checked={name} onChange={setName} label="Include my display name" />
        </div>
        <div>
          <div className="label">Skills to include (assessment v{a.data.version})</div>
          <div className="col mt8" style={{ gap: 6, maxHeight: 300, overflow: "auto" }}>
            {a.data.rows.map((r) => (
              <label key={r.id} className="check"><input type="checkbox" checked={rows.includes(r.id)} onChange={(e) => setRows(e.target.checked ? [...rows, r.id] : rows.filter((x) => x !== r.id))} />
                <span className="row grow between"><span>{r.requirement.display_name}</span><VerdictBadge v={r.verdict} /></span></label>
            ))}
          </div>
          <div className="faint xs mt8">Every verdict is included exactly as assessed — including partial or not-demonstrated rows if you select them.</div>
        </div>
      </div>
      <div className="row mt16"><button className="btn primary" disabled={!rows.length || title.length < 3 || !!busy} onClick={() => run("pp", async () => {
        const r = await api.post("/api/passports", { assessment_id: a.data!.id, row_ids: rows, title, visibility: vis, include_permalinks: links, include_display_name: name, expires_days: days });
        onDone(); return r;
      }, "Passport issued and signed (Ed25519)")}>{busy && <Spinner />}Sign & issue</button></div>
    </Card>
  );
}

function View({ id, onClose, onChange }: { id: string; onClose: () => void; onChange: () => void }) {
  const p = useApi<any>(`/api/passports/${id}`, [id]);
  const { busy, run } = useAction();
  const [reason, setReason] = useState("");
  const d = p.data;
  const download = () => {
    const blob = new Blob([JSON.stringify({ payload: d.payload, signature: d.signature, key_id: d.key_id, alg: d.alg }, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${id}.passport.json`; a.click();
  };
  return (
    <Modal title={d?.title || "Passport"} onClose={onClose} footer={d && <>
      <button className="btn" onClick={download}><Download size={14} />Signed JSON</button>
      {d.visibility === "public" && <Link className="btn" href={`/p/${id}`} target="_blank">Public page</Link>}
      {!d.verification.revoked && <><input className="input" style={{ maxWidth: 200 }} placeholder="revoke reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="btn danger" disabled={!!busy} onClick={() => run("rv", async () => { await api.post(`/api/passports/${id}/revoke`, { reason }); p.reload(true); onChange(); }, "Passport revoked")}>Revoke</button></>}
    </>}>
      {p.loading || !d ? <Loading /> : (
        <>
          <div className={`seal ${d.verification.valid ? "ok" : "bad"}`}>{d.verification.valid ? <ShieldCheck size={18} /> : <ShieldX size={18} />}
            {d.verification.valid ? "Signature valid · not revoked · not expired" : d.verification.revoked ? `Revoked ${fmtDate(d.verification.revoked_at)}` : d.verification.expired ? "Expired" : "Signature invalid"}</div>
          <dl className="kv"><dt>Artifact</dt><dd className="mono xs">{d.payload.artifact_id}</dd><dt>Key</dt><dd className="mono xs">{d.key_id} (Ed25519)</dd><dt>Issued</dt><dd>{fmtDate(d.payload.issued_at)}</dd><dt>Expires</dt><dd>{fmtDate(d.payload.expires_at)}</dd><dt>Visibility</dt><dd>{d.visibility}</dd></dl>
          {d.payload.skills.map((s: any) => <div key={s.skill_key} className="row between small"><span>{s.skill}</span><VerdictBadge v={s.verdict} /></div>)}
          <Alert tone="info">{d.payload.limitations}</Alert>
        </>
      )}
    </Modal>
  );
}

export default function Passports() {
  const list = useApi<any[]>("/api/passports");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <>
      <PageHead title="Verified skill passport" subtitle="A shareable, revocable evidence artifact signed with Ed25519. You choose what is included; private code and personal fields are never revealed without your consent." />
      <Create onDone={() => list.reload()} />
      <Card title="Issued passports" pad={false} className="mt16">
        {list.loading ? <div className="card-body"><Loading /></div> : !(list.data || []).length ? <div className="card-body muted small">None issued yet.</div> : (
          <div className="table-wrap"><table className="tbl"><thead><tr><th>Title</th><th>Skills</th><th>Visibility</th><th>Status</th><th>Issued</th><th /></tr></thead>
            <tbody>{(list.data || []).map((p) => (
              <tr key={p.artifact_id}><td className="bold">{p.title}<div className="mono xs faint">{p.artifact_id}</div></td><td>{p.skills}</td><td>{p.visibility}</td>
                <td>{p.revoked_at ? <span className="badge bad">revoked</span> : new Date(p.expires_at) < new Date() ? <span className="badge warn">expired</span> : <span className="badge ok">active</span>}</td>
                <td className="small">{fmtDate(p.created_at)}</td><td><button className="btn sm" onClick={() => setOpen(p.artifact_id)}>Open</button></td></tr>
            ))}</tbody></table></div>
        )}
      </Card>
      {open && <View id={open} onClose={() => setOpen(null)} onChange={() => list.reload(true)} />}
    </>
  );
}
