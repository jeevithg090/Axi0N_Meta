/**
 * The tag on every screen, or part of a screen, that shows hand-written sample content.
 *
 * Sample content (a scripted walkthrough, built-in example cards, an illustrative Rule CI table)
 * is never a live run or a recorded result, so it is labelled where it is shown, in words a
 * judge or an owner cannot miss. Recorded runs use the REPLAY badge instead, and live data
 * the LIVE badge.
 */
export function SampleDataTag({ what }: { what: string }) {
  return (
    <p className="sample-tag" role="note" data-testid="sample-data-tag">
      <b>SAMPLE DATA</b> · {what} Hand-written for the interface, not a live run or a measured
      result.
    </p>
  );
}
