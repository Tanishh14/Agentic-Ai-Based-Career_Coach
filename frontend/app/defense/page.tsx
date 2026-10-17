"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Code2, Play } from "lucide-react";
import { api, Evidence, shortSha, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, Empty, Loading, PageHead, SourceBadge, Spinner, Switch } from "@/components/ui";
import EvidenceDrawer from "@/components/EvidenceDrawer";

function DefenseInner() {
  const router = useRouter();
  const params = useSearchParams();
  const { trackedJob, status } = useAppState();
  const ev = useApi<Evidence[]>("/api/github/evidence?verification=accepted");
  const sessions = useApi<any[]>("/api/interviews?kind=code_defense");
  const [sel, setSel] = useState<string[]>([]);
  const [bug, setBug] = useState(false);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const { busy, run } = useAction();
  useEffect(() => { const e = params.get("evidence"); if (e) setSel([e]); }, [params]);
  const list = useMemo(() => (ev.data || []).filter((e) => e.source_type !== "DEPENDENCY_DECLARATION" && ["intermediate", "substantial"].includes(e.depth) && !e.skill_key.match(/^(python|javascript|typescript|java|go)$/) && (!q || (e.path + e.skill_key + e.reason).toLowerCase().includes(q.toLowerCase()))), [ev.data, q]);
  const toggle = (id: string) => setSel((s) => s.includes(id) ? s.filter((x) => x !== id) : s.length >= 5 ? s : [...s, id]);
  return (
    <>
      <PageHead title="Code defense" subtitle="Defend code you actually wrote. Every question cites a real repository, commit, file and line range that is re-read from GitHub and digest-checked before the session starts." />
      <Alert tone="info">Comprehension is recorded as <b>understanding evidence</b>, separately from implementation evidence. A strong answer never raises a code verdict; a weak answer means “not demonstrated during this interview” — never an accusation of copying.</Alert>
      <div className="grid g-side mt16">
        <Card title={`Select code to defend (${sel.length}/5)`} icon={<Code2 size={16} />} pad={false} actions={<input className="input" style={{ width: 220 }} placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} />}>
          {ev.loading ? <div className="card-body"><Loading /></div> : !list.length ? <div className="card-body"><Empty title="No defensible evidence yet">Analyse a repository first; accepted evidence of intermediate or substantial depth appears here.</Empty></div> : (
            <div className="table-wrap" style={{ maxHeight: 560 }}><table className="tbl"><tbody>{list.map((e) => (
              <tr key={e.id} className={`clickable ${sel.includes(e.id) ? "selected" : ""}`} onClick={() => toggle(e.id)}>
                <td style={{ width: 28 }}><input type="checkbox" checked={sel.includes(e.id)} readOnly aria-label="select" /></td>
                <td><div className="mono xs">{e.repo}:{e.path} L{e.start_line}-{e.end_line} @ {shortSha(e.commit_sha)}</div><div className="small muted mt4">{e.reason}</div></td>
                <td><div className="col" style={{ alignItems: "flex-end", gap: 4 }}><span className="badge sq">{e.skill_key}</span><SourceBadge type={e.source_type} /><button className="btn sm ghost" onClick={(x) => { x.stopPropagation(); setOpen(e.id); }}>View</button></div></td>
              </tr>
            ))}</tbody></table></div>
          )}
        </Card>
        <div className="stack">
          <Card title="Start session">
            <div className="stack">
              <Switch checked={bug} onChange={setBug} disabled={!status?.llm?.configured} label="Add a bug-diagnosis challenge" help={status?.llm?.configured ? "A hypothetical bug is introduced into a copy of one snippet in memory — never committed or executed." : "Requires an LLM provider."} />
              {status?.llm?.configured && !status?.llm?.providers?.[0]?.local && <div className="faint xs">Code is sent to the LLM only if you allowed it in Settings (or the provider is local). Otherwise questions use detector-based templates.</div>}
              <button className="btn primary lg" disabled={!sel.length || !!busy} onClick={() => run("cd", async () => {
                const s = await api.post("/api/interviews/code-defense", { evidence_ids: sel, include_bug_challenge: bug, job_posting_id: trackedJob?.id || null });
                router.push(`/interview/${s.id}`); return s;
              })}>{busy ? <Spinner /> : <Play size={16} />}Validate citations & start</button>
            </div>
          </Card>
          <Card title="Past sessions" pad={false}>
            {!(sessions.data || []).length ? <div className="card-body muted small">None yet.</div> : <div className="table-wrap"><table className="tbl"><tbody>{(sessions.data || []).map((s) => (
              <tr key={s.id} className="clickable" onClick={() => router.push(s.status === "completed" ? `/interview/${s.id}/report` : `/interview/${s.id}`)}><td className="small">{timeAgo(s.created_at)}</td><td><span className="badge">{s.status}</span></td><td className="bold">{s.overall != null ? Math.round(s.overall) : ""}</td></tr>
            ))}</tbody></table></div>}
          </Card>
        </div>
      </div>
      {open && <EvidenceDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

export default function Defense() {
  return <Suspense fallback={<Loading />}><DefenseInner /></Suspense>;
}
