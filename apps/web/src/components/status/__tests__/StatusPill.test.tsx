import {
  ArrowsClockwise,
  CheckCircle,
  Circle,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { globSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import StatusPill from "../StatusPill";
import { STATUS_LABELS, UNKNOWN_STATUS_LABEL } from "../labels";
import { STATUS, statusSelections, type StatusSelection } from "../map";
import { STATUS_TONES, TONES } from "../tones";

const expectedStatus = {
  project: {
    draft: "neutral",
    approval_pending: "info",
    active: "success",
    deferred: "warning",
    completed: "neutral",
    abandoned: "danger",
  },
  requisition: {
    draft: "neutral",
    approval_pending: "info",
    sourcing_in_progress: "info",
    sourced: "info",
    awarded: "success",
    open_for_purchase: "info",
    completed: "success",
    rejected: "danger",
    withdrawn: "neutral",
    cancelled: "danger",
    expired: "warning",
  },
  purchase_order: {
    draft: "neutral",
    approval_pending: "info",
    approved: "info",
    issued: "success",
    partially_delivered: "info",
    delivered: "success",
    accepted: "success",
    closed: "neutral",
    cancelled: "danger",
    superseded: "warning",
  },
  rfq: {
    draft: "neutral",
    published: "info",
    closed: "info",
    under_evaluation: "info",
    awarded: "success",
    cancelled: "danger",
  },
  award: {
    draft: "neutral",
    approval_pending: "info",
    approved: "success",
    superseded: "warning",
    cancelled: "danger",
  },
  vendor: {
    draft: "neutral",
    pending_approval: "info",
    active: "success",
    suspended: "warning",
    inactive: "neutral",
    rejected: "danger",
  },
  budget: {
    within: "success",
    nearing: "warning",
    exceeded: "danger",
  },
  user: {
    active: "success",
    deactivated: "neutral",
  },
} as const;

const expectedLabels = {
  draft: "Draft",
  approval_pending: "Approval pending",
  active: "Active",
  deferred: "Deferred",
  completed: "Completed",
  abandoned: "Abandoned",
  sourcing_in_progress: "Sourcing in progress",
  sourced: "Sourced",
  awarded: "Awarded",
  open_for_purchase: "Open for purchase",
  cancelled: "Cancelled",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  approved: "Approved",
  issued: "Issued",
  partially_delivered: "Partially delivered",
  delivered: "Delivered",
  accepted: "Accepted",
  closed: "Closed",
  superseded: "Superseded",
  published: "Published",
  under_evaluation: "Under evaluation",
  pending_approval: "Pending approval",
  suspended: "Suspended",
  expired: "Expired",
  inactive: "Inactive",
  within: "Within budget",
  nearing: "Nearing limit",
  exceeded: "Exceeded",
  deactivated: "Deactivated",
} as const;

function renderSelection(selection: StatusSelection): string {
  return renderToStaticMarkup(<StatusPill {...selection} />);
}

function assertClosedProps() {
  // @ts-expect-error Status tone cannot be authored by a caller.
  void <StatusPill color="var(--status-danger)" docType="project" status="draft" />;
  // @ts-expect-error Status icon is derived from the tone.
  void <StatusPill docType="project" icon={Circle} status="draft" />;
  // @ts-expect-error Callers cannot reach the component's tone through a class override.
  void <StatusPill className="override" docType="project" status="draft" />;
}
void assertClosedProps;

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("StatusPill", () => {
  it("contains the complete closed per-document vocabulary", () => {
    expect(STATUS).toEqual(expectedStatus);
    expect(STATUS_LABELS).toEqual(expectedLabels);
  });

  it("maps every semantic tone to its required regular Phosphor icon", () => {
    expect(STATUS_TONES).toEqual(["neutral", "info", "success", "warning", "danger"]);
    expect(Object.fromEntries(STATUS_TONES.map((tone) => [tone, TONES[tone].icon]))).toEqual({
      neutral: Circle,
      info: ArrowsClockwise,
      success: CheckCircle,
      warning: Warning,
      danger: XCircle,
    });
  });

  it("renders tone, one decorative icon, and one translated label for every mapped status", () => {
    const selections = statusSelections();
    expect(selections).toHaveLength(
      Object.values(expectedStatus).reduce((total, statuses) => total + Object.keys(statuses).length, 0),
    );

    for (const selection of selections) {
      const markup = renderSelection(selection);
      const tone = expectedStatus[selection.docType][selection.status as never];
      const label = expectedLabels[selection.status];
      expect(tone, `missing expected ${selection.docType}.${selection.status}`).toBeDefined();
      expect(label, `missing label ${selection.status}`).toBeDefined();
      expect(markup).toContain(`data-tone="${tone}"`);
      expect(markup.match(/<svg\b/g)).toHaveLength(1);
      expect(markup).toContain('aria-hidden="true"');
      expect(markup.match(new RegExp(`>${label}<`, "g"))).toHaveLength(1);
    }
  });

  it("renders an expired requisition with the warning tone and Warning icon, not XCircle", () => {
    const markup = renderSelection({ docType: "requisition", status: "expired" });
    const icon = (node: React.ReactElement) => renderToStaticMarkup(node).replace(/^<svg[^>]*>/, "");

    expect(markup).toContain('data-tone="warning"');
    expect(markup).toContain(icon(<Warning />));
    expect(markup).not.toContain(icon(<XCircle />));
    expect(renderSelection({ docType: "requisition", status: "rejected" })).toContain('data-tone="danger"');
    expect(renderSelection({ docType: "requisition", status: "withdrawn" })).toContain('data-tone="neutral"');
  });

  it("snapshots square status geometry through the dedicated pill tokens", () => {
    const openingTag = renderSelection({ docType: "purchase_order", status: "issued" }).match(
      /^<span[^>]+>/,
    )?.[0];

    expect(openingTag).toMatchInlineSnapshot(
      `"<span class="status-pill" data-doc-type="purchase_order" data-status="issued" data-tone="success" style="background-color:var(--status-success-tint);border-radius:var(--pill-radius);color:var(--status-success);gap:var(--pill-gap);height:var(--pill-h);padding-inline:var(--pill-pad-x)">"`,
    );
  });

  it("throws on an unmapped development status", () => {
    vi.stubEnv("DEV", true);
    const invalid = { docType: "project", status: "not_mapped" } as unknown as StatusSelection;

    expect(() => renderSelection(invalid)).toThrowError("Unmapped project status: not_mapped");
  });

  it("renders the neutral translated unknown pill for an unmapped production status", () => {
    vi.stubEnv("DEV", false);
    const invalid = { docType: "project", status: "not_mapped" } as unknown as StatusSelection;
    const markup = renderSelection(invalid);

    expect(markup).toContain('data-tone="neutral"');
    expect(markup).toContain(`>${UNKNOWN_STATUS_LABEL}<`);
    expect(markup.match(/<svg\b/g)).toHaveLength(1);
    expect(markup).toContain('aria-hidden="true"');
  });

  it("finds no status colour rendering outside the StatusPill package", () => {
    const sourceRoot = fileURLToPath(new URL("../../..", import.meta.url));
    const componentFiles = globSync(["components/**/*.tsx", "routes/**/*.tsx"], {
      cwd: sourceRoot,
    }).filter(
      (path) =>
        // Signal is the one other reader of the tone icons (MASTER 7.14, E11-S11).
        path !== "components/status/StatusPill.tsx" &&
        path !== "components/ui/Signal.tsx" &&
        !path.includes("/__tests__/"),
    );

    expect(componentFiles).toContain("components/shell/AppShell.tsx");
    expect(componentFiles).toContain("routes/__root.tsx");
    const violations = componentFiles.flatMap((path) => {
      const source = readFileSync(`${sourceRoot}/${path}`, "utf8");
      const authorsStatusColour =
        /var\(--status-(?:neutral|info|success|warning|danger)(?:-tint)?\)|(?:bg|text|border|fill|stroke)-status-(?:neutral|info|success|warning|danger)\b|className=["'][^"']*\bstatus-pill\b/;
      const importsToneDefinitions = /from\s+["'][^"']*status\/tones["']/;
      return authorsStatusColour.test(source) || importsToneDefinitions.test(source) ? [path] : [];
    });

    expect(violations, `status colour authored outside StatusPill: ${violations.join(", ")}`).toEqual([]);
  });
});
