import { describe, expect, it } from "vitest";
import { docWords } from "../api";

describe("docWords", () => {
  it("looks ahead in words, one tone per standing", () => {
    expect(docWords("expiring", 12, 1)).toEqual({ text: "1 Expiring in 12 Days", tone: "warning" });
    expect(docWords("expiring", 1)).toEqual({ text: "Expiring in 1 Day", tone: "warning" });
    expect(docWords("expiring", 0).text).toBe("Expiring Today");
    expect(docWords("expired", null, 2)).toEqual({ text: "2 Expired", tone: "danger" });
    expect(docWords("missing").text).toBe("Missing");
  });
  it("gives every standing different words, so greyscale still tells them apart", () => {
    const states = ["expiring", "expired", "missing", "rejected", "unverified", "ok"] as const;
    expect(new Set(states.map(s => docWords(s, 3).text)).size).toBe(states.length);
  });
});
