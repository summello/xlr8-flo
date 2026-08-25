import {
  Archive,
  Bank,
  Buildings,
  ChartBar,
  CheckSquare,
  FilePlus,
  FileText,
  FolderOpen,
  GearSix,
  Gavel,
  Package,
  ShoppingCart,
  Storefront,
  Wallet,
  type Icon,
} from "@phosphor-icons/react";

export type Phase = "foundation" | "plan" | "demand" | "commit";
export type Permission =
  | "records:read"
  | "projects:create"
  | "views:archive"
  | "organization:admin";
export type CommandGroup = "Records" | "Actions" | "Navigation";

export type NavigationItem = {
  href: string;
  icon: Icon;
  id: string;
  label: string;
  phase: Phase;
};

export type NavigationPhase = {
  id: Phase;
  label: string;
  items: readonly NavigationItem[];
};

export type Command = {
  destructive?: boolean;
  group: CommandGroup;
  href: string;
  icon: Icon;
  id: string;
  keywords: readonly string[];
  label: string;
  phase?: Phase;
  recent?: boolean;
  requiredPermissions?: readonly Permission[];
  shortcut?: string;
};

export type FuzzyResult = {
  indices: number[];
  score: number;
};

export const NAVIGATION_PHASES: readonly NavigationPhase[] = [
  {
    id: "foundation",
    label: "Foundation",
    items: [
      {
        href: "/organization",
        icon: Buildings,
        id: "organization",
        label: "Organization",
        phase: "foundation",
      },
      {
        href: "/administration",
        icon: GearSix,
        id: "administration",
        label: "Administration",
        phase: "foundation",
      },
    ],
  },
  {
    id: "plan",
    label: "Plan",
    items: [
      { href: "/projects", icon: FolderOpen, id: "projects", label: "Projects", phase: "plan" },
      { href: "/budget", icon: Wallet, id: "budget", label: "Budget", phase: "plan" },
      { href: "/reporting", icon: ChartBar, id: "reporting", label: "Reporting", phase: "plan" },
    ],
  },
  {
    id: "demand",
    label: "Demand",
    items: [
      {
        href: "/requisitions",
        icon: FileText,
        id: "requisitions",
        label: "Requisitions",
        phase: "demand",
      },
      {
        href: "/approvals",
        icon: CheckSquare,
        id: "approvals",
        label: "Approvals",
        phase: "demand",
      },
    ],
  },
  {
    id: "commit",
    label: "Commit",
    items: [
      { href: "/vendors", icon: Storefront, id: "vendors", label: "Vendors", phase: "commit" },
      { href: "/sourcing", icon: Gavel, id: "sourcing", label: "Sourcing", phase: "commit" },
      {
        href: "/purchase-orders",
        icon: ShoppingCart,
        id: "purchase-orders",
        label: "Purchase orders",
        phase: "commit",
      },
      { href: "/assets", icon: Package, id: "assets", label: "Assets", phase: "commit" },
    ],
  },
] as const;

const NAVIGATION_COMMANDS: readonly Command[] = NAVIGATION_PHASES.flatMap((phase) =>
  phase.items.map((item) => ({
    group: "Navigation" as const,
    href: item.href,
    icon: item.icon,
    id: `navigate-${item.id}`,
    keywords: [phase.label, item.label],
    label: `Go to ${item.label}`,
    phase: item.phase,
  })),
);

export const COMMANDS: readonly Command[] = [
  {
    group: "Records",
    href: "/projects/north-plant-renewal",
    icon: Bank,
    id: "record-north-plant-renewal",
    keywords: ["capital", "project", "plant"],
    label: "North plant renewal",
    phase: "plan",
    recent: true,
    requiredPermissions: ["records:read"],
  },
  {
    group: "Actions",
    href: "/projects/new",
    icon: FilePlus,
    id: "create-project",
    keywords: ["add", "new", "plan"],
    label: "Create project",
    phase: "plan",
    recent: true,
    requiredPermissions: ["projects:create"],
    shortcut: "C",
  },
  {
    group: "Actions",
    href: "/help/keyboard",
    icon: FileText,
    id: "keyboard-help",
    keywords: ["help", "keys", "shortcuts"],
    label: "Open keyboard help",
  },
  {
    group: "Actions",
    href: "/administration",
    icon: GearSix,
    id: "manage-organization",
    keywords: ["admin", "settings"],
    label: "Manage organization",
    phase: "foundation",
    requiredPermissions: ["organization:admin"],
  },
  {
    destructive: true,
    group: "Actions",
    href: "/views/archive",
    icon: Archive,
    id: "archive-view",
    keywords: ["delete", "remove"],
    label: "Archive current view",
    requiredPermissions: ["views:archive"],
  },
  ...NAVIGATION_COMMANDS,
] as const;

export const DEFAULT_PERMISSIONS: ReadonlySet<Permission> = new Set([
  "projects:create",
  "records:read",
  "views:archive",
]);

export function permittedCommands(
  permissions: ReadonlySet<Permission>,
  includeDestructive = false,
): Command[] {
  return COMMANDS.filter(
    (command) =>
      (!command.destructive || includeDestructive) &&
      (command.requiredPermissions?.every((permission) => permissions.has(permission)) ?? true),
  );
}

export function fuzzyMatch(query: string, candidate: string): FuzzyResult | null {
  const needle = query.trim().toLocaleLowerCase();
  if (needle.length === 0) return { indices: [], score: 0 };

  const haystack = candidate.toLocaleLowerCase();
  const indices: number[] = [];
  let cursor = 0;
  let score = 0;

  for (const character of needle) {
    const index = haystack.indexOf(character, cursor);
    if (index < 0) return null;
    const previous = indices.at(-1);
    score += previous !== undefined && index === previous + 1 ? 4 : 1;
    if (index === 0 || /[\s/-]/.test(haystack[index - 1] ?? "")) score += 2;
    indices.push(index);
    cursor = index + 1;
  }

  return { indices, score };
}

export function searchCommands(commands: readonly Command[], query: string): Command[] {
  if (query.trim().length === 0) return [...commands];

  return commands
    .map((command) => {
      const labelMatch = fuzzyMatch(query, command.label);
      const keywordMatch = fuzzyMatch(query, command.keywords.join(" "));
      const score = Math.max(labelMatch?.score ?? -1, keywordMatch?.score ?? -1);
      return { command, score };
    })
    .filter((result) => result.score >= 0)
    .sort((first, second) => second.score - first.score)
    .map((result) => result.command);
}
