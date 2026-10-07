import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { STATUS } from "../../../components/status/map";

const source = readFileSync(new URL("./$id.tsx", import.meta.url), "utf8");

describe("effective-access explorer contracts", () => {
  it("keeps the required shared DataGrid and StatusPill primitives present by name", () => {
    expect(source, "MISSING shared DataGrid import").toMatch(
      /import DataGrid from ["'][^"']*components\/grid\/DataGrid["']/,
    );
    expect(source, "MISSING shared StatusPill import").toMatch(
      /import StatusPill from ["'][^"']*components\/status\/StatusPill["']/,
    );
    expect(source).toContain('<DataGrid\n');
    expect(source).toContain('<StatusPill docType="user"');
  });

  it("fails as MISSING if either user lifecycle status leaves the closed map", () => {
    expect(STATUS.user.active, "MISSING user.active status").toBe("success");
    expect(STATUS.user.deactivated, "MISSING user.deactivated status").toBe("neutral");
  });
});
