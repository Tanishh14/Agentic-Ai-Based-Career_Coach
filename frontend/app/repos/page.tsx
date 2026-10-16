"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { FolderGit2, GitBranch, Lock, Plus, RefreshCw, Webhook } from "lucide-react";
import { api, shortSha, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState, useEventRefresh } from "@/lib/app";
import { Alert, Card, Empty, ErrorBox, Ext, Loading, Modal, PageHead, Spinner } from "@/components/ui";

function AddScope({ onDone }: { onDone: () => void }) {
  const { status } = useAppState();
  const hasToken = !!status?.github?.token_present;
  const [owner, setOwner] = useState("");
  const [listQuery, setListQuery] = useState<string | null>(null);
  const repos = useApi<any>(listQuery !== null ? `/api/github/repos/available${listQuery ? `?owner=${encodeURIComponent(listQuery)}` : ""}` : null, [listQuery]);
  const [sel, setSel] = useState("");
  const [branches, setBranches] = useState<any[]>([]);
  const [branch, setBranch] = useState("");
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("node_modules/, vendor/, dist/, build/, .venv/, venv/, __pycache__/, *.min.js");
  const [filter, setFilter] = useState("");
  const { busy, run } = useAction();
  useEffect(() => { if (hasToken && listQuery === null) setListQuery(""); }, [hasToken, listQuery]);
  useEffect(() => {
    setBranches([]); setBranch("");
    if (!/^[\w.-]+\/[\w.-]+$/.test(sel)) return;
    const [o, n] = sel.split("/");
    api.get(`/api/github/repos/${o}/${n}/branches`).then((b: any[]) => { setBranches(b); }).catch(() => setBranches([]));
  }, [sel]);
  const list = (repos.data?.repos || []).filter((r: any) => !filter || r.full_name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <Card title="Add a repository to the analysis scope" icon={<Plus size={16} />}>
      <div className="grid g2">
        <div className="stack">
          {!hasToken && (
            <div className="field"><label>List public repositories of a GitHub user</label>
              <div className="row"><input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="github username" /><button className="btn" disabled={!owner} onClick={() => setListQuery(owner)}>List</button></div>
              <div className="help">No token connected: public repositories only (GitHub allows 60 API requests/hour anonymously; file contents are read from GitHub's raw host). <Link href="/settings">Connect a token</Link> for private repos, issues and webhooks.</div>
            </div>
          )}
          <div className="field"><label>Repository (owner/name)</label><input className="input mono" value={sel} onChange={(e) => setSel(e.target.value.trim())} placeholder="owner/repository" /></div>
          {repos.loading && <Loading rows={2} />}
          {repos.error && <ErrorBox error={repos.error} />}
          {list.length > 0 && (
            <div className="card flat" style={{ maxHeight: 260, overflow: "auto" }}>
              <div style={{ padding: 8 }}><input className="input" placeholder={`Filter ${repos.data.repos.length} repositories (${repos.data.source})`} value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
              {list.map((r: any) => (
                <button key={r.github_id} className={`nav-link ${sel === r.full_name ? "active" : ""}`} style={{ width: "100%", border: 0, background: sel === r.full_name ? undefined : "transparent", cursor: "pointer", textAlign: "left" }} onClick={() => setSel(r.full_name)}>
                  {r.private ? <Lock size={13} /> : <FolderGit2 size={13} />}<span className="grow ellipsis">{r.full_name}</span><span className="xs faint">{r.language || ""} · {timeAgo(r.pushed_at)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="stack">
          <div className="field"><label>Branch</label>
            <select className="select" value={branch} onChange={(e) => setBranch(e.target.value)} disabled={!branches.length}>
              <option value="">{branches.length ? "Default branch" : "Select a repository first"}</option>
              {branches.map((b) => <option key={b.name} value={b.name}>{b.name} ({shortSha(b.sha)})</option>)}
            </select></div>
          <div className="field"><label>Include paths (optional)</label><input className="input mono" value={include} onChange={(e) => setInclude(e.target.value)} placeholder="backend/, src/**/*.py" /><div className="help">Comma-separated directories or globs. Empty = whole repository.</div></div>
          <div className="field"><label>Exclude paths</label><input className="input mono" value={exclude} onChange={(e) => setExclude(e.target.value)} /><div className="help">Vendored, generated and documentation files are always ignored (with reasons in the manifest).</div></div>
          <button className="btn primary" disabled={!/^[\w.-]+\/[\w.-]+$/.test(sel) || !!busy} onClick={() => run("add", async () => {
            await api.post("/api/github/scopes", { full_name: sel, branch: branch || null, include_patterns: include.split(",").map((s) => s.trim()).filter(Boolean), exclude_patterns: exclude.split(",").map((s) => s.trim()).filter(Boolean) });
            onDone(); setSel("");
          }, "Scope saved — the agent is scanning the pinned commit now")}>{busy === "add" && <Spinner />}Analyse repository</button>
        </div>
      </div>
    </Card>
  );
}

function WebhookModal({ scope, onClose }: { scope: any; onClose: () => void }) {
  const { status } = useAppState();
  const { busy, run } = useAction();
  const wh = status?.github?.webhook;
  const ok = wh?.public_url?.startsWith("https://");
  return (
    <Modal title="Real-time GitHub webhook" onClose={onClose} footer={<>
      <button className="btn" onClick={onClose}>Close</button>
      <button className="btn primary" disabled={!ok || !!busy} onClick={() => run("hook", async () => { await api.post("/api/github/webhook/setup", { repository_id: scope.repository.id, confirm: true }); onClose(); }, "Webhook created on GitHub")}>{busy && <Spinner />}Create webhook on {scope.repository.full_name}</button>
    </>}>
      <p className="muted">GitHub will POST every push to <code>{wh?.endpoint}</code>. The signature (HMAC-SHA256 over the raw body) is verified before anything is processed, duplicate deliveries are ignored, and the scan runs in the background worker.</p>
      {!ok ? <Alert tone="warn"><b>APP_PUBLIC_URL is not set to a public https URL.</b> GitHub cannot reach localhost. For local development run a tunnel (e.g. <code>ngrok http 8000</code>), put its https URL in <code>.env</code> as APP_PUBLIC_URL and restart. Until then, SHA polling detects pushes automatically every {status?.worker?.poll_interval_seconds || 60}s.</Alert>
        : <Alert>This creates a repository webhook (requires a token with webhook admin permission: fine-grained “Webhooks: read & write”, classic <code>admin:repo_hook</code>).</Alert>}
    </Modal>
  );
}

export default function Repos() {
  const scopes = useApi<any[]>("/api/github/scopes");
  const deliveries = useApi<any[]>("/api/github/webhook/deliveries");
  const [live, setLive] = useState<Record<string, any>>({});
  const [hookFor, setHookFor] = useState<any>(null);
  const { run } = useAction();
  useEventRefresh(["scan_progress", "change_detected", "scope_created"], (t, p) => {
    if (p?.scope_id) setLive((x) => ({ ...x, [p.scope_id]: { type: t, ...p } }));
    scopes.reload(true); deliveries.reload(true);
  });
  return (
    <>
      <PageHead title="Repositories & analysis scope" subtitle="Every scan is pinned to a commit SHA. The scope manifest records exactly which files and detectors were used, so a missing skill means “not demonstrated in this scope” — never “lacks the skill”." />
      <AddScope onDone={() => scopes.reload()} />
      <div className="mt16">
        {scopes.loading ? <Loading /> : scopes.error ? <ErrorBox error={scopes.error} /> : !(scopes.data || []).length ? (
          <Card><Empty icon={<FolderGit2 size={20} />} title="No repositories in scope">Add one of your real repositories above. The agent fetches the tree at a pinned commit, runs AST/lexical detectors and records cited evidence.</Empty></Card>
        ) : (
          <div className="grid g2">
            {(scopes.data || []).map((s) => {
              const l = live[s.id];
              return (
                <Card key={s.id} title={<Ext href={s.repository.html_url}>{s.repository.full_name}</Ext>} icon={s.repository.private ? <Lock size={14} /> : <FolderGit2 size={14} />}
                  actions={<span className={`badge ${s.scan_status === "ready" ? "ok" : s.scan_status === "failed" ? "bad" : "info"}`}>{s.scan_status === "scanning" || s.scan_status === "queued" ? <span className="spinner" style={{ width: 10, height: 10 }} /> : null}{s.scan_status}</span>}>
                  <dl className="kv">
                    <dt>Branch</dt><dd className="row"><GitBranch size={13} />{s.branch} <span className="faint xs">scope v{s.version}</span></dd>
                    <dt>Analysed commit</dt><dd className="mono">{s.snapshot ? <Ext href={s.snapshot.commit_url}>{shortSha(s.last_analyzed_sha)}</Ext> : "—"} {s.snapshot && <span className="faint xs">{(s.snapshot.commit_message || "").split("\n")[0].slice(0, 60)}</span>}</dd>
                    <dt>Files</dt><dd>{s.snapshot ? `${s.snapshot.scanned_file_count} analysed of ${s.snapshot.tree_file_count} · ${s.snapshot.ignored_count} ignored` : "—"}</dd>
                    <dt>Evidence</dt><dd className="row wrap">{Object.entries(s.evidence_counts || {}).map(([k, v]) => <span key={k} className={`badge ${k === "accepted" ? "ok" : k === "rejected" ? "bad" : "warn"}`}>{String(v)} {k}</span>)}</dd>
                    <dt>Change detection</dt><dd className="small">{s.repository.webhook_status === "active" ? "Webhook active + polling fallback" : `SHA polling every ${s.poll_interval_seconds}s`}<div className="faint xs">last poll {timeAgo(s.last_polled_at)} · head {shortSha(s.last_seen_head_sha)}{s.last_event_kind ? ` · last ${s.last_event_kind} ${timeAgo(s.last_event_at)}` : ""}</div></dd>
                    {s.include_patterns?.length > 0 && <><dt>Include</dt><dd className="mono xs">{s.include_patterns.join(", ")}</dd></>}
                  </dl>
                  {s.scan_error && <div className="mt8"><Alert tone="bad">{s.scan_error}</Alert></div>}
                  {s.last_poll_error && <div className="mt8"><Alert tone="warn">Polling: {s.last_poll_error}</Alert></div>}
                  {l && <div className="mt8 small muted">Live: {l.type === "change_detected" ? `push detected via ${l.via} (${shortSha(l.head)})` : `scan ${l.stage}${l.commit_sha ? ` at ${shortSha(l.commit_sha)}` : ""}${l.affected_skills ? ` · affected: ${l.affected_skills.join(", ") || "none"}` : ""}`}</div>}
                  <div className="row wrap mt12">
                    <Link className="btn sm" href={`/repos/${s.id}`}>Scope manifest</Link>
                    <button className="btn sm" onClick={() => run("rs", () => api.post(`/api/github/scopes/${s.id}/rescan`), "Rescan queued (reconciles with the latest commit)")}><RefreshCw size={13} />Check now</button>
                    <button className="btn sm" onClick={() => setHookFor(s)}><Webhook size={13} />Webhook</button>
                    <button className="btn sm danger" onClick={() => { if (confirm("Remove this repository from the active scope? A new assessment version will be created.")) run("rm", async () => { await api.del(`/api/github/scopes/${s.id}`); scopes.reload(); }, "Removed from scope"); }}>Remove</button>
                  </div>
                </Card>
              );
            })}
          </div>
        )}
      </div>
      <Card title="Webhook deliveries" icon={<Webhook size={16} />} className="mt16" pad={false}>
        {!(deliveries.data || []).length ? <div className="card-body muted small">No deliveries received yet. Signed GitHub push events will appear here (invalid signatures are rejected with 401 and logged).</div> : (
          <div className="table-wrap"><table className="tbl"><thead><tr><th>Delivery</th><th>Event</th><th>Signature</th><th>Ref / commit</th><th>Status</th><th>Received</th></tr></thead>
            <tbody>{(deliveries.data || []).map((d) => (
              <tr key={d.delivery_id}><td className="mono xs">{d.delivery_id.slice(0, 13)}</td><td>{d.event}</td><td><span className={`badge ${d.signature_status === "valid" ? "ok" : "bad"}`}>{d.signature_status}</span></td>
                <td className="mono xs">{d.ref} {shortSha(d.after_sha)}</td><td><span className="badge">{d.status}</span>{d.error && <div className="xs faint">{d.error}</div>}</td><td className="small faint">{timeAgo(d.received_at)}</td></tr>
            ))}</tbody></table></div>
        )}
      </Card>
      {hookFor && <WebhookModal scope={hookFor} onClose={() => { setHookFor(null); scopes.reload(true); }} />}
    </>
  );
}
