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
          <Link href="/demo?demo=1">Golden run</Link> a recorded live run, replayed step by step
          (REPLAY, no server or model needed), with the evidence check in your browser
        </li>
        <li>
          <Link href="/monitor?demo=1">Golden run on the live monitor</Link> the same recording as
          the monitor shows it
        </li>
      </ul>
    </section>
  );
}
