"use client";

import { DeclinedCardTile, RuleCardTile } from "@/components/ops/GalleryCards";
import { OwnerOnly } from "@/components/OwnerOnly";
import { useStream } from "@/lib/stream";
import { usePlans } from "@/lib/usePlans";

export default function CardGalleryPage() {
  const state = useStream();
  const { plans, error, forbidden } = usePlans();
  const entries = Object.values(plans);
  return (
    <section aria-label="Rule cards">
      <h2>Rule cards</h2>
      {forbidden ? (
        <OwnerOnly what="The rule cards of this site" checking={false} />
      ) : (
        error && <p className="muted">{error}</p>
      )}
      {entries.length === 0 && !error && <p className="muted">No approved plan yet.</p>}
      {entries.map(({ latest }) => (
        <div key={latest.plan_id}>
          <h3 className="muted">
            {latest.site_name} · v{latest.version}
          </h3>
          <div className="gallery">
            {latest.rules.map((rule) => (
              <RuleCardTile key={rule.id} rule={rule} ci={state.ci[rule.id]} />
            ))}
            {latest.feasibility
              .filter((v) => v.verdict === "declined")
              .map((v, i) => (
                <DeclinedCardTile key={`declined-${i}`} verdict={v} />
              ))}
          </div>
        </div>
      ))}
    </section>
  );
}
