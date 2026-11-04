import Link from "next/link";

import { EmptyState, Skeleton } from "./kit";

/** What a visitor sees instead of an owner-only control (the API refuses it either way). */
export function OwnerOnly({ what, checking }: { what: string; checking: boolean }) {
  if (checking) return <Skeleton lines={2} label="Checking who you are" />;
  return (
    <div data-testid="owner-only">
      <EmptyState
        title={`${what} is for the site owner`}
        icon="shield"
        action={
          <Link href="/login" className="btn">
            Log in
          </Link>
        }
      >
        The API refuses it for visitors either way. The golden replay shows the same screens with
        recorded data, no login needed.
      </EmptyState>
    </div>
  );
}
