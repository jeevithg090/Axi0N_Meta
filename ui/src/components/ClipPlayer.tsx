"use client";

import { useState, type CSSProperties, type Ref, type SyntheticEvent } from "react";

/**
 * Clips committed under `ui/public/videos/`. The stock clips the sample boards were written
 * against (`/videos/cam01_dock.mp4` …) are git-ignored and absent from every deploy, so a path not
 * listed here never reaches the network: the camera's committed still is shown instead, and says
 * so. Add a path here when its file is committed.
 */
export const BUNDLED_CLIPS: ReadonlySet<string> = new Set<string>();

/** The committed still for a sample camera's clip (`/videos/cam05_kitchen.mp4` → CAM-05). */
const CLIP_CAMERA: Record<string, string> = {
  "/videos/warehouse_forklift.mp4": "CAM-04",
  "/videos/master_capabilities_demo.mp4": "CAM-04",
  "/videos/kitchen_prep.mp4": "CAM-05",
};

/** The still that stands in for a clip: `public/strips/<CAM>_strip_1.jpg`, or `null`. */
export function stillFor(src: string): string | null {
  const known = CLIP_CAMERA[src];
  if (known) return `/strips/${known}_strip_1.jpg`;
  const match = /\/cam(\d{2})_/.exec(src);
  return match ? `/strips/CAM-${match[1]}_strip_1.jpg` : null;
}

export interface ClipPlayerProps {
  src: string;
  /** A still to show in the clip's place; defaults to the camera's committed still. */
  poster?: string;
  /** Sizing for the video or the still that replaces it. */
  style?: CSSProperties;
  className?: string;
  ref?: Ref<HTMLVideoElement>;
  autoPlay?: boolean;
  loop?: boolean;
  muted?: boolean;
  playsInline?: boolean;
  controls?: boolean;
  onTimeUpdate?: (event: SyntheticEvent<HTMLVideoElement>) => void;
}

/**
 * A camera clip that never shows a black 0:00 player: the video when its file is bundled and
 * loads, otherwise the camera's still with a plain note that the clip is not bundled.
 */
export function ClipPlayer({ src, poster, style, className, ref, ...video }: ClipPlayerProps) {
  const [failed, setFailed] = useState(false);
  const still = poster ?? stillFor(src);

  if (!BUNDLED_CLIPS.has(src) || failed) {
    return (
      <figure className={`clip-still ${className ?? ""}`} style={style} data-testid="clip-still">
        {still ? (
          // eslint-disable-next-line @next/next/no-img-element -- a committed sample still
          <img src={still} alt="" width={640} height={360} />
        ) : null}
        <figcaption>Still frame. The clip is not bundled with this build.</figcaption>
      </figure>
    );
  }
  return (
    <video
      ref={ref}
      src={src}
      poster={still ?? undefined}
      className={className}
      style={style}
      onError={() => setFailed(true)}
      {...video}
    />
  );
}
