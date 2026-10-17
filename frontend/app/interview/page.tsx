"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Mic, Play } from "lucide-react";
import { api, Job, timeAgo } from "@/lib/api";
import { useAction, useApi, useAppState } from "@/lib/app";
import { Alert, Card, Empty, Loading, PageHead, Spinner } from "@/components/ui";

export default function InterviewHome() {
  const router = useRouter();
  const { trackedJob } = useAppState();
  const jobs = useApi<Job[]>("/api/jobs?status=analyzed");
  const sessions = useApi<any[]>("/api/interviews?kind=mock");
  const caps = useApi<any>("/api/interviews/capabilities");
  const [job, setJob] = useState("");
  const [n, setN] = useState(7);
  const { busy, run } = useAction();
  useEffect(() => { if (!job && trackedJob) setJob(trackedJob.id); }, [trackedJob, job]);
  return (
    <>
      <PageHead title="Mock interview" subtitle="A professional, timed interview generated from your selected real job, its validated requirements, your current gap matrix, cited repository evidence and supplied claims — not a canned question bank." />
      <div className="grid g-side">
        <Card title="New interview" icon={<Mic size={16} />}>
          {jobs.loading ? <Loading /> : !(jobs.data || []).length ? <Empty title="Analyse a job first" action={<Link className="btn primary" href="/jobs">Add a job</Link>} /> : (
            <div className="stack">
              <div className="field"><label>Job posting</label>
                <select className="select" value={job} onChange={(e) => setJob(e.target.value)}>
                  <option value="">Select…</option>
                  {(jobs.data || []).map((j) => <option key={j.id} value={j.id}>{j.title} {j.company ? `· ${j.company}` : ""}{j.is_tracked ? " (target)" : ""}</option>)}
                </select></div>
              <div className="field"><label>Number of questions: {n}</label><input type="range" min={3} max={12} value={n} onChange={(e) => setN(Number(e.target.value))} /></div>
              <Alert tone={caps.data?.llm?.configured ? "info" : "warn"}>
                {caps.data?.llm?.configured ? <>Questions and feedback by <b>{caps.data.llm.model}</b>; every question is validated against real requirement/evidence IDs.</> :
                  <>No LLM configured: questions are generated from your real JD quotes, gap rows and cited code with templates, and answers get heuristic rubric feedback. <Link href="/settings">Configure an LLM</Link> for full evaluation.</>}
              </Alert>
              <div className="faint small">Speech-to-text: {caps.data?.transcription?.available ? `${caps.data.transcription.provider} (${caps.data.transcription.model})` : "not available — typed answers"}</div>
              <button className="btn primary lg" disabled={!job || !!busy} onClick={() => run("new", async () => { const s = await api.post("/api/interviews", { job_posting_id: job, n_questions: n }); router.push(`/interview/${s.id}`); return s; })}>{busy ? <Spinner /> : <Play size={16} />}Generate interview plan</button>
            </div>
          )}
        </Card>
        <Card title="Your sessions" pad={false}>
          {sessions.loading ? <div className="card-body"><Loading /></div> : !(sessions.data || []).length ? <div className="card-body muted small">No interviews yet.</div> : (
            <div className="table-wrap"><table className="tbl"><tbody>{(sessions.data || []).map((s) => (
              <tr key={s.id} className="clickable" onClick={() => router.push(s.status === "completed" ? `/interview/${s.id}/report` : `/interview/${s.id}`)}>
                <td><div className="bold small">{s.job?.title || "Interview"}</div><div className="xs faint">{timeAgo(s.created_at)} · {s.plan_generator?.split(" ")[0]}</div></td>
                <td><span className={`badge ${s.status === "completed" ? "ok" : s.status === "active" ? "info" : ""}`}>{s.status}</span></td>
                <td className="bold">{s.overall != null ? Math.round(s.overall) : ""}</td>
              </tr>
            ))}</tbody></table></div>
          )}
        </Card>
      </div>
    </>
  );
}
