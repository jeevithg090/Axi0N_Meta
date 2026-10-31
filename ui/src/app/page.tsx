import Link from "next/link";

export default function Home() {
  return (
    <section className="home">
      <h1>Describe it. Plan it. Prove it. Trust it.</h1>
      <p className="muted">
        Every alert is verified, every record is hash-chained, and the badge above always says
        whether you are watching a LIVE run or a recorded REPLAY.
      </p>
      <ul className="home-links">
        <li>
          <Link href="/monitor">Live monitor</Link> rule cards, verifier reasoning, BLIND cameras
        </li>
        <li>
          <Link href="/monitor?demo=1">Recorded UC1 demo</Link> (REPLAY, no server needed)
        </li>
      </ul>
    </section>
  );
}
