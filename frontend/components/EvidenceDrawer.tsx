"use client";
import Link from "next/link";
import { Bot, Code2 } from "lucide-react";
import { api, Evidence, fmtDate, shortSha } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, CodeView, Drawer, ErrorBox, Ext, Loading, SourceBadge, Spinner, StatusDot } from "./ui";

export default function EvidenceDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const ev = useApi<Evidence>(`/api/github/evidence/${id}`, [id]);
  const { status } = useAppState();
  const { busy, run } = useAction();
  const e = ev.data;
  return (
    <Drawer title={e ? `${e.path}:${e.start_line}-${e.end_line}` : "Evidence"} subtitle={e ? <>{e.repo} @ <span className="mono">{shortSha(e.commit_sha)}</span> · {e.skill_key}</> : null} onClose={onClose}>
      {ev.loading ? <Loading rows={8} /> : ev.error || !e ? <ErrorBox error={ev.error} /> : (
        <>
          <div className="row wrap">
            <SourceBadge type={e.source_type} />
            <span className={`badge ${e.verification_status === "accepted" ? "ok" : e.verification_status === "rejected" ? "bad" : "warn"}`}><StatusDot status={e.verification_status} />critic: {e.verification_status}</span>
            <span className="badge">depth: {e.depth}</span>
            <span className="badge">signal: {e.signal_level.replaceAll("_", " ")}</span>
            <span className="badge">reachability: {e.reachability.replaceAll("_", " ")}</span>
            <span className="badge">confidence {Math.round(e.confidence * 100)}%</span>
            {e.status !== "current" && <span className="badge warn">historical</span>}
          </div>
          <div className="quote">{e.reason}</div>
          <CodeView code={e.snippet || ""} startLine={e.start_line} header={<><span className="mono">{e.path}</span><Ext href={e.permalink}>Open permalink on GitHub</Ext></>} />
          <dl className="kv">
            <dt>Permalink</dt><dd className="mono xs"><Ext href={e.permalink}>{e.permalink}</Ext></dd>
            <dt>Commit SHA</dt><dd className="mono xs">{e.commit_sha}</dd>
            <dt>Branch</dt><dd>{e.branch}</dd>
            <dt>Detector</dt><dd className="mono xs">{e.detector} v{e.detector_version} ({e.node_type})</dd>
            <dt>File kind</dt><dd>{e.file_kind}</dd>
            <dt>Snippet SHA-256</dt><dd className="mono xs">{e.snippet_digest}</dd>
            <dt>Recorded</dt><dd>{fmtDate(e.created_at)}</dd>
            {e.superseded_reason && <><dt>Superseded</dt><dd>{e.superseded_reason}</dd></>}
          </dl>
          {e.verification_notes?.length > 0 && (
            <div><h4>Critic notes</h4><ul className="small muted" style={{ paddingLeft: 18 }}>{e.verification_notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>
          )}
          {e.llm_corroboration && <Alert tone={e.llm_corroboration.supports_claim ? "info" : "warn"}>LLM review (can only lower confidence): {e.llm_corroboration.supports_claim ? "supports" : "raised concerns"}{e.llm_corroboration.concerns?.length ? ` — ${e.llm_corroboration.concerns.join("; ")}` : ""}</Alert>}
          <div className="row wrap">
            {e.verification_status === "ambiguous" && e.status === "current" && (
              <button className="btn" disabled={!status?.llm?.configured || !!busy} title={status?.llm?.configured ? "" : "Configure an LLM provider first"}
                onClick={() => run("inv", async () => { const r = await api.post(`/api/github/evidence/${e.id}/investigate`); ev.reload(true); return r; },
                  (r: any) => `Investigation: ${r.conclusion}${r.verified ? " (citation verified)" : ""}`)}>{busy === "inv" ? <Spinner /> : <Bot size={14} />}Let the agent investigate</button>
            )}
            {e.verification_status !== "rejected" && e.source_type !== "DEPENDENCY_DECLARATION" && <Link className="btn" href={`/defense?evidence=${e.id}`}><Code2 size={14} />Defend this code</Link>}
          </div>
        </>
      )}
    </Drawer>
  );
}
