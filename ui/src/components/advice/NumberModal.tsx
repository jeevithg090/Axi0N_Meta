"use client";

import { useEffect, useRef } from "react";

import { formatNumber, type NumberCheck } from "@/lib/advice";

/** What a clickable number opens: the Tier 1 model's own SQL, its result, and whether it matched. */
export function NumberModal({
  check,
  hidden,
  onClose,
}: {
  check: NumberCheck;
  hidden: boolean;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open && typeof dialog.showModal === "function") dialog.showModal();
  }, []);
  return (
    <dialog ref={ref} className="number-modal" data-testid="number-modal" onClose={onClose} open>
      <h4>{check.metric}</h4>
      <p>
        Catalogue:{" "}
        <strong>{hidden ? "hidden" : formatNumber(check.catalogue_value, check.unit)}</strong>
      </p>
      <p>
        Tier 1 model recomputed:{" "}
        <strong>
          {check.recomputed === null ? "no value" : formatNumber(check.recomputed, check.unit)}
        </strong>{" "}
        {check.ok ? "✓ reproduced" : `✗ ${check.error || "not reproduced"}`}
      </p>
      <p className="muted">The Tier 1 model&apos;s own SQL, run read-only on the process log:</p>
      <pre>{check.sql || "(none)"}</pre>
      <button type="button" onClick={onClose}>
        Close
      </button>
    </dialog>
  );
}
