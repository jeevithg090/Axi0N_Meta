import { describe, expect, it } from "vitest";

import { CLIENT_IP_HEADER, PROXY_SECRET_HEADER, proxyHeaders } from "./proxy";

describe("UI proxy headers", () => {
  it("forwards the browser's address with the secret", () => {
    const out = proxyHeaders(
      new Headers({ "x-real-ip": "198.51.100.7", "x-forwarded-for": "198.51.100.7, 10.0.0.1" }),
      "k".repeat(32),
    );
    expect(out.get(CLIENT_IP_HEADER)).toBe("198.51.100.7");
    expect(out.get(PROXY_SECRET_HEADER)).toBe("k".repeat(32));
  });

  it("falls back to the first x-forwarded-for entry", () => {
    const out = proxyHeaders(new Headers({ "x-forwarded-for": " 203.0.113.9 , 10.0.0.1" }), "k");
    expect(out.get(CLIENT_IP_HEADER)).toBe("203.0.113.9");
  });

  it("never passes on a browser's own copies, and adds nothing without a secret", () => {
    const spoofed = new Headers({
      [CLIENT_IP_HEADER]: "1.2.3.4",
      [PROXY_SECRET_HEADER]: "guess",
      "x-real-ip": "198.51.100.7",
      cookie: "a=b",
    });
    const out = proxyHeaders(spoofed, undefined);
    expect(out.get(CLIENT_IP_HEADER)).toBeNull();
    expect(out.get(PROXY_SECRET_HEADER)).toBeNull();
    expect(out.get("cookie")).toBe("a=b");
    expect(proxyHeaders(new Headers(), "k").get(PROXY_SECRET_HEADER)).toBeNull();
  });
});
