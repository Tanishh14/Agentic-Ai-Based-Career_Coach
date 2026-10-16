"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Briefcase, ClipboardPaste, FileUp, Globe2, Link2, MapPin, Search, Star } from "lucide-react";
import { api, ApiError, Job, METHOD_LABEL, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, Empty, ErrorBox, Ext, Loading, PageHead, Progress, Spinner, Tabs, VerdictBadge } from "@/components/ui";

const PROVIDER_ORDER = ["remotive", "arbeitnow", "remoteok", "greenhouse", "lever", "adzuna", "serpapi"];

function AddJob() {
  const router = useRouter();
  const { reload } = useAppState();
  const { busy, run } = useAction();
  const [mode, setMode] = useState("url");
  const [url, setUrl] = useState("");
  const [paste, setPaste] = useState({ text: "", source_url: "", company: "", title: "", location: "" });
  const [file, setFile] = useState<File | null>(null);
  const [fileUrl, setFileUrl] = useState("");
  const [err, setErr] = useState<ApiError | null>(null);

  const done = (j: any) => { reload(); router.push(`/jobs/${j.id}`); };
  const ingestUrl = async () => {
    setErr(null);
    try {
      const j = await api.post("/api/jobs/ingest/url", { url, track: true });
      done(j);
    } catch (e: any) {
      setErr(e);
      if (e?.detail?.fallback === "paste_or_upload") { setPaste((p) => ({ ...p, source_url: url })); setFileUrl(url); }
    }
  };
  return (
    <Card title="Add a real job or internship" icon={<Briefcase size={16} />}>
      <Tabs value={mode} onChange={(m) => { setMode(m); setErr(null); }} tabs={[
        { key: "url", label: <span className="row"><Link2 size={14} />Job URL</span> },
        { key: "paste", label: <span className="row"><ClipboardPaste size={14} />Paste description</span> },
        { key: "file", label: <span className="row"><FileUp size={14} />Upload PDF / text</span> },
      ]} />
      {mode === "url" && (
        <div className="stack">
          <div className="field">
            <label htmlFor="jurl">Posting URL</label>
            <div className="row">
              <input id="jurl" className="input" placeholder="https://boards.greenhouse.io/<company>/jobs/<id>, jobs.lever.co/…, a careers page, LinkedIn or Naukri URL" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && url && ingestUrl()} />
              <button className="btn primary" disabled={!url || busy !== null} onClick={() => run("url", ingestUrl)}>{busy === "url" ? <Spinner /> : null}Fetch & analyse</button>
            </div>
            <div className="help">Greenhouse, Lever and Ashby links are read through their public APIs. Other pages are fetched only if robots.txt allows it — no login, cookies or bot-protection bypass. LinkedIn/Naukri pages usually require pasting the text.</div>
          </div>
          {err && <ErrorBox error={err} />}
          {err?.detail?.fallback === "paste_or_upload" && (
            <Alert tone="info">The URL is kept as provenance. <button className="btn sm" onClick={() => setMode("paste")}>Paste the description instead</button> <button className="btn sm" onClick={() => setMode("file")}>Upload a PDF</button></Alert>
          )}
        </div>
      )}
      {mode === "paste" && (
        <div className="stack">
          <div className="grid g2">
            <div className="field"><label>Source URL (provenance, optional)</label><input className="input" value={paste.source_url} onChange={(e) => setPaste({ ...paste, source_url: e.target.value })} placeholder="https://www.linkedin.com/jobs/view/…" /></div>
            <div className="field"><label>Company (only if stated in the posting)</label><input className="input" value={paste.company} onChange={(e) => setPaste({ ...paste, company: e.target.value })} /></div>
            <div className="field"><label>Role title</label><input className="input" value={paste.title} onChange={(e) => setPaste({ ...paste, title: e.target.value })} /></div>
            <div className="field"><label>Location</label><input className="input" value={paste.location} onChange={(e) => setPaste({ ...paste, location: e.target.value })} /></div>
          </div>
          <div className="field"><label>Job description text</label><textarea className="textarea" style={{ minHeight: 220 }} value={paste.text} onChange={(e) => setPaste({ ...paste, text: e.target.value })} placeholder="Paste the full posting text exactly as published." /></div>
          <div className="row"><button className="btn primary" disabled={paste.text.length < 40 || !!busy} onClick={() => run("paste", async () => done(await api.post("/api/jobs/ingest/text", { ...paste, source_url: paste.source_url || null, track: true })))}>{busy === "paste" && <Spinner />}Analyse pasted text</button>
            <span className="faint small">{paste.text.length} characters</span></div>
        </div>
      )}
      {mode === "file" && (
        <div className="stack">
          <div className="field"><label>File (PDF, DOCX, TXT, HTML)</label><input className="input" type="file" accept=".pdf,.docx,.txt,.md,.html,.htm" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>
          <div className="field"><label>Source URL (optional provenance)</label><input className="input" value={fileUrl} onChange={(e) => setFileUrl(e.target.value)} /></div>
          <div><button className="btn primary" disabled={!file || !!busy} onClick={() => run("file", async () => {
            const f = new FormData(); f.append("file", file!); if (fileUrl) f.append("source_url", fileUrl);
            done(await api.form("/api/jobs/ingest/file", f));
          })}>{busy === "file" && <Spinner />}Upload & analyse</button></div>
        </div>
      )}
    </Card>
  );
}

function Discovery() {
  const status = useApi<any>("/api/status");
  const { busy, run } = useAction();
  const router = useRouter();
  const [q, setQ] = useState({ query: "", location: "", level: "any", remote_only: false, greenhouse: "", lever: "" });
  const [providers, setProviders] = useState<string[]>(["remotive", "arbeitnow", "remoteok", "greenhouse", "lever", "adzuna", "serpapi"]);
  const [res, setRes] = useState<any>(null);
  const jp = status.data?.job_providers || {};
  const search = async () => {
    const r = await api.post("/api/discovery/search", {
      query: q.query, location: q.location || null, level: q.level, remote_only: q.remote_only, providers,
      greenhouse_boards: q.greenhouse.split(/[,\s]+/).filter(Boolean), lever_sites: q.lever.split(/[,\s]+/).filter(Boolean),
    });
    setRes(r);
    return r;
  };
  return (
    <div className="stack">
      <Card title="Search live, permitted job sources" icon={<Globe2 size={16} />}>
        <div className="grid g4">
          <div className="field" style={{ gridColumn: "span 2" }}><label>Role / keywords</label><input className="input" value={q.query} onChange={(e) => setQ({ ...q, query: e.target.value })} placeholder="backend intern, python developer…" onKeyDown={(e) => e.key === "Enter" && q.query && run("search", search)} /></div>
          <div className="field"><label>Location</label><input className="input" value={q.location} onChange={(e) => setQ({ ...q, location: e.target.value })} placeholder="Bengaluru, Remote, Berlin…" /></div>
          <div className="field"><label>Level</label><select className="select" value={q.level} onChange={(e) => setQ({ ...q, level: e.target.value })}><option value="any">Any</option><option value="internship">Internship</option><option value="entry">Entry level / graduate</option></select></div>
          <div className="field"><label>Greenhouse boards</label><input className="input" value={q.greenhouse} onChange={(e) => setQ({ ...q, greenhouse: e.target.value })} placeholder="e.g. stripe, airbnb" /></div>
          <div className="field"><label>Lever sites</label><input className="input" value={q.lever} onChange={(e) => setQ({ ...q, lever: e.target.value })} placeholder="company ids from jobs.lever.co/<id>" /></div>
          <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={q.remote_only} onChange={(e) => setQ({ ...q, remote_only: e.target.checked })} />Remote only</label>
        </div>
        <div className="row wrap mt12">
          {PROVIDER_ORDER.map((p) => (
            <label key={p} className="badge" style={{ cursor: "pointer", height: 28 }} title={jp[p]?.needs || jp[p]?.attribution || ""}>
              <input type="checkbox" checked={providers.includes(p)} onChange={(e) => setProviders(e.target.checked ? [...providers, p] : providers.filter((x) => x !== p))} />
              {jp[p]?.label || p}{jp[p] && !jp[p].configured && <span className="faint">(not configured)</span>}
            </label>
          ))}
          <div className="grow" />
          <button className="btn primary" disabled={!q.query || !!busy} onClick={() => run("search", search, (r: any) => `${r.results.length} real postings found (${r.duplicates} duplicates removed)`)}>{busy === "search" ? <Spinner /> : <Search size={15} />}Search</button>
        </div>
        <div className="faint xs mt8">LinkedIn/Naukri listings appear only when a licensed provider (SerpApi Google Jobs) returns their source links — no scraping. Remotive/RemoteOK results require attribution, shown on each result.</div>
      </Card>
      {res && (
        <Card title={`Results · ${res.results.length}`} actions={<span className="faint xs">Ranked by your current requirement coverage (not a hiring probability)</span>} pad={false}>
          <div className="row wrap" style={{ padding: "10px 18px", borderBottom: "1px solid var(--border)", gap: 6 }}>
            {Object.entries(res.providers).map(([p, s]: any) => (
              <span key={p} className={`badge ${s.status === "ok" ? "ok" : s.status === "error" ? "bad" : ""}`} title={s.error || s.reason || ""}>{jp[p]?.label || p}: {s.status === "ok" ? `${s.count}` : s.status.replace("_", " ")}</span>
            ))}
          </div>
          {!res.results.length ? <Empty title="No matching postings">Try broader keywords, remove the level filter, or add Greenhouse/Lever boards.</Empty> : (
            <div className="table-wrap"><table className="tbl"><tbody>
              {res.results.map((j: Job) => (
                <tr key={j.id}>
                  <td style={{ width: "48%" }}>
                    <div className="bold">{j.title}</div>
                    <div className="small muted row wrap">{j.company && <span>{j.company}</span>}{j.location && <span className="row"><MapPin size={12} />{j.location}</span>}{j.posted_at && <span className="faint">{timeAgo(j.posted_at)}</span>}</div>
                    <div className="xs faint mt4 row wrap"><span className="badge sq">{j.provider}</span>{j.platform !== j.provider && <span className="badge sq info">{j.platform}</span>}{j.meta?.attribution && <span>{j.meta.attribution}</span>}{j.description_is_partial && <span className="badge sq warn">snippet only</span>}</div>
                  </td>
                  <td style={{ width: "30%" }}>
                    {j.readiness?.coverage_indicator != null ? <>
                      <div className="row between small"><span className="muted">Coverage</span><b>{j.readiness.coverage_indicator}%</b></div>
                      <Progress value={j.readiness.coverage_indicator} />
                      <div className="xs faint mt4">{j.readiness.requirements.length} requirements · {Object.entries(j.readiness.counts).map(([k, v]) => `${v} ${k.split("_")[0].toLowerCase()}`).join(", ")}</div>
                    </> : <span className="faint small">No taxonomy skills detected in this listing</span>}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div className="col" style={{ alignItems: "flex-end" }}>
                      <button className="btn sm primary" disabled={!!busy} onClick={() => run(`an-${j.id}`, async () => { const r = await api.post(`/api/jobs/${j.id}/analyze`); router.push(`/jobs/${j.id}`); return r; }, "Deep analysis complete")}>{busy === `an-${j.id}` && <Spinner />}Analyse & target</button>
                      <Ext href={j.source_url} className="small">Original posting</Ext>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody></table></div>
          )}
        </Card>
      )}
    </div>
  );
}

function Saved() {
  const jobs = useApi<Job[]>("/api/jobs?status=analyzed");
  const { reload } = useAppState();
  const { run } = useAction();
  if (jobs.loading) return <Loading />;
  if (jobs.error) return <ErrorBox error={jobs.error} onRetry={() => jobs.reload()} />;
  if (!jobs.data?.length) return <Empty title="No analysed jobs yet">Add a job by URL, paste or upload, or analyse a search result.</Empty>;
  return (
    <Card pad={false}>
      <div className="table-wrap"><table className="tbl">
        <thead><tr><th>Job</th><th>Source</th><th>Latest assessment</th><th /></tr></thead>
        <tbody>{jobs.data.map((j) => (
          <tr key={j.id} className={j.is_tracked ? "selected" : ""}>
            <td><Link href={`/jobs/${j.id}`} className="bold">{j.title || "Untitled posting"}</Link><div className="small muted">{j.company || "company not stated"}{j.location ? ` · ${j.location}` : ""}</div></td>
            <td className="small"><div>{METHOD_LABEL[j.ingestion_method] || j.ingestion_method}</div><div className="faint">{j.platform} · {timeAgo(j.retrieved_at)}</div></td>
            <td>{j.latest_assessment ? <div className="small"><b>{j.latest_assessment.coverage}%</b> coverage · v{j.latest_assessment.version}<div className="row wrap mt4">{Object.entries(j.latest_assessment.counts || {}).map(([k, v]) => <span key={k} className="xs faint">{String(v)} {k.toLowerCase().replaceAll("_", " ")}</span>)}</div></div> : <span className="faint small">none</span>}</td>
            <td style={{ textAlign: "right" }}>{j.is_tracked ? <span className="badge accent"><Star size={12} />Target</span> : <button className="btn sm" onClick={() => run("t", async () => { await api.post(`/api/jobs/${j.id}/track`); reload(); jobs.reload(true); }, "Target job updated")}>Make target</button>}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </Card>
  );
}

export default function JobsPage() {
  const [tab, setTab] = useState("add");
  return (
    <>
      <PageHead title="Job discovery" subtitle="Only real postings: a URL you provide, text/PDF you paste or upload, or live results from permitted providers. Source link, ingestion method and timestamp are kept for every job." />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "add", label: "Add a job" }, { key: "search", label: "Search live sources" }, { key: "saved", label: "Analysed jobs" }]} />
      {tab === "add" && <AddJob />}
      {tab === "search" && <Discovery />}
      {tab === "saved" && <Saved />}
    </>
  );
}
