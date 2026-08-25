import { describe, expect, it } from "vitest";

import {
  COMMANDS,
  DEFAULT_PERMISSIONS,
  NAVIGATION_PHASES,
  fuzzyMatch,
  permittedCommands,
  searchCommands,
} from "./registry";

const navigationIds = [
  "navigate-organization",
  "navigate-administration",
  "navigate-projects",
  "navigate-budget",
  "navigate-reporting",
  "navigate-requisitions",
  "navigate-approvals",
  "navigate-vendors",
  "navigate-sourcing",
  "navigate-purchase-orders",
  "navigate-assets",
];

describe("command registry", () => {
  it("contains every required lifecycle phase and module by name", () => {
    expect(
      NAVIGATION_PHASES.map((phase) => ({
        label: phase.label,
        modules: phase.items.map((item) => item.label),
      })),
    ).toEqual([
      { label: "Foundation", modules: ["Organization", "Administration"] },
      { label: "Plan", modules: ["Projects", "Budget", "Reporting"] },
      { label: "Demand", modules: ["Requisitions", "Approvals"] },
      { label: "Commit", modules: ["Vendors", "Sourcing", "Purchase orders", "Assets"] },
    ]);
  });

  it("returns the complete permitted set and excludes both unauthorized and destructive entries", () => {
    expect(permittedCommands(DEFAULT_PERMISSIONS).map((command) => command.id)).toEqual([
      "record-north-plant-renewal",
      "create-project",
      "keyboard-help",
      ...navigationIds,
    ]);
  });

  it("reveals a permitted destructive entry only with the explicit modifier", () => {
    expect(permittedCommands(DEFAULT_PERMISSIONS, true).map((command) => command.id)).toEqual([
      "record-north-plant-renewal",
      "create-project",
      "keyboard-help",
      "archive-view",
      ...navigationIds,
    ]);
  });

  it("requires the declared permission before advertising organization management", () => {
    const permissions = new Set([...DEFAULT_PERMISSIONS, "organization.admin"] as const);
    expect(permittedCommands(permissions).map((command) => command.id)).toEqual([
      "record-north-plant-renewal",
      "create-project",
      "keyboard-help",
      "manage-organization",
      ...navigationIds,
    ]);
  });

  it("fuzzy-matches in order and ranks a matching label", () => {
    expect(fuzzyMatch("npr", "North plant renewal")?.indices).toEqual([0, 6, 12]);
    expect(searchCommands(COMMANDS, "npr").map((command) => command.id)).toEqual([
      "record-north-plant-renewal",
      "navigate-projects",
      "navigate-reporting",
      "navigate-approvals",
    ]);
    expect(fuzzyMatch("nrz", "North plant renewal")).toBeNull();
  });

  it("binds the displayed C shortcut to the create-project registry entry", () => {
    expect(
      COMMANDS.filter((command) => command.shortcut === "C").map(({ href, id, label }) => ({
        href,
        id,
        label,
      })),
    ).toEqual([{ href: "/projects/new", id: "create-project", label: "Create project" }]);
  });
});
