import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { Po } from "./api";
import { PoStepper } from "./PoCards";

const steps = (status: string) => [...renderToStaticMarkup(<PoStepper po={{ status } as Po} />).matchAll(/<li[^>]*>(.*?)<\/li>/g)].map(m => m[1]!);
const stateOf = (li: string) => li.match(/req-sub">([^<]*)</)![1]!;

describe("PoStepper", () => {
  it("draws a closed PO's receiving steps as not tracked, never done, and Closed done", () => {
    const li = steps("closed");
    expect(li.slice(4, 7).map(stateOf)).toEqual(["Not tracked yet", "Not tracked yet", "Not tracked yet"]);
    expect(stateOf(li[7]!)).toBe("Done");
    expect(li.slice(0, 4).map(stateOf)).toEqual(["Done", "Done", "Done", "Done"]);
  });
  it("leaves an issued PO's receiving steps as future", () => {
    const li = steps("issued");
    expect(stateOf(li[3]!)).toBe("Current");
    expect(li.slice(4, 7).map(stateOf)).toEqual(["Future", "Future", "Future"]);
  });
});
