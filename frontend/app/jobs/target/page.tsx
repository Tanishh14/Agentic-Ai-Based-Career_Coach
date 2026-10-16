"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useAppState } from "@/lib/app";
import { Empty, Loading, PageHead } from "@/components/ui";

export default function TargetJob() {
  const { trackedJob, status } = useAppState();
  const router = useRouter();
  useEffect(() => { if (trackedJob) router.replace(`/jobs/${trackedJob.id}`); }, [trackedJob, router]);
  if (!status) return <Loading />;
  if (trackedJob) return <Loading />;
  return (
    <>
      <PageHead title="Job details & requirements" />
      <Empty title="No target job selected" action={<Link href="/jobs" className="btn primary">Add or find a job</Link>}>Choose a real posting to see its verified requirements.</Empty>
    </>
  );
}
