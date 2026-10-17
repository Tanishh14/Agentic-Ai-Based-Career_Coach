"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Camera, CameraOff, CheckCircle2, Keyboard, Mic, MicOff, Pause, Play, SkipForward, Square, Video } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAction, useEventRefresh, useToast } from "@/lib/app";
import { Alert, Card, CodeView, DiffView, ErrorBox, Ext, Loading, Spinner, Switch } from "./ui";
import Feedback from "./Feedback";

type Phase = "READY" | "PREPARATION" | "RECORDING" | "SUBMITTED" | "TRANSCRIBING" | "REVIEW_TRANSCRIPT" | "EVALUATING" | "NEXT_QUESTION" | "COMPLETE";
const PHASES: Phase[] = ["READY", "PREPARATION", "RECORDING", "SUBMITTED", "TRANSCRIBING", "REVIEW_TRANSCRIPT", "EVALUATING", "NEXT_QUESTION", "COMPLETE"];
const MIME_CANDIDATES = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4", "audio/aac"];

function pickMime(): string | undefined {
  if (typeof window === "undefined" || typeof (window as any).MediaRecorder === "undefined") return undefined;
  return MIME_CANDIDATES.find((m) => (window as any).MediaRecorder.isTypeSupported?.(m));
}

function fmt(s: number) { const m = Math.floor(Math.max(0, s) / 60); const r = Math.max(0, s) % 60; return `${m}:${String(r).padStart(2, "0")}`; }

export default function InterviewRoom({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const { push } = useToast();
  const { busy, run } = useAction();
  const [s, setS] = useState<any>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [phase, setPhase] = useState<Phase>("READY");
  const [qid, setQid] = useState<string | null>(null);
  const [left, setLeft] = useState(0);
  const [typed, setTyped] = useState("");
  const [transcript, setTranscript] = useState("");
  const [caption, setCaption] = useState("");
  const [caps, setCaps] = useState<any>(null);
  // media
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [mediaErr, setMediaErr] = useState<string | null>(null);
  const [wantCam, setWantCam] = useState(true);
  const [wantMic, setWantMic] = useState(true);
  const [uploadConsent, setUploadConsent] = useState(false);
  const [retain, setRetain] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const recStart = useRef<number>(0);
  const recogRef = useRef<any>(null);
  const timerRef = useRef<any>(null);
  const finishingRef = useRef(false);

  const load = useCallback(async () => {
    try { const d = await api.get(`/api/interviews/${sessionId}`); setS(d); setErr(null); return d; } catch (e: any) { setErr(e); return null; }
  }, [sessionId]);
  useEffect(() => { load(); api.get("/api/interviews/capabilities").then(setCaps).catch(() => {}); }, [load]);

  const questions: any[] = s?.questions || [];
  const current = useMemo(() => questions.find((q) => q.id === qid) || null, [questions, qid]);

  // derive phase from server state (refresh recovery)
  const sync = useCallback((d: any, keepQ = true) => {
    if (!d) return;
    if (d.status === "completed") { setPhase("COMPLETE"); return; }
    if (d.status !== "active") return;
    const qs: any[] = d.questions;
    let q = keepQ && qid ? qs.find((x) => x.id === qid) : null;
    if (!q) {
      q = qs.find((x) => !x.answer || x.answer.status !== "evaluated") || null;
      if (!q) { setPhase("COMPLETE"); return; }
    }
    if (q.id !== qid) setQid(q.id);
    const a = q.answer;
    if (!a) { if (!["PREPARATION", "RECORDING", "SUBMITTED"].includes(phase) || q.id !== qid) setPhase("READY"); }
    else if (a.status === "transcribing") setPhase("TRANSCRIBING");
    else if (a.status === "review") { setPhase("REVIEW_TRANSCRIPT"); setTranscript(a.transcript_final || ""); }
    else if (a.status === "evaluating") setPhase("EVALUATING");
    else if (a.status === "evaluated") setPhase("NEXT_QUESTION");
  }, [qid, phase]);

  useEffect(() => { if (s && !qid) sync(s, false); }, [s]); // eslint-disable-line
  // when the user jumps to another question, derive its phase from the persisted answer
  useEffect(() => {
    if (!current || s?.status !== "active") return;
    const a = current.answer;
    if (!a) { if (!["PREPARATION", "RECORDING", "SUBMITTED"].includes(phase)) setPhase("READY"); }
    else if (a.status === "transcribing") setPhase("TRANSCRIBING");
    else if (a.status === "review") { setTranscript(a.transcript_final || ""); setPhase("REVIEW_TRANSCRIPT"); }
    else if (a.status === "evaluating") setPhase("EVALUATING");
    else setPhase("NEXT_QUESTION");
  }, [qid]); // eslint-disable-line

  // live updates + polling while waiting on the worker
  useEventRefresh(["interview_answer"], async () => { const d = await load(); if (d) sync(d); });
  useEffect(() => {
    if (phase !== "TRANSCRIBING" && phase !== "EVALUATING") return;
    const t = setInterval(async () => { const d = await load(); if (d) sync(d); }, 1500);
    return () => clearInterval(t);
  }, [phase, load, sync]);

  // video preview
  useEffect(() => { if (videoRef.current) videoRef.current.srcObject = stream; }, [stream, phase]);
  const stopMedia = useCallback(() => {
    try { recRef.current?.state === "recording" && recRef.current.stop(); } catch { /* */ }
    try { recogRef.current?.stop(); } catch { /* */ }
    stream?.getTracks().forEach((t) => t.stop());
    setStream(null);
  }, [stream]);
  useEffect(() => () => { stream?.getTracks().forEach((t) => t.stop()); }, [stream]);
  useEffect(() => { if (phase === "COMPLETE") stopMedia(); }, [phase]); // eslint-disable-line

  const enableMedia = async () => {
    setMediaErr(null);
    if (!navigator.mediaDevices?.getUserMedia) { setMediaErr("This browser does not expose camera/microphone (requires HTTPS or localhost). Use typed answers."); return; }
    try {
      const st = await navigator.mediaDevices.getUserMedia({ video: wantCam ? { width: 640, height: 400 } : false, audio: wantMic });
      st.getTracks().forEach((t) => (t.onended = () => { setMediaErr("A media device was disconnected or permission was revoked. Continue with typed answers."); setStream(null); }));
      setStream(st);
    } catch (e: any) {
      setMediaErr(e?.name === "NotAllowedError" ? "Permission denied. You can still complete the interview with typed answers." : `Could not start devices: ${e?.message || e}`);
    }
  };

  const start = async () => {
    const r = await run("start", () => api.post(`/api/interviews/${sessionId}/start`, {
      camera: !!stream?.getVideoTracks().length, microphone: !!stream?.getAudioTracks().length, audio_upload: uploadConsent && !!stream?.getAudioTracks().length, retain_audio: retain,
    }));
    if (r) { setS(r); sync(r, false); }
  };

  // timers drive the state machine
  const clearTimer = () => { clearInterval(timerRef.current); timerRef.current = null; };
  const beginPrep = () => { if (!current) return; setTyped(""); setCaption(""); setLeft(current.prep_seconds); setPhase("PREPARATION"); };
  const beginRecording = useCallback(() => {
    if (!current) return;
    clearTimer();
    setLeft(current.answer_seconds);
    chunks.current = [];
    finishingRef.current = false;
    const audioTracks = stream?.getAudioTracks().filter((t) => t.readyState === "live") || [];
    if (audioTracks.length && s?.consent?.audio_upload) {
      const mime = pickMime();
      try {
        const rec = new MediaRecorder(new MediaStream(audioTracks), mime ? { mimeType: mime } : undefined);
        rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.current.push(e.data); };
        rec.start(1000);
        recRef.current = rec;
        recStart.current = Date.now();
      } catch (e: any) { push("bad", `Recording unavailable (${e?.message}); type your answer.`); }
      const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      if (SR) {
        try {
          const r = new SR(); r.continuous = true; r.interimResults = true;
          r.onresult = (ev: any) => { let t = ""; for (let i = ev.resultIndex; i < ev.results.length; i++) t += ev.results[i][0].transcript; setCaption(t.slice(-160)); };
          r.onerror = () => {}; r.start(); recogRef.current = r;
        } catch { /* captions optional */ }
      }
    } else { recStart.current = Date.now(); }
    setPhase("RECORDING");
  }, [current, stream, s, push]);

  const stopRecorder = (): Promise<Blob | null> => new Promise((resolve) => {
    try { recogRef.current?.stop(); } catch { /* */ }
    const rec = recRef.current;
    if (!rec || rec.state === "inactive") { resolve(chunks.current.length ? new Blob(chunks.current, { type: rec?.mimeType || "audio/webm" }) : null); return; }
    rec.onstop = () => resolve(chunks.current.length ? new Blob(chunks.current, { type: rec.mimeType || "audio/webm" }) : null);
    rec.stop();
  });

  const submit = useCallback(async (opts: { timedOut?: boolean; skipped?: boolean }) => {
    if (!current || finishingRef.current) return;
    finishingRef.current = true;
    clearTimer();
    setPhase("SUBMITTED");
    const blob = opts.skipped ? null : await stopRecorder();
    recRef.current = null;
    const dur = (Date.now() - recStart.current) / 1000;
    const f = new FormData();
    if (typed.trim() && !opts.skipped) f.append("typed_text", typed.trim());
    f.append("duration_seconds", String(Math.round(dur)));
    f.append("timed_out", String(!!opts.timedOut));
    f.append("skipped", String(!!opts.skipped));
    if (blob && blob.size > 500 && !typed.trim()) f.append("audio", blob, `answer.${(blob.type.split("/")[1] || "webm").split(";")[0]}`);
    try {
      const r = await api.form(`/api/interviews/${sessionId}/questions/${current.id}/answer`, f);
      setS(r.session);
      const q = r.session.questions.find((x: any) => x.id === current.id);
      const a = q?.answer;
      if (opts.skipped || a?.status === "evaluated") setPhase("NEXT_QUESTION");
      else if (a?.status === "transcribing") setPhase("TRANSCRIBING");
      else { setTranscript(a?.transcript_final || ""); setPhase("REVIEW_TRANSCRIPT"); }
      if (opts.timedOut) push("info", "Time is up — your answer so far was saved and marked as timed out.");
    } catch (e: any) {
      push("bad", e.message);
      finishingRef.current = false;
      setPhase("RECORDING");
    }
  }, [current, typed, sessionId, push]);

  useEffect(() => {
    if (phase !== "PREPARATION" && phase !== "RECORDING") { clearTimer(); return; }
    clearTimer();
    timerRef.current = setInterval(() => setLeft((x) => x - 1), 1000);
    return clearTimer;
  }, [phase, qid]);
  useEffect(() => {
    if (left > 0) return;
    if (phase === "PREPARATION") beginRecording();
    else if (phase === "RECORDING") submit({ timedOut: true });
  }, [left]); // eslint-disable-line

  const evaluate = async () => {
    const a = current?.answer;
    if (!a) return;
    const ok = await run("eval", async () => {
      if (transcript !== (a.transcript_final || "")) await api.patch(`/api/interviews/answers/${a.id}/transcript`, { text: transcript });
      await api.post(`/api/interviews/answers/${a.id}/evaluate`);
      return true;
    });
    if (ok) setPhase("EVALUATING");
  };

  const next = async () => {
    await run("next", () => api.post(`/api/interviews/${sessionId}/advance`));
    const d = await load();
    const q = d?.questions.find((x: any) => !x.answer);
    if (!q) { setPhase("COMPLETE"); return; }
    setQid(q.id); setTyped(""); setTranscript(""); setCaption(""); setPhase("READY");
  };

  const complete = async () => {
    stopMedia();
    const r = await run("complete", () => api.post(`/api/interviews/${sessionId}/complete`), "Interview completed — report generated");
    if (r) router.push(`/interview/${sessionId}/report`);
  };

  if (err) return <ErrorBox error={err} onRetry={load} />;
  if (!s) return <Loading rows={8} />;

  const tr = caps?.transcription;
  const qIndex = current ? questions.findIndex((q) => q.id === current.id) : -1;
  const answeredCount = questions.filter((q) => q.answer && q.answer.status === "evaluated").length;

  // --------------------------------------------------------------- consent & device check
  if (s.status === "planned") {
    return (
      <div className="room">
        <Card title="Before you start">
          <div className="stack">
            <p className="muted">This interview has <b>{questions.length} questions</b> generated for <b>{s.job?.title || "your code"}</b> ({s.plan_generator}). Each has a preparation timer and an answer timer that really controls recording. You can answer by voice, by typing, or both.</p>
            <div className="grid g2">
              <Switch checked={wantCam} onChange={setWantCam} label="Use camera (preview only)" help="Video never leaves your browser and is not analysed." />
              <Switch checked={wantMic} onChange={setWantMic} label="Use microphone" help="Records answer audio for transcription." />
            </div>
            <Switch checked={uploadConsent} onChange={setUploadConsent} disabled={!wantMic}
              label="I consent to uploading my answer audio for transcription"
              help={tr?.third_party_upload ? `Audio is sent to a third-party service (${tr?.base_url}).` : tr?.available ? "Transcribed locally on this machine with Whisper; audio is not sent to third parties." : "No transcription provider available — you will type or edit your answers."} />
            <Switch checked={retain} onChange={setRetain} label="Keep recordings after transcription" help="Off by default: audio is deleted once transcribed. You can delete recordings at any time." />
            {mediaErr && <Alert tone="warn">{mediaErr}</Alert>}
            <div className="row wrap">
              <button className="btn" onClick={enableMedia} disabled={!wantCam && !wantMic}><Camera size={15} />{stream ? "Re-check devices" : "Enable camera & microphone"}</button>
              <button className="btn primary" disabled={!!busy} onClick={start}>{busy === "start" && <Spinner />}<Play size={15} />Start interview{stream ? "" : " (typed answers)"}</button>
            </div>
            <div className="faint xs">The camera and microphone are activated only after you press the button and grant browser permission. No emotion, honesty or personality inference is performed.</div>
          </div>
        </Card>
        <div className="stack">
          <div className="video-box">{stream?.getVideoTracks().length ? <video ref={videoRef} autoPlay muted playsInline /> : <div className="col" style={{ alignItems: "center" }}><CameraOff size={28} /><span className="small">Camera off</span></div>}
            <div className="overlay-badges">{stream?.getAudioTracks().length ? <span className="rec-pill"><Mic size={12} />mic ready</span> : <span className="rec-pill"><MicOff size={12} />no mic</span>}</div></div>
          <Card title="Capabilities"><div className="small col" style={{ gap: 4 }}>
            <div>Transcription: {tr?.available ? `${tr.provider} (${tr.model})` : `unavailable — ${tr?.missing?.join(", ") || "not configured"}`}</div>
            <div>Evaluation: {caps?.llm?.configured ? `LLM ${caps.llm.model}` : "heuristic keyword coverage (no LLM configured)"}</div>
            <div>Recording format: {pickMime() || "MediaRecorder unsupported — typed answers only"}</div>
          </div></Card>
        </div>
      </div>
    );
  }

  if (phase === "COMPLETE" || s.status === "completed") {
    return (
      <Card>
        <div className="empty"><div className="icon"><CheckCircle2 size={20} /></div><h3>All questions answered</h3>
          <p>{answeredCount} answer(s) evaluated individually.</p>
          <div className="row" style={{ justifyContent: "center" }}>
            {s.status !== "completed" ? <button className="btn primary" onClick={complete} disabled={!!busy}>{busy && <Spinner />}Finish & generate report</button> : <Link className="btn primary" href={`/interview/${sessionId}/report`}>View report</Link>}
          </div></div>
      </Card>
    );
  }

  if (!current) return <Loading />;
  const a = current.answer;
  const low = (phase === "RECORDING" && left <= 15) || (phase === "PREPARATION" && left <= 5);
  return (
    <div className="room">
      <div className="stack">
        <Card>
          <div className="row between wrap">
            <div className="row wrap"><span className="badge accent">Q{qIndex + 1}/{questions.length}</span><span className="badge sq">{current.qtype.replaceAll("_", " ")}</span><span className="badge sq">{current.difficulty}</span>
              {current.parent_question_id && <span className="badge pink">follow-up based on your answer</span>}</div>
            <div className="state-steps" aria-label="Interview state">{PHASES.slice(0, 8).map((p) => <span key={p} className={p === phase ? "on" : PHASES.indexOf(p) < PHASES.indexOf(phase) ? "done" : ""}>{p.replace("_", " ")}</span>)}</div>
          </div>
          <div className="question-box mt16">{current.prompt}</div>
          <div className="row wrap mt12 xs faint">
            Source: {current.provenance?.kind}
            {current.provenance?.quote && <span>· JD: “{String(current.provenance.quote).slice(0, 120)}…”</span>}
            {current.provenance?.permalink && <Ext href={current.provenance.permalink} className="xs">· cited code</Ext>}
          </div>
          {current.code_context?.snippet && <div className="mt12"><CodeView code={current.code_context.snippet} startLine={current.code_context.start_line}
            header={<><span className="mono">{current.code_context.repo}:{current.code_context.path} @ {String(current.code_context.commit_sha).slice(0, 7)}</span><Ext href={current.code_context.permalink}>GitHub</Ext></>} /></div>}
          {current.challenge?.diff && <div className="mt12"><Alert tone="warn">{current.challenge.label}</Alert><div className="mt8"><DiffView diff={current.challenge.diff} /></div></div>}
        </Card>

        {(phase === "READY" || phase === "PREPARATION" || phase === "RECORDING" || phase === "SUBMITTED") && (
          <Card>
            <div className="row between wrap">
              <div>
                <div className="faint small">{phase === "PREPARATION" ? "Preparation time" : phase === "RECORDING" ? "Answer time remaining" : phase === "READY" ? `Prep ${fmt(current.prep_seconds)} · Answer ${fmt(current.answer_seconds)}` : "Saving…"}</div>
                {(phase === "PREPARATION" || phase === "RECORDING") && <div className={`timer ${low ? "low" : ""}`} role="timer" aria-live="polite">{fmt(left)}</div>}
              </div>
              <div className="row wrap">
                {phase === "READY" && <button className="btn primary lg" onClick={beginPrep}><Play size={16} />Start preparation</button>}
                {phase === "PREPARATION" && <button className="btn primary lg" onClick={beginRecording}><Mic size={16} />Start answering now</button>}
                {phase === "RECORDING" && <button className="btn primary lg" onClick={() => submit({})}><Square size={15} />Submit answer</button>}
                {(phase === "READY" || phase === "PREPARATION" || phase === "RECORDING") && <button className="btn" onClick={() => submit({ skipped: true })}><SkipForward size={15} />Skip</button>}
                {phase === "SUBMITTED" && <Spinner label="Uploading answer…" />}
              </div>
            </div>
            {(phase === "RECORDING" || phase === "PREPARATION") && (
              <div className="field mt12">
                <label className="row"><Keyboard size={13} />{recRef.current ? "Typed answer (optional — leave empty to use your recording)" : "Type your answer"}</label>
                <textarea className="textarea" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={phase === "PREPARATION" ? "Notes / outline (submitted only if you keep it)" : "Your answer…"} aria-label="Typed answer" />
              </div>
            )}
          </Card>
        )}

        {phase === "TRANSCRIBING" && <Card><Spinner label={`Transcribing your recording (${tr?.provider || "provider"})…`} /><div className="faint xs mt8">The browser caption below is not used; the authoritative transcript comes from the recorded audio.</div></Card>}

        {phase === "REVIEW_TRANSCRIPT" && a && (
          <Card title="Review your transcript" actions={<span className="faint xs">{a.answer_source === "audio" ? `source: ${a.transcription_provider || "—"}` : "typed answer"}{a.timed_out ? " · timed out" : ""}</span>}>
            {a.transcription_error && <Alert tone="warn">{a.transcription_error} You can type or correct the text below.</Alert>}
            <textarea className="textarea mt8" style={{ minHeight: 160 }} value={transcript} onChange={(e) => setTranscript(e.target.value)} aria-label="Transcript" />
            <div className="row mt12"><button className="btn primary" disabled={!!busy} onClick={evaluate}>{busy === "eval" && <Spinner />}Evaluate this answer</button>
              <span className="faint xs">Edits are saved before evaluation. Nothing is invented for missing audio.</span></div>
          </Card>
        )}

        {phase === "EVALUATING" && <Card><Spinner label={`Evaluating against the rubric (${caps?.llm?.configured ? caps.llm.model : "heuristic"})…`} /></Card>}

        {phase === "NEXT_QUESTION" && a && (
          <>
            {a.evaluation ? <Feedback answer={a} question={current} /> : <Card><div className="muted">Question skipped.</div></Card>}
            <div className="row"><button className="btn primary lg" onClick={next} disabled={!!busy}>{busy === "next" && <Spinner />}Next question<SkipForward size={15} /></button>
              <button className="btn" onClick={complete}>Finish interview now</button></div>
          </>
        )}
      </div>

      <div className="stack">
        <div className="video-box">
          {stream?.getVideoTracks().length ? <video ref={videoRef} autoPlay muted playsInline /> : <div className="col" style={{ alignItems: "center" }}><Video size={28} /><span className="small">Camera off — audio/typed mode</span></div>}
          <div className="overlay-badges">
            {phase === "RECORDING" && recRef.current ? <span className="rec-pill"><span className="dot rec" />REC {fmt(left)}</span> : null}
            {stream?.getAudioTracks().length ? <span className="rec-pill"><Mic size={12} />{s.consent?.audio_upload ? "mic on" : "mic (not uploaded)"}</span> : <span className="rec-pill"><MicOff size={12} />mic off</span>}
          </div>
          {phase === "RECORDING" && caption && <div className="caption" aria-live="polite">{caption}</div>}
        </div>
        <Card title="Questions">
          <div className="qnav">{questions.map((q, i) => <button key={q.id} className={`${q.id === current.id ? "cur" : ""} ${q.answer?.status === "evaluated" ? (q.status === "skipped" ? "skip" : "done") : ""}`} title={q.qtype} onClick={() => { if (!["RECORDING", "PREPARATION", "SUBMITTED"].includes(phase)) setQid(q.id); }}>{i + 1}</button>)}</div>
          <div className="faint xs mt8">{answeredCount}/{questions.length} evaluated · plan v{s.plan_version} frozen {s.plan_frozen_at ? "at start" : ""}</div>
          <div className="hr" />
          <div className="row wrap">
            {stream ? <button className="btn sm" onClick={stopMedia}><Pause size={13} />Turn off devices</button> : <button className="btn sm" onClick={enableMedia}><Camera size={13} />Enable devices</button>}
            <button className="btn sm danger" onClick={() => run("dm", () => api.del(`/api/interviews/${sessionId}/media`), (r: any) => `${r.deleted} recording(s) deleted`)}>Delete recordings</button>
          </div>
          {mediaErr && <div className="mt8"><Alert tone="warn">{mediaErr}</Alert></div>}
        </Card>
        <Card title="Rubric for this question">
          <ul className="small muted" style={{ paddingLeft: 18, margin: 0 }}>{(current.rubric?.key_points || []).map((k: string) => <li key={k}>{k}</li>)}</ul>
          <div className="faint xs mt8">Scored 1–5 on technical correctness, depth, structure, relevance and communication.</div>
        </Card>
      </div>
    </div>
  );
}
