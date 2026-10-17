"use client";
import { useParams } from "next/navigation";
import InterviewRoom from "@/components/InterviewRoom";
import { PageHead } from "@/components/ui";

export default function Room() {
  const { id } = useParams<{ id: string }>();
  return (
    <>
      <PageHead title="Interview room" subtitle="One question at a time. Timers control recording; you can always type instead of speaking." />
      <InterviewRoom sessionId={id} />
    </>
  );
}
