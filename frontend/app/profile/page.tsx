"use client";
import { useState } from "react";
import { Code, FileUp, ListChecks, Sparkles, Trash2, Trophy } from "lucide-react";
import { api, fmtDate, timeAgo } from "@/lib/api";
import { useAction, useApi } from "@/lib/app";
import { Alert, Card, Empty, ErrorBox, Ext, Loading, PageHead, SourceBadge, Spinner, Tabs } from "@/components/ui";

const STATUS_TONE: Record<string, string> = {
  "claimed, portfolio evidence found": "ok", "partially supported by supplied scope": "warn", "not demonstrated in current GitHub scope": "", "not a code-verifiable claim": "",
};

function ProfileSection() {
  const docs = useApi<any[]>("/api/profile/documents");
  const claims = useApi<any[]>("/api/profile/claims");
  const { busy, run } = useAction();
  const [kind, setKind] = useState("resume");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const [bullets, setBullets] = useState<any>(null);
  const [filter, setFilter] = useState("all");
  const refresh = () => { docs.reload(true); claims.reload(true); };
  const list = (claims.data || []).filter((c) => filter === "all" || c.status === filter);
  return (
    <div className="stack">
      <div className="grid g2">
        <Card title="Add resume or LinkedIn export" icon={<FileUp size={16} />}>
          <div className="stack">
            <div className="field"><label>Document type</label>
              <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
                <option value="resume">Resume (PDF / DOCX / TXT)</option>
                <option value="linkedin_export">LinkedIn “Save to PDF” export</option>
                <option value="pasted_profile">Pasted profile text</option>
              </select></div>
            {kind !== "pasted_profile" ? <div className="field"><label>File</label><input type="file" className="input" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>
              : <div className="field"><label>Profile text</label><textarea className="textarea" value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste your LinkedIn About/Experience/Skills sections or resume text" /></div>}
            <label className="check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />I consent to storing this document locally and parsing it. If an LLM provider is configured, the text is sent to it for structuring. Claims are never treated as proof and are never published.</label>
            <button className="btn primary" disabled={!consent || !!busy || (kind === "pasted_profile" ? text.length < 30 : !file)} onClick={() => run("up", async () => {
              let r;
              if (kind === "pasted_profile") r = await api.post("/api/profile/paste", { text, kind, consent });
              else { const f = new FormData(); f.append("kind", kind); f.append("consent", "true"); f.append("file", file!); r = await api.form("/api/profile/upload", f); }
              refresh(); return r;
            }, (r: any) => `${r.claims} claims extracted with source spans`)}>{busy === "up" && <Spinner />}Parse claims</button>
            <div className="faint xs">LinkedIn profiles are never scraped. Use LinkedIn’s own “More → Save to PDF” export.</div>
          </div>
        </Card>
        <Card title="Documents">
          {docs.loading ? <Loading /> : !(docs.data || []).length ? <div className="muted small">No documents yet.</div> : (
            <div className="col" style={{ gap: 10 }}>
              {(docs.data || []).map((d) => (
                <div key={d.id} className="row between">
                  <div><div className="bold small">{d.filename || d.kind.replace("_", " ")} {d.active ? <span className="badge ok">active</span> : <span className="badge">superseded</span>}</div>
                    <div className="faint xs">{d.kind} · {d.claims} claims · {d.pages} page(s) · {d.parse_method} · {timeAgo(d.created_at)}{d.structured?.llm ? ` · LLM ${d.structured.llm.model}` : ""}</div>
                    {d.parse_warning && <div className="xs" style={{ color: "var(--warn)" }}>{d.parse_warning}</div>}</div>
                  <button className="btn sm danger" onClick={() => run("del", async () => { await api.del(`/api/profile/documents/${d.id}`); refresh(); }, "Document and its claims deleted")}><Trash2 size={13} /></button>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <Card title="Claim audit" icon={<ListChecks size={16} />} pad={false} actions={
        <select className="select" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 280 }}>
          <option value="all">All claims</option>{Object.keys(STATUS_TONE).map((k) => <option key={k} value={k}>{k}</option>)}
        </select>}>
        {claims.loading ? <div className="card-body"><Loading /></div> : !list.length ? <div className="card-body"><Empty title="No claims">Upload a resume or LinkedIn export to compare your claims with repository evidence.</Empty></div> : (
          <div className="table-wrap"><table className="tbl">
            <thead><tr><th>Claim (verbatim span)</th><th>Type</th><th>Skill</th><th>Comparison with GitHub scope</th></tr></thead>
            <tbody>{list.map((c) => (
              <tr key={c.id}>
                <td className="small" style={{ maxWidth: 420 }}>“{c.text}”<div className="xs faint mt4"><SourceBadge type={c.evidence_class} /> {c.document}{c.page ? ` · page ${c.page}` : ""}{c.section ? ` · ${c.section}` : ""} · {c.extraction_method}</div></td>
                <td className="small">{c.claim_type}</td>
                <td className="small">{c.skill || "—"}</td>
                <td><span className={`badge ${STATUS_TONE[c.status] || ""}`}>{c.status}</span>
                  {c.supporting_evidence?.length > 0 && <div className="col mt4" style={{ gap: 2 }}>{c.supporting_evidence.map((e: any) => <Ext key={e.id} href={e.permalink} className="xs mono">{e.repo}:{e.path}</Ext>)}</div>}
                  {c.next_step && <div className="xs faint mt4">{c.next_step}</div>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </Card>
      <Card title="Evidence-grounded resume bullets" icon={<Sparkles size={16} />} actions={<button className="btn sm" disabled={!!busy} onClick={() => run("b", async () => setBullets(await api.post("/api/profile/resume-bullets")))}>{busy === "b" && <Spinner />}Draft from verified evidence</button>}>
        {!bullets ? <div className="muted small">Bullets are drafted only from accepted repository evidence and cite it. Drafts introducing numbers or outcomes absent from the evidence are discarded.</div> : !bullets.bullets.length ? <Alert tone="info">{bullets.note}</Alert> : (
          <div className="col" style={{ gap: 12 }}>
            {bullets.bullets.map((b: any, i: number) => (
              <div key={i} className="card flat sunken" style={{ padding: 12 }}>
                <div>• {b.text}</div>
                <div className="row wrap mt8">{b.evidence.map((e: any) => <Ext key={e.id} href={e.permalink} className="xs mono">{e.repo}:{e.path} L{e.lines}</Ext>)}</div>
              </div>
            ))}
            <div className="faint xs">{bullets.generator} · {bullets.note}</div>
          </div>
        )}
      </Card>
    </div>
  );
}

function LeetCodeSection() {
  const lc = useApi<any>("/api/leetcode/latest");
  const { busy, run } = useAction();
  const [user, setUser] = useState("");
  const [manual, setManual] = useState(false);
  const [m, setM] = useState({ username: "", easy: 0, medium: 0, hard: 0, contest_rating: "", tags: "" });
  const s = lc.data?.latest;
  const tags = Object.entries(s?.fields?.tag_counts || {}).sort((a: any, b: any) => b[1] - a[1]).slice(0, 14) as [string, number][];
  const max = tags[0]?.[1] || 1;
  return (
    <div className="grid g-side">
      <Card title="Public LeetCode profile" icon={<Trophy size={16} />}>
        {lc.loading ? <Loading /> : !s ? <Empty title="No LeetCode data">Enter your public username. Only publicly visible statistics are fetched — never a password or session cookie.</Empty> : (
          <div className="stack">
            <div className="row between wrap">
              <div><div className="bold">{s.username} <Ext href={s.source_url} className="small">profile</Ext></div>
                <div className="faint xs">{s.provider === "user_supplied" ? "User-supplied (not verified)" : "Public GraphQL (unofficial, best-effort)"} · retrieved {fmtDate(s.retrieved_at)} ({timeAgo(s.retrieved_at)})</div></div>
              <span className={`badge ${s.status === "ok" ? "ok" : s.status === "partial" ? "warn" : "violet"}`}>{s.status.replace("_", " ")}</span>
            </div>
            <div className="grid g4">
              {["All", "Easy", "Medium", "Hard"].map((k) => <div key={k} className="card flat sunken stat"><div className="label">{k === "All" ? "Solved" : k}</div><div className="value">{s.fields?.solved?.[k] ?? "unknown"}</div></div>)}
            </div>
            {s.fields?.contest && <div className="small">Contest rating <b>{s.fields.contest.rating ? Math.round(s.fields.contest.rating) : "—"}</b>{s.fields.contest.attendedContestsCount != null && ` · ${s.fields.contest.attendedContestsCount} contests · top ${s.fields.contest.topPercentage}%`}</div>}
            {tags.length > 0 && <div><h4>Solved by topic</h4><div className="chart-bars mt8">{tags.map(([t, n]) => (
              <div key={t} className="meter-row"><span className="ellipsis">{s.fields?.tag_names?.[t] || t}</span><div className="progress"><div style={{ width: `${(n / max) * 100}%` }} /></div><b>{n}</b></div>
            ))}</div></div>}
            {s.fields?.recent_accepted?.length > 0 && <details><summary className="small muted">Recent accepted ({s.fields.recent_accepted.length})</summary>
              <ul className="small">{s.fields.recent_accepted.map((r: any) => <li key={r.id}><Ext href={`https://leetcode.com/problems/${r.titleSlug}/`}>{r.title}</Ext> <span className="faint">{fmtDate(new Date(Number(r.timestamp) * 1000).toISOString())}</span></li>)}</ul></details>}
            <div className="faint xs">Field status: {Object.entries(s.field_status || {}).filter(([k]) => !k.startsWith("_")).map(([k, v]) => `${k}: ${v}`).join(" · ")}</div>
          </div>
        )}
      </Card>
      <div className="stack">
        <Card title="Fetch">
          <div className="field"><label>Username or profile URL</label><input className="input" value={user} onChange={(e) => setUser(e.target.value)} placeholder="https://leetcode.com/u/yourname/" /></div>
          <button className="btn primary mt8" disabled={!user || !!busy} onClick={() => run("lc", async () => { await api.post("/api/leetcode/fetch", { username_or_url: user }); lc.reload(true); }, "Public LeetCode stats retrieved")}>{busy === "lc" && <Spinner />}Fetch public stats</button>
          <div className="hr" />
          <button className="btn sm ghost" onClick={() => setManual(!manual)}>{manual ? "Hide" : "Can’t fetch? Enter stats manually"}</button>
          {manual && <div className="stack mt8">
            <Alert tone="warn">Manually entered stats are stored and displayed as <b>user-supplied (not verified)</b>.</Alert>
            <input className="input" placeholder="username" value={m.username} onChange={(e) => setM({ ...m, username: e.target.value })} />
            <div className="grid g3">{(["easy", "medium", "hard"] as const).map((k) => <div key={k} className="field"><label>{k}</label><input type="number" min={0} className="input" value={m[k]} onChange={(e) => setM({ ...m, [k]: Number(e.target.value) })} /></div>)}</div>
            <input className="input" placeholder="contest rating (optional)" value={m.contest_rating} onChange={(e) => setM({ ...m, contest_rating: e.target.value })} />
            <input className="input" placeholder="tags: dynamic-programming=20, graph=12" value={m.tags} onChange={(e) => setM({ ...m, tags: e.target.value })} />
            <button className="btn" disabled={!m.username || !!busy} onClick={() => run("m", async () => {
              const tag_counts: Record<string, number> = {};
              m.tags.split(",").forEach((p) => { const [k, v] = p.split("="); if (k && v) tag_counts[k.trim()] = Number(v); });
              await api.post("/api/leetcode/manual", { username: m.username, easy: m.easy, medium: m.medium, hard: m.hard, contest_rating: m.contest_rating ? Number(m.contest_rating) : null, tag_counts });
              lc.reload(true);
            }, "Saved as user-supplied")}>Save user-supplied stats</button>
          </div>}
        </Card>
        <Alert tone="info" icon={<Code size={16} />}>{lc.data?.note || "LeetCode is a problem-solving signal only."}</Alert>
        {lc.error && <ErrorBox error={lc.error} />}
      </div>
    </div>
  );
}

export default function Profile() {
  const [tab, setTab] = useState("profile");
  return (
    <>
      <PageHead title="Profile claims & LeetCode" subtitle="Resume/LinkedIn claims and LeetCode activity are separate, lower-weight signals. They never turn a repository verdict into “demonstrated”." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "profile", label: "Resume & LinkedIn claims" }, { key: "lc", label: "LeetCode" }]} />
      {tab === "profile" ? <ProfileSection /> : <LeetCodeSection />}
    </>
  );
}
