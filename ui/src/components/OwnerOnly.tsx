import Link from "next/link";

/** What a visitor sees instead of an owner-only control (the API refuses it either way). */
export function OwnerOnly({ what, checking }: { what: string; checking: boolean }) {
  if (checking) return <p className="muted">Checking who you are…</p>;
  return (
    <p className="muted" data-testid="owner-only">
      {what} is for the site owner. <Link href="/login">Log in</Link> to continue.
    </p>
  );
}
