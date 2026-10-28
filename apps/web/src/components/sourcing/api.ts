import type { components } from "../../api/generated/schema";

export type Rfq = components["schemas"]["RfqRead"];
export type RfqListRow = components["schemas"]["RfqListRow"];
export type RfqCounts = components["schemas"]["RfqCounts"];
export type RfqPage = components["schemas"]["RfqPage"];
export type RfqEvent = components["schemas"]["RfqEvent"];
export type RfqVersion = components["schemas"]["RfqVersionRead"];
export type Invitee = components["schemas"]["InviteeRead"];

export const MAX_INVITEES = 5; // decision 106

/** Draft to Awarded, in order; Cancelled is a banner, never a step (pages/sourcing.md 4.4). */
export const LIFECYCLE = [
  ["draft", "Draft"],
  ["published", "Published"],
  ["closed", "Closed"],
  ["under_evaluation", "Under Evaluation"],
  ["awarded", "Awarded"],
] as const;

/** `in 2 days` or `closed 3 days ago`, from the server's instant. The date beside it stays canonical. */
export function relativeDeadline(iso: string, now = Date.now()): string {
  const ms = new Date(iso).getTime() - now;
  const [unit, size] = Math.abs(ms) >= 86_400_000 ? (["day", 86_400_000] as const) : Math.abs(ms) >= 3_600_000 ? (["hour", 3_600_000] as const) : (["minute", 60_000] as const);
  const words = new Intl.RelativeTimeFormat("en", { numeric: "always" }).format(Math.trunc(ms / size), unit);
  return ms < 0 ? `closed ${words}` : words;
}

const parts = (ms: number, zone: string) => Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: zone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).formatToParts(new Date(ms)).map(p => [p.type, p.value])) as Record<string, string>;
const asUtc = (p: Record<string, string>) => Date.UTC(+p.year!, +p.month! - 1, +p.day!, +p.hour!, +p.minute!, +p.second!);

/** The date (`YYYY-MM-DD`) and 24-hour time (`HH:MM`) an instant has on the wall clock of `zone`. */
export function wallClock(iso: string, zone: string): { date: string; time: string } {
  const p = parts(new Date(iso).getTime(), zone);
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

/** The instant a wall-clock date and time mean in `zone`; null if either is malformed or the clocks skipped it. Two passes settle a daylight-saving edge. */
export function instantIn(date: string, time: string, zone: string): Date | null {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date), t = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(time);
  if (!d || !t) return null;
  const wall = Date.UTC(+d[1]!, +d[2]! - 1, +d[3]!, +t[1]!, +t[2]!);
  if (Number.isNaN(wall) || new Date(wall).getUTCDate() !== +d[3]!) return null;
  let guess = wall;
  for (let i = 0; i < 2; i++) guess = wall - (asUtc(parts(guess, zone)) - guess);
  const result = new Date(guess), back = wallClock(result.toISOString(), zone);
  return back.date === date && back.time === time ? result : null; // a wall time the clocks skipped never happened
}

export type Award = components["schemas"]["AwardRead"];
