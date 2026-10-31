import { describe, expect, it } from "vitest";

import { encodeWav } from "./wav";

const ascii = (bytes: Uint8Array, start: number, end: number): string =>
  String.fromCharCode(...bytes.slice(start, end));

describe("encodeWav", () => {
  it("writes a 16-bit mono PCM WAV the API's sniffing and Voice Transcribe accept", () => {
    const wav = encodeWav(new Float32Array([0, 1, -1, 2, -0.5]), 16_000);
    const view = new DataView(wav.buffer);
    expect(ascii(wav, 0, 4)).toBe("RIFF");
    expect(ascii(wav, 8, 12)).toBe("WAVE");
    expect(ascii(wav, 36, 40)).toBe("data");
    expect(wav.length).toBe(44 + 5 * 2);
    expect(view.getUint32(4, true)).toBe(wav.length - 8);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16_000);
    expect(view.getUint16(34, true)).toBe(16);
    expect([0, 1, 2, 3, 4].map((i) => view.getInt16(44 + i * 2, true))).toEqual([
      0, 32767, -32768, 32767, -16384,
    ]);
  });
});
