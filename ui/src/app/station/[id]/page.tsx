"use client";

import { useParams } from "next/navigation";

import { Countdown } from "@/components/ops/Countdown";
import { stationCountdown } from "@/lib/ops";
import { useStream } from "@/lib/stream";

/** The station display (sink tablet): one huge countdown and the coaching line, nothing else. */
export default function StationPage() {
  const { id } = useParams<{ id: string }>();
  const state = useStream();
  const station = state.station[id];
  const latest = state.events.find((v) => v.candidate.camera_id === id)?.candidate;
  const required = Number(latest?.predicate_trace.required_s ?? 0);
  const lastFail =
    latest?.measurement && latest.measurement.unit === "s" && required > 0
      ? { value: latest.measurement.value, required }
      : undefined;
  return (
    <section className="station" aria-label={`Station ${id}`}>
      <Countdown value={stationCountdown(station?.progress, lastFail)} />
      {station?.coach && (
        <p className="station-coach" lang={station.coach.lang ?? undefined}>
          {station.coach.text}
        </p>
      )}
    </section>
  );
}
