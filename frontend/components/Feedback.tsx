"use client";
import { Card } from "./ui";

const DIM_LABEL: Record<string, string> = {
  technical_correctness: "Technical correctness", depth: "Depth", structure: "Structure", relevance: "Relevance & examples", communication: "Communication",
};

export default function Feedback({ answer, question }: { answer: any; question?: any }) {
  const ev = answer.evaluation;
  if (!ev) return null;
  const dims = Object.entries(ev.scores || {}).filter(([k]) => k in DIM_LABEL);
  const dm = ev.delivery_metrics || {};
  return (
    <Card title="Feedback" actions={<span className="faint xs">{ev.provider === "heuristic" ? "heuristic (no LLM)" : `${ev.provider} · ${ev.model}`} · {ev.evaluator_version}</span>}>
      <div className="row start wrap" style={{ gap: 20 }}>
        <div className="score-ring" style={{ ["--p" as any]: ev.overall }}><b>{Math.round(ev.overall)}</b></div>
        <div className="col grow" style={{ gap: 6, minWidth: 260 }}>
          {dims.map(([k, v]) => (
            <div key={k} className="dim-bar"><span className="muted">{DIM_LABEL[k]}</span>
              {typeof v === "number" ? <div className="progress"><div style={{ width: `${(Number(v) / 5) * 100}%` }} /></div> : <span className="faint xs">not assessed</span>}
              <b>{typeof v === "number" ? `${v}/5` : "—"}</b></div>
          ))}
          {ev.scores?.method && <div className="faint xs">{ev.scores.method}</div>}
        </div>
      </div>
      <div className="grid g2 mt16">
        <div><h4>What went well</h4><ul className="small" style={{ paddingLeft: 18 }}>{(ev.strengths || []).length ? ev.strengths.map((x: string, i: number) => <li key={i}>{x}</li>) : <li className="faint">—</li>}</ul></div>
        <div><h4>Missing or incorrect</h4><ul className="small" style={{ paddingLeft: 18 }}>{(ev.missing_points || []).length ? ev.missing_points.map((x: string, i: number) => <li key={i}>{x}</li>) : <li className="faint">—</li>}</ul></div>
      </div>
      <div className="mt12"><h4>Practise next</h4><div className="quote mt8">{ev.correction}</div></div>
      <details className="mt12"><summary className="small bold">Model answer outline</summary><pre className="plain mt8">{ev.model_answer}</pre></details>
      <details className="mt8"><summary className="small bold">Your transcript</summary><pre className="plain mt8">{answer.transcript_final || "(empty)"}</pre>
        <div className="faint xs mt4">{answer.answer_source === "audio" ? `Transcribed by ${answer.transcription_provider}${answer.transcript_edited ? " · edited by you" : ""}` : "Typed"}</div></details>
      <div className="row wrap mt12 xs faint">
        <span>{dm.words ?? 0} words</span>
        {dm.words_per_minute != null && <span>· ~{dm.words_per_minute} wpm</span>}
        {dm.filler_count != null && <span>· {dm.filler_count} filler words</span>}
        <span>· {dm.label}</span>
      </div>
      {ev.followup_suggestion && <div className="mt8 small"><span className="badge pink">Follow-up added</span> {ev.followup_suggestion}</div>}
      {question?.qtype === "debugging_challenge" && <div className="faint xs mt8">The bug was hypothetical and was never executed or committed.</div>}
    </Card>
  );
}
