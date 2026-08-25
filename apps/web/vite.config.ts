import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { Plugin } from "vite";
import { defineConfig } from "vitest/config";

const PROJECT_STATUSES = [
  "draft",
  "approval_pending",
  "active",
  "deferred",
  "completed",
  "abandoned",
] as const;
const OWNERS = ["Avery Chen", "Jordan Singh", "Morgan Lee", "Riley Jones"] as const;
const GRID_PAGE_SIZE = 50;

type FixtureRow = {
  id: string;
  name: string;
  owner: string;
  reference: string;
  status: (typeof PROJECT_STATUSES)[number];
  units: number;
};

function fixtureRow(index: number): FixtureRow {
  const serial = String(index).padStart(5, "0");
  return {
    id: `project-${serial}`,
    name: `Capital project ${serial}`,
    owner: OWNERS[index % OWNERS.length]!,
    reference: `PRJ-${serial}`,
    status: PROJECT_STATUSES[index % PROJECT_STATUSES.length]!,
    units: index * 3,
  };
}

function cursorFor(offset: number): string {
  return Buffer.from(`offset:${offset}`).toString("base64url");
}

function offsetFor(cursor: string | null): number | null {
  if (cursor === null) return 0;
  try {
    const decoded = Buffer.from(cursor, "base64url").toString();
    const match = decoded.match(/^offset:(\d+)$/);
    return match === null ? null : Number(match[1]);
  } catch {
    return null;
  }
}

function gridFixture(): Plugin {
  return {
    configureServer(server) {
      server.middlewares.use("/api/_dev/grid", (request, response) => {
        const url = new URL(request.url ?? "/", "http://grid.local");
        if (url.searchParams.get("page_size") !== String(GRID_PAGE_SIZE)) {
          response.statusCode = 400;
          response.end("page_size must be 50");
          return;
        }
        if (url.searchParams.get("fixture") === "error") {
          response.statusCode = 503;
          response.end("fixture unavailable");
          return;
        }

        const offset = offsetFor(url.searchParams.get("cursor"));
        if (offset === null) {
          response.statusCode = 400;
          response.end("invalid cursor");
          return;
        }
        const filter = url.searchParams.get("filter")?.toLocaleLowerCase() ?? "";
        const fixtureTotal = url.searchParams.get("fixture") === "empty" ? 0 : 10_000;
        const rows = Array.from({ length: fixtureTotal }, (_, itemIndex) => fixtureRow(itemIndex + 1))
          .filter((row) =>
            [row.reference, row.name, row.owner, row.status].some((value) =>
              value.toLocaleLowerCase().includes(filter),
            ),
          );
        const sort = url.searchParams.get("sort");
        const direction = url.searchParams.get("direction") === "desc" ? -1 : 1;
        if (sort !== null && ["reference", "name", "status", "owner", "units"].includes(sort)) {
          rows.sort((left, right) =>
            String(left[sort as keyof FixtureRow]).localeCompare(
              String(right[sort as keyof FixtureRow]),
              undefined,
              { numeric: true },
            ) * direction,
          );
        }

        const boundedOffset = Math.min(offset, Math.max(0, rows.length - 1));
        const pageRows = rows.slice(boundedOffset, boundedOffset + GRID_PAGE_SIZE);
        const lastOffset = Math.max(0, Math.floor(Math.max(0, rows.length - 1) / GRID_PAGE_SIZE) * GRID_PAGE_SIZE);
        response.setHeader("Content-Type", "application/json");
        response.end(
          JSON.stringify({
            lastCursor: rows.length > GRID_PAGE_SIZE ? cursorFor(lastOffset) : null,
            nextCursor:
              boundedOffset + GRID_PAGE_SIZE < rows.length
                ? cursorFor(boundedOffset + GRID_PAGE_SIZE)
                : null,
            previousCursor:
              boundedOffset > 0 ? cursorFor(Math.max(0, boundedOffset - GRID_PAGE_SIZE)) : null,
            rows: pageRows,
            startIndex: boundedOffset,
            total: rows.length,
          }),
        );
      });
    },
    name: "xlr8flo-grid-fixture",
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), gridFixture()],
  test: {
    exclude: ["e2e/**", "tests/a11y/**", "tests/e2e/**", "node_modules/**"],
  },
});
