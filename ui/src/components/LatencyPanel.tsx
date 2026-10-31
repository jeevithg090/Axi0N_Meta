"use client";

import type { RelayState } from "@/lib/relay";

/** Measured latency per model in the current segment (mean of the calls seen). */
export function LatencyPanel({ relay }: { relay: RelayState }) {
  if (relay.models.length === 0) return null;
  return (
    <table className="latency">
      <caption>Latency · {relay.segment}</caption>
      <thead>
        <tr>
          <th scope="col">Model</th>
          <th scope="col">Calls</th>
          <th scope="col">Mean ms</th>
        </tr>
      </thead>
      <tbody>
        {relay.models.map((m) => (
          <tr key={m.model}>
            <td>{m.model}</td>
            <td>{m.calls}</td>
            <td>{Math.round(m.totalLatencyMs / m.calls)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
