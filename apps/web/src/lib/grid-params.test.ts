import { describe, expect, it } from "vitest";

import {
  EMPTY_GRID_QUERY,
  GRID_PAGE_SIZE,
  gridRequestUrl,
  mergeGridParams,
  parseGridParams,
} from "./grid-params";

describe("grid URL state", () => {
  it("parses only an allowed complete server sort and preserves an opaque cursor", () => {
    expect(
      parseGridParams(
        "?filter=%20plant%20&sort=name&direction=desc&cursor=b2Zmc2V0OjUw",
        ["name", "status"],
      ),
    ).toEqual({
      cursor: "b2Zmc2V0OjUw",
      filter: "plant",
      sort: { direction: "desc", id: "name" },
    });

    expect(parseGridParams("?sort=internal&direction=asc", ["name"])).toEqual(
      EMPTY_GRID_QUERY,
    );
    expect(parseGridParams("?sort=name", ["name"]).sort).toBeNull();
  });

  it("writes filter, sort, cursor, and the fixed page size without deleting unrelated state", () => {
    const params = mergeGridParams("?fixture=large&stale=yes", {
      cursor: "next-page",
      filter: "Cooling",
      sort: { direction: "asc", id: "reference" },
    });

    expect(Object.fromEntries(params)).toEqual({
      cursor: "next-page",
      direction: "asc",
      filter: "Cooling",
      fixture: "large",
      page_size: String(GRID_PAGE_SIZE),
      sort: "reference",
      stale: "yes",
    });
  });

  it("builds a server request that never asks for an unpaginated result", () => {
    const previousWindow = globalThis.window;
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { location: { origin: "https://xlr8flo.example" } },
    });
    try {
      const url = new URL(gridRequestUrl("/api/projects", EMPTY_GRID_QUERY));
      expect(url.origin).toBe("https://xlr8flo.example");
      expect(url.searchParams.get("page_size")).toBe("50");
      expect(url.searchParams.has("filter")).toBe(false);
    } finally {
      Object.defineProperty(globalThis, "window", { configurable: true, value: previousWindow });
    }
  });
});
