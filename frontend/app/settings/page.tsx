"use client";
import { useEffect, useState } from "react";
import { Bot, Database, FolderGit2, KeyRound, Mic, Shield, Trash2 } from "lucide-react";
import { api, API_BASE, fmtDate } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, ErrorBox, Loading, PageHead, Spinner, Switch } from "@/components/ui";

const PRESETS = [
  { label: "OpenCode Zen", base_url: "https://opencode.ai/zen/v1", model: "", hint: "Free models listed in OpenCode's docs; needs an API key." },
  { label: "Groq", base_url: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile", hint: "Free tier with rate limits." },
  { label: "OpenRouter", base_url: "https://openrouter.ai/api/v1", model: "", hint: "Many models incl. free ones (\":free\" suffix)." },
  { label: "Google Gemini", base_url: "https://generativelanguage.googleapis.com/v1beta/openai", model: "gemini-2.0-flash", hint: "OpenAI-compatible endpoint." },
  { label: "Ollama (local)", base_url: "http://127.0.0.1:11434/v1", model: "", hint: "Runs on this machine; no data leaves your device." },
  { label: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", hint: "" },
];

function GitHubCard() {
  const { status, reload } = useAppState();
  const [token, setToken] = useState("");
  const { busy, run } = useAction();
  const gh = status?.github;
  return (
    <Card title="GitHub" icon={<FolderGit2 size={16} />}>
      {gh?.status === "connected" ? (
        <div className="stack">
          <Alert tone="ok">Connected as <b>@{gh.config?.login}</b> ({gh.config?.token_type}) · token {gh.credential_hint} · scopes: {gh.scopes?.join(", ") || "fine-grained (per-repository permissions)"}</Alert>
          <div className="small muted">Rate limit: {gh.rate_limit?.remaining ?? "?"}/{gh.rate_limit?.limit ?? "?"} remaining{gh.rate_limit?.reset ? `, resets ${fmtDate(new Date(gh.rate_limit.reset * 1000).toISOString())}` : ""}</div>
          <div><button className="btn danger" onClick={() => run("dc", async () => { await api.del("/api/integrations/github"); reload(); }, "GitHub disconnected")}>Disconnect</button></div>
        </div>
      ) : (
        <div className="stack">
          <p className="small muted">Paste a personal access token. It is verified against GitHub, encrypted at rest (Fernet) and never shown again. Your GitHub password is never requested.</p>
          <ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>
            <li><b>Fine-grained (recommended):</b> select only the repositories to analyse; permissions Contents: read, Metadata: read; optional Issues: read & write (gap tasks) and Webhooks: read & write (auto webhook).</li>
            <li><b>Classic:</b> <code>public_repo</code> (or <code>repo</code> for private), optional <code>admin:repo_hook</code>.</li>
          </ul>
          <div className="row"><input className="input mono" type="password" autoComplete="off" placeholder="github_pat_… or ghp_…" value={token} onChange={(e) => setToken(e.target.value)} />
            <button className="btn primary" disabled={token.length < 20 || !!busy} onClick={() => run("gh", async () => { const r = await api.post("/api/integrations/github", { token }); setToken(""); reload(); return r; }, (r: any) => `Connected as @${r.login}`)}>{busy === "gh" && <Spinner />}Connect</button></div>
          {gh?.token_present && <div className="faint xs">A token from GITHUB_TOKEN in .env is currently in use.</div>}
        </div>
      )}
    </Card>
  );
}

function LLMCard() {
  const { status, reload } = useAppState();
  const ollama = useApi<any>("/api/integrations/ollama/models");
  const [slot, setSlot] = useState("llm_primary");
  const [f, setF] = useState({ base_url: "", model: "", api_key: "" });
  const [test, setTest] = useState<any>(null);
  const { busy, run } = useAction();
  const llm = status?.llm;
  return (
    <Card title="AI provider (LLM)" icon={<Bot size={16} />}>
      <div className="stack">
        {llm?.configured ? <div className="col" style={{ gap: 4 }}>{llm.providers.map((p: any) => <div key={p.slot} className="small"><span className="badge sq">{p.slot === "llm_primary" ? "primary" : "fallback"}</span> {p.model} · <span className="mono xs">{p.base_url}</span> · {p.local ? "local" : "remote"} · from {p.source}</div>)}</div>
          : <Alert tone="warn">No LLM configured. The app still works with deterministic extraction, template questions and heuristic feedback, all clearly labelled.</Alert>}
        <div className="row wrap">{PRESETS.map((p) => <button key={p.label} className="btn sm" title={p.hint} onClick={() => setF({ ...f, base_url: p.base_url, model: p.model })}>{p.label}</button>)}</div>
        {ollama.data?.available && <div className="small">Ollama detected with models: {ollama.data.models.map((m: any) => <button key={m.name} className="btn sm ghost" onClick={() => setF({ base_url: ollama.data.base_url, model: m.name, api_key: "" })}>{m.name}</button>)}
          {ollama.data.models.every((m: any) => (m.capabilities || []).includes("vision")) && <div className="faint xs">These look like vision models; for best results pull a text/instruct model, e.g. <code>ollama pull qwen2.5:7b-instruct</code>.</div>}</div>}
        <div className="grid g2">
          <div className="field"><label>Slot</label><select className="select" value={slot} onChange={(e) => setSlot(e.target.value)}><option value="llm_primary">Primary</option><option value="llm_fallback">Fallback</option></select></div>
          <div className="field"><label>Model</label><input className="input mono" value={f.model} onChange={(e) => setF({ ...f, model: e.target.value })} placeholder="model id" /></div>
          <div className="field"><label>Base URL (OpenAI-compatible)</label><input className="input mono" value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} /></div>
          <div className="field"><label>API key</label><input className="input mono" type="password" autoComplete="off" value={f.api_key} onChange={(e) => setF({ ...f, api_key: e.target.value })} placeholder="(kept if left empty)" /></div>
        </div>
        <div className="row"><button className="btn primary" disabled={!f.base_url || !f.model || !!busy} onClick={() => run("llm", async () => { const r = await api.post("/api/integrations/llm", { slot, ...f, api_key: f.api_key || null }); setTest(r.test); reload(); return r; })}>{busy === "llm" && <Spinner />}Save & test</button>
          {llm?.configured && <button className="btn" onClick={() => run("t", async () => setTest((await api.post("/api/integrations/llm/test")).results))}>Test configured</button>}</div>
        {test && <pre className="plain">{JSON.stringify(test, null, 2)}</pre>}
      </div>
    </Card>
  );
}

function TranscriptionCard() {
  const { status, reload } = useAppState();
  const tr = status?.transcription;
  const [p, setP] = useState({ provider: "local_whisper", model: "base", base_url: "", secret: "" });
  const { busy, run } = useAction();
  useEffect(() => { if (tr) setP((x) => ({ ...x, provider: tr.provider === "none" ? "local_whisper" : tr.provider, model: tr.model || x.model })); }, [tr?.provider]); // eslint-disable-line
  return (
    <Card title="Speech-to-text" icon={<Mic size={16} />}>
      <div className="stack">
        <Alert tone={tr?.available ? "ok" : "warn"}>{tr?.available ? `${tr.provider} (${tr.model}) available${tr.third_party_upload ? " — audio is uploaded to a third party (users are asked for consent)" : " — audio stays on this machine"}` : `Unavailable: ${(tr?.missing || []).join(", ")}`}</Alert>
        <div className="grid g2">
          <div className="field"><label>Provider</label><select className="select" value={p.provider} onChange={(e) => setP({ ...p, provider: e.target.value })}><option value="local_whisper">Local Whisper (openai-whisper)</option><option value="openai_compatible">OpenAI-compatible API (Groq/OpenAI)</option><option value="none">Disabled (typed only)</option></select></div>
          <div className="field"><label>Model</label><input className="input mono" value={p.model} onChange={(e) => setP({ ...p, model: e.target.value })} placeholder={p.provider === "local_whisper" ? "tiny / base / small" : "whisper-large-v3"} /></div>
          {p.provider === "openai_compatible" && <><div className="field"><label>Base URL</label><input className="input mono" value={p.base_url} onChange={(e) => setP({ ...p, base_url: e.target.value })} placeholder="https://api.groq.com/openai/v1" /></div>
            <div className="field"><label>API key</label><input className="input mono" type="password" value={p.secret} onChange={(e) => setP({ ...p, secret: e.target.value })} /></div></>}
        </div>
        <div><button className="btn primary" disabled={!!busy} onClick={() => run("tr", async () => { await api.post("/api/integrations/provider", { provider: "transcription", secret: p.secret || null, config: { provider: p.provider, model: p.model, base_url: p.base_url } }); reload(); }, "Saved")}>Save</button></div>
      </div>
    </Card>
  );
}

function JobKeysCard() {
  const { status, reload } = useAppState();
  const [a, setA] = useState({ app_id: "", key: "", country: "in" });
  const [s, setS] = useState("");
  const { run } = useAction();
  const jp = status?.job_providers || {};
  return (
    <Card title="Job search providers" icon={<KeyRound size={16} />}>
      <div className="stack">
        <div className="small muted">Keyless and always on: Remotive, Arbeitnow, Remote OK, Greenhouse and Lever public boards. Optional keyed providers:</div>
        <div className="field"><label>Adzuna {jp.adzuna?.configured && <span className="badge ok">configured</span>}</label>
          <div className="row"><input className="input" placeholder="app id" value={a.app_id} onChange={(e) => setA({ ...a, app_id: e.target.value })} /><input className="input" type="password" placeholder="app key" value={a.key} onChange={(e) => setA({ ...a, key: e.target.value })} />
            <input className="input" style={{ width: 80 }} value={a.country} onChange={(e) => setA({ ...a, country: e.target.value })} title="country code" />
            <button className="btn" disabled={!a.app_id || !a.key} onClick={() => run("az", async () => { await api.post("/api/integrations/provider", { provider: "adzuna", secret: a.key, config: { app_id: a.app_id, country: a.country } }); reload(); }, "Adzuna saved")}>Save</button></div></div>
        <div className="field"><label>SerpApi Google Jobs {jp.serpapi?.configured && <span className="badge ok">configured</span>}</label>
          <div className="row"><input className="input" type="password" placeholder="API key" value={s} onChange={(e) => setS(e.target.value)} />
            <button className="btn" disabled={!s} onClick={() => run("sp", async () => { await api.post("/api/integrations/provider", { provider: "serpapi", secret: s }); reload(); }, "SerpApi saved")}>Save</button></div>
          <div className="help">Surfaces LinkedIn/Naukri/Indeed source links through a licensed search API — the app never scrapes those sites.</div></div>
      </div>
    </Card>
  );
}

function PrivacyCard() {
  const st = useApi<any>("/api/settings");
  const { reload } = useAppState();
  const { run } = useAction();
  const [confirm, setConfirm] = useState("");
  const set = (k: string, v: any) => run("s", async () => { st.setData(await api.patch("/api/settings", { [k]: v })); reload(); });
  if (st.loading) return <Loading />;
  const d = st.data || {};
  return (
    <Card title="Privacy & behaviour" icon={<Shield size={16} />}>
      <div className="stack">
        <div className="field"><label>Display name</label><input className="input" defaultValue={d.display_name} onBlur={(e) => e.target.value !== d.display_name && set("display_name", e.target.value)} /></div>
        <Switch checked={!!d.allow_code_snippets_to_llm} onChange={(v) => set("allow_code_snippets_to_llm", v)} label="Allow sending code snippets to a remote LLM" help="Used for evidence corroboration (can only lower confidence), code-defense questions and interview context. Local providers (Ollama) are always allowed." />
        <Switch checked={!!d.auto_close_issues} onChange={(v) => set("auto_close_issues", v)} label="Auto-close gap issues when all criteria are verified" help="Off by default — you confirm each close." />
        <Switch checked={!!d.sandbox_allow_network} onChange={(v) => set("sandbox_allow_network", v)} label="Allow network inside sandbox runs" help="Needed to install dependencies; off by default." />
        <div className="hr" />
        <div className="row wrap"><a className="btn" href={`${API_BASE}/api/privacy/export`} target="_blank" rel="noreferrer"><Database size={14} />Export my data (JSON)</a></div>
        <div className="field"><label>Delete all my data (jobs, repositories, evidence, assessments, interviews, recordings, passports, credentials)</label>
          <div className="row"><input className="input" placeholder="type DELETE MY DATA" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
            <button className="btn danger" disabled={confirm !== "DELETE MY DATA"} onClick={() => run("del", async () => { await api.post("/api/privacy/delete-all", { confirm }); setConfirm(""); reload(); }, "All data deleted")}><Trash2 size={14} />Delete</button></div></div>
      </div>
    </Card>
  );
}

export default function SettingsPage() {
  const { status, error } = useAppState();
  return (
    <>
      <PageHead title="Integrations & privacy" subtitle="Everything here is optional except a real job and a real repository. Missing integrations disable only their feature and say exactly what is missing." />
      {error && <ErrorBox error={error} />}
      {status && !status.worker?.running && <Alert tone="warn"><b>Background worker not running.</b> Automatic change detection, transcription and evaluation jobs need it. Start everything with <code>python run.py</code> (or <code>python -m backend.app.workers.worker</code>).</Alert>}
      <div className="grid g2 mt16">
        <div className="stack"><GitHubCard /><TranscriptionCard /><JobKeysCard /></div>
        <div className="stack"><LLMCard /><PrivacyCard />
          <Card title="System">
            <dl className="kv">
              <dt>Database</dt><dd className="mono xs">{status?.database?.url}</dd>
              <dt>Journal / FKs</dt><dd>{status?.database?.journal_mode} · foreign_keys {String(status?.database?.foreign_keys)}</dd>
              <dt>Migration</dt><dd className="mono xs">{status?.database?.migration_version}</dd>
              <dt>Webhook endpoint</dt><dd className="mono xs">{status?.github?.webhook?.endpoint}</dd>
              <dt>Webhook secret</dt><dd>{status?.github?.webhook?.secret_configured ? "set in .env" : "auto-generated per user when a webhook is created"}</dd>
              <dt>Sandbox</dt><dd className="small">{status?.sandbox?.available ? `Docker ${status.sandbox.server_version}` : status?.sandbox?.reason}</dd>
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
