// The QR code of the Steam Web API key page is a constant (decky/tools/gen_qr.py). A decoder is not part of the tests; this checks that
// the constant is a well-formed version 3 QR code (finder, timing and dark-module patterns, a valid format-information word) of the right size.
// It was decoded with OpenCV's QRCodeDetector when it was generated: it returned the URL.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ApiKeyQr } from "../src/components/QrCode";
import { API_KEY_QR, API_KEY_URL } from "../src/components/qrData";

const dark = (r: number, c: number) => API_KEY_QR[r][c] === "#";
const N = API_KEY_QR.length;

describe("API key QR data", () => {
  it("is the key page and a 29 x 29 square of # and .", () => {
    expect(API_KEY_URL).toBe("https://steamcommunity.com/dev/apikey");
    expect(N).toBe(29);   // version 3
    for (const row of API_KEY_QR) expect(row).toMatch(/^[#.]{29}$/);
  });

  it("has the three finder patterns with their white separators", () => {
    for (const [r0, c0] of [[0, 0], [0, N - 7], [N - 7, 0]]) {
      for (let r = 0; r < 7; r++) for (let c = 0; c < 7; c++) {
        const ring = Math.max(Math.abs(r - 3), Math.abs(c - 3));
        expect(dark(r0 + r, c0 + c)).toBe(ring !== 2);   // dark rim, light ring, dark 3x3 centre
      }
    }
    for (let i = 0; i < 8; i++) {
      expect(dark(7, i)).toBe(false);
      expect(dark(i, 7)).toBe(false);
      expect(dark(7, N - 1 - i)).toBe(false);
      expect(dark(i, N - 8)).toBe(false);
      expect(dark(N - 8, i)).toBe(false);
      expect(dark(N - 1 - i, 7)).toBe(false);
    }
  });

  it("has the alternating timing patterns, the dark module and one alignment pattern", () => {
    for (let i = 8; i < N - 8; i++) {
      expect(dark(6, i)).toBe(i % 2 === 0);
      expect(dark(i, 6)).toBe(i % 2 === 0);
    }
    expect(dark(4 * 3 + 9, 8)).toBe(true);
    for (let r = -2; r <= 2; r++) for (let c = -2; c <= 2; c++) expect(dark(22 + r, 22 + c)).toBe(Math.max(Math.abs(r), Math.abs(c)) !== 1);   // version 3: at (22, 22)
  });

  it("carries a valid format word: error level M, the same twice", () => {
    // the 15 format bits, read around the top-left finder (listed LSB first) and again along the other two finders (MSB first)
    const a = [...[0, 1, 2, 3, 4, 5].map((r) => dark(r, 8)), dark(7, 8), dark(8, 8), dark(8, 7), ...[5, 4, 3, 2, 1, 0].map((c) => dark(8, c))];
    const b = [...Array.from({ length: 7 }, (_, i) => dark(N - 1 - i, 8)), ...Array.from({ length: 8 }, (_, i) => dark(8, N - 8 + i))];
    const toNum = (bits: boolean[]) => bits.reduce((n, x) => n * 2 + (x ? 1 : 0), 0);
    const word = toNum(a.slice().reverse());
    const word2 = toNum(b);
    const MASK = 0b101010000010010;
    const valid = new Map<number, number>();   // data (5 bits) -> format word, built from the BCH(15,5) generator
    for (let data = 0; data < 32; data++) {
      let rem = data << 10;
      for (let i = 14; i >= 10; i--) if (rem & (1 << i)) rem ^= 0b10100110111 << (i - 10);
      valid.set(((data << 10) | rem) ^ MASK, data);
    }
    expect(valid.has(word)).toBe(true);
    expect(valid.get(word)! >> 3).toBe(0b00);   // error correction level M
    expect(word2).toBe(word);
  });

  it("is neither empty nor solid", () => {
    const darkCount = API_KEY_QR.join("").split("#").length - 1;
    expect(darkCount).toBeGreaterThan(N * N * 0.3);
    expect(darkCount).toBeLessThan(N * N * 0.7);
  });
});

describe("ApiKeyQr", () => {
  it("is an SVG with a four-module quiet zone, one square per dark module", () => {
    const { container } = render(<ApiKeyQr size={200} />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("viewBox", `0 0 ${N + 8} ${N + 8}`);
    expect(svg).toHaveAttribute("width", "200");
    const d = svg.querySelector("path")!.getAttribute("d")!;
    expect(d.split("M").length - 1).toBe(API_KEY_QR.join("").split("#").length - 1);
    expect(d.startsWith("M4 4h1v1h-1z")).toBe(true);   // the top-left module, shifted by the quiet zone
  });
});
