"use client";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ShieldCheck, ShieldX } from "lucide-react";
import { api, fmtDate } from "@/lib/api";
import { useApi } from "@/lib/app";
import { ErrorBox, Ext, Loading, VerdictBadge } from "@/components/ui";

export default function PublicPassport() {
  const { artifactId } = useParams<{ artifactId: string }>();
  const p = useApi<any>(`/api/public/passports/${artifactId}`, [artifactId]);
  const [tamper, setTamper] = useState<any>(null);
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => { document.title = "Skill passport"; }, []);
  if (p.loading) return <div className="content"><Loading rows={8} /></div>;
  if (p.error) return <div className="content"><ErrorBox error={p.error} /></div>;
  const d = p.data;
  const v = d.verification;
  return (
    <div className="content passport-doc">
      <div className="card card-pad stack">
        <div className="row between wrap">
          <div><h1>{d.title}</h1><div className="muted small mt4">{d.payload.subject?.display_name ? `${d.payload.subject.display_name} · ` : ""}issued {fmtDate(d.payload.issued_at)} by {d.payload.issuer.name}</div></div>
          <div className={`seal ${v.valid ? "ok" : "bad"}`}>{v.valid ? <ShieldCheck size={18} /> : <ShieldX size={18} />}{v.valid ? "Verified signature" : v.revoked ? "Revoked" : v.expired ? "Expired" : "Invalid"}</div>
        </div>
        {d.payload.job?.title && <div className="small">Assessed against: <b>{d.payload.job.title}</b>{d.payload.job.company ? ` at ${d.payload.job.company}` : ""} {d.payload.job.source_url && <Ext href={d.payload.job.source_url} className="xs">job source</Ext>}</div>}
        <div className="small faint">Assessment v{d.payload.assessment.version} · {fmtDate(d.payload.assessment.created_at)} · scope: {d.payload.assessment.scopes.map((s: any) => `${s.repo || "private repository"}@${s.branch} ${String(s.commit_sha).slice(0, 7)}`).join(", ")}</div>
        <table className="tbl"><thead><tr><th>Skill</th><th>Verdict</th><th>Evidence</th></tr></thead><tbody>
          {d.payload.skills.map((s: any) => (
            <tr key={s.skill_key}><td><b>{s.skill}</b><div className="xs faint">“{s.requirement_quote.slice(0, 120)}”</div></td><td><VerdictBadge v={s.verdict} /><div className="xs faint mt4">verified {fmtDate(s.verification_date)}</div></td>
              <td className="col" style={{ gap: 3 }}>{s.evidence.map((e: any, i: number) => e.permalink ? <Ext key={i} href={e.permalink} className="mono xs">{e.repo}:{e.path} L{e.lines}</Ext> : <span key={i} className="xs faint">{e.repo} · {String(e.commit_sha).slice(0, 7)} (redacted)</span>)}
                {s.runtime_test_result && <span className="badge ok">tests run: {s.runtime_test_result.passed} passed</span>}
                {s.understanding && <span className="badge pink">{s.understanding.label}</span>}</td></tr>
          ))}</tbody></table>
        <div className="alert info"><div><b>Scope.</b> {d.payload.scope_statement}<br /><b>Limitations.</b> {d.payload.limitations}</div></div>
        <div className="small faint">Signature: Ed25519 · key <span className="mono">{d.key_id}</span> · canonical JSON (sorted keys, compact separators). Expires {fmtDate(d.payload.expires_at)}.</div>
        <div className="hr" />
        <div className="field"><label>Check a downloaded passport file for tampering</label>
          <div className="row"><input type="file" className="input" accept="application/json" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <button className="btn" disabled={!file} onClick={async () => { try { const j = JSON.parse(await file!.text()); setTamper(await api.post(`/api/public/passports/${artifactId}/verify`, { payload: j.payload, signature: j.signature })); } catch (e: any) { setTamper({ valid: false, reason: e.message }); } }}>Verify file</button></div>
          {tamper && <div className={`seal mt8 ${tamper.signature_valid ? "ok" : "bad"}`}>{tamper.signature_valid ? "File signature matches — content unmodified" : "Signature does not match this content (modified or invalid)"}</div>}
        </div>
      </div>
    </div>
  );
}
