"use client";

import type { CameraInfo } from "@/lib/api";
import type { InventoryItem } from "@/lib/plan";

/**
 * One tile per camera, with the concepts the survey found on it. Mask overlays need a frame
 * snapshot route; until it exists the tile lists what SAM saw, with counts and confidence.
 */
export function CameraTiles({
  cameras,
  inventory,
}: {
  cameras: CameraInfo[];
  inventory: InventoryItem[];
}) {
  const ids = [
    ...new Set([...cameras.map((c) => c.camera_id), ...inventory.map((i) => i.camera_id)]),
  ];
  if (ids.length === 0)
    return <p className="muted">No cameras yet: pair a phone or start capture.</p>;
  return (
    <div className="tiles" data-testid="camera-tiles">
      {ids.map((id) => {
        const camera = cameras.find((c) => c.camera_id === id);
        const seen = inventory.filter((i) => i.camera_id === id);
        return (
          <article key={id} className="tile">
            <h3>{camera?.label ?? id}</h3>
            <p className="muted">{camera ? camera.source.replace("_", " ") : "from the survey"}</p>
            <ul>
              {seen.map((i) => (
                <li key={i.concept}>
                  {i.concept} ×{i.count}{" "}
                  <span className="muted">({Math.round(i.mean_confidence * 100)}%)</span>
                </li>
              ))}
            </ul>
          </article>
        );
      })}
    </div>
  );
}
