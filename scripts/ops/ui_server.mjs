// Serves the production Next.js build (run from ui/ after `npm run build`) on the fallback UI
// pod, like `next start`, with one difference: pages and data are sent `Cache-Control: no-store`.
// The RunPod proxy is a CDN that honours Next's `s-maxage=31536000` on prerendered pages, so
// without this a redeploy keeps serving old HTML that points at JavaScript the new build no longer
// has. Hashed files under /_next/static/ keep their long cache. Used by ship_ui_runpod.sh.
import { createServer } from "node:http";

import next from "next";

const port = Number(process.env.PORT ?? 3000);
const host = process.env.HOSTNAME ?? "0.0.0.0";
const app = next({ dev: false, dir: process.cwd(), hostname: host, port });
const handle = app.getRequestHandler();
await app.prepare();

function noStore(res) {
  const setHeader = res.setHeader.bind(res);
  res.setHeader = (name, value) =>
    setHeader(name, String(name).toLowerCase() === "cache-control" ? "no-store" : value);
  const writeHead = res.writeHead.bind(res);
  res.writeHead = (status, ...rest) => {
    res.setHeader("Cache-Control", "no-store");
    for (const arg of rest) {
      if (arg && typeof arg === "object" && !Array.isArray(arg)) {
        for (const key of Object.keys(arg)) {
          if (key.toLowerCase() === "cache-control") arg[key] = "no-store";
        }
      }
    }
    return writeHead(status, ...rest);
  };
}

createServer((req, res) => {
  if (!(req.url ?? "").startsWith("/_next/static/")) noStore(res);
  handle(req, res);
}).listen(port, host, () => {
  console.log(`rulecam ui on http://${host}:${port}`);
});
