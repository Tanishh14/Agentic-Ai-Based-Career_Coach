"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import React, { useMemo, useRef, useState } from "react";
import { FileText, RefreshCw, Star, Trash2 } from "lucide-react";
import { api, fmtDate, Job, METHOD_LABEL, Requirement } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, ErrorBox, Ext, Loading, PageHead, PriorityBadge, Spinner, Switch } from "@/components/ui";

function HighlightedText({ text, reqs, active, onPick }: { text: string; reqs: Requirement[]; active: string | null; onPick: (id: string) => void }) {
  const parts = useMemo(() => {
    const spans = [...reqs].filter((r) => r.char_end > r.char_start).sort((a, b) => a.char_start - b.char_start);
    const out: React.ReactNode[] = [];
    let pos = 0;
    for (const r of spans) {
      if (r.char_start < pos) continue;
      out.push(text.slice(pos, r.char_start));
      out.push(<mark key={r.id} id={`q-${r.id}`} className={`req ${active === r.id ? "active" : ""}`} title={`${r.display_name} (${r.priority})`} onClick={() => onPick(r.id)}>{text.slice(r.char_start, r.char_end)}</mark>);
      pos = r.char_end;
    }
    out.push(text.slice(pos));
    return out;
  }, [text, reqs, active, onPick]);
  return <div className="jd-text">{parts}</div>;
}

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const job = useApi<Job>(`/api/jobs/${id}`, [id]);
  const { reload } = useAppState();
  const { busy, run } = useAction();
  const [active, setActive] = useState<string | null>(null);
  const textRef = useRef<HTMLDivElement>(null);
  const j = job.data;
  const pick = (rid: string) => { setActive(rid); document.getElementById(`q-${rid}`)?.scrollIntoView({ block: "center", behavior: "smooth" }); };
  if (job.loading) return <Loading rows={6} />;
  if (job.error || !j) return <ErrorBox error={job.error} onRetry={() => job.reload()} />;
  const ext = j.meta?.extraction || {};
  return (
    <>
      <PageHead
        title={j.title || "Untitled posting"}
        subtitle={<>{j.company || "Company not stated in the posting"}{j.location ? ` · ${j.location}` : ""}{j.employment_type ? ` · ${j.employment_type}` : ""}</>}
        actions={<>
          {j.is_tracked ? <span className="badge accent"><Star size={12} />Target job</span> :
            <button className="btn primary" onClick={() => run("track", async () => { await api.post(`/api/jobs/${id}/track`); reload(); job.reload(true); }, "Set as target job")}>Make target job</button>}
          <button className="btn" disabled={!!busy} onClick={() => run("re", async () => { await api.post(`/api/jobs/${id}/reextract`); job.reload(true); }, "Requirements re-extracted")}>{busy === "re" ? <Spinner /> : <RefreshCw size={14} />}Re-extract</button>
          <Link className="btn" href="/gaps">Gap matrix</Link>
          <button className="btn danger" onClick={() => { if (confirm("Delete this job and its assessments?")) run("del", async () => { await api.del(`/api/jobs/${id}`); reload(); router.push("/jobs"); }); }}><Trash2 size={14} /></button>
        </>}
      />
      <div className="grid g3">
        <Card title="Provenance">
          <dl className="kv">
            <dt>Source</dt><dd>{j.source_url ? <Ext href={j.source_url}>{j.platform}</Ext> : "Text supplied by you"}</dd>
            <dt>Ingestion</dt><dd>{METHOD_LABEL[j.ingestion_method] || j.ingestion_method} {j.provider !== j.platform && <span className="faint">via {j.provider}</span>}</dd>
            <dt>Retrieved</dt><dd>{fmtDate(j.retrieved_at)}</dd>
            {j.posted_at && <><dt>Posted</dt><dd>{fmtDate(j.posted_at)}</dd></>}
            <dt>Content hash</dt><dd className="mono xs">{j.content_hash.slice(0, 16)}…</dd>
            <dt>Text analysed</dt><dd>{j.text_length.toLocaleString()} characters</dd>
          </dl>
        </Card>
        <Card title="Extraction">
          <dl className="kv">
            <dt>Version</dt><dd className="mono xs">{ext.version || "—"}</dd>
            <dt>LLM</dt><dd>{ext.llm ? `${ext.llm.model} · ${ext.llm.proposed} proposed, ${ext.llm.rejected} rejected` : "not used (taxonomy matcher only)"}</dd>
            <dt>Requirements</dt><dd>{j.requirements?.length} with verified quotes</dd>
          </dl>
          {ext.rejected?.length > 0 && <details className="mt8"><summary className="small muted">Rejected candidates ({ext.rejected.length})</summary>
            <ul className="small muted">{ext.rejected.map((r: any, i: number) => <li key={i}><b>{r.term}</b>: {r.reason}</li>)}</ul></details>}
        </Card>
        <Card title="Warnings">
          {j.extraction_warning ? j.extraction_warning.split("\n").map((w, i) => <Alert key={i} tone="warn">{w}</Alert>) : <div className="muted small">None.</div>}
          {j.description_is_partial && <Alert tone="warn">This provider supplied only a snippet. Paste the full description for complete extraction.</Alert>}
        </Card>
      </div>
      <div className="grid g2 mt16">
        <Card title="Requirements" icon={<FileText size={16} />} pad={false}>
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>Skill</th><th>Priority</th><th>Verbatim quote</th><th>In scope</th></tr></thead>
            <tbody>{(j.requirements || []).map((r) => (
              <tr key={r.id} className={`clickable ${active === r.id ? "selected" : ""}`} onClick={() => pick(r.id)}>
                <td><div className="bold">{r.display_name}</div><div className="xs faint">“{r.original_term}” · {r.category.replace("_", " ")}</div>
                  <div className="row wrap mt4"><span className={`badge sq ${r.detector_supported ? "info" : ""}`}>{r.detector_supported ? "code-verifiable" : "not code-verifiable"}</span><span className="badge sq">{r.extraction_method.replace(/_/g, " ")}</span></div></td>
                <td><PriorityBadge p={r.priority} /></td>
                <td className="small muted" style={{ maxWidth: 320 }}>“{r.quote.length > 160 ? r.quote.slice(0, 160) + "…" : r.quote}”<div className="xs faint mt4">chars {r.char_start}–{r.char_end}</div></td>
                <td onClick={(e) => e.stopPropagation()}><Switch checked={!r.out_of_scope} onChange={(v) => run("scope", async () => { await api.post(`/api/jobs/requirements/${r.id}/scope`, { out_of_scope: !v }); job.reload(true); }, v ? "Back in scope" : "Marked out of scope")} label="" /></td>
              </tr>
            ))}</tbody>
          </table></div>
        </Card>
        <Card title="Stored job text (quotes highlighted)" pad={false}>
          <div ref={textRef}><HighlightedText text={j.raw_text || ""} reqs={j.requirements || []} active={active} onPick={setActive} /></div>
        </Card>
      </div>
    </>
  );
}
