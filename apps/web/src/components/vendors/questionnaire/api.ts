import type { components } from "../../../api/generated/schema";
import { writeHeaders } from "../../../features/auth/api";
import { mutationHeaders } from "../../requisitions/api";

export type VersionRead = components["schemas"]["VersionRead"];
export type VersionDetail = components["schemas"]["VersionDetail"];
export type Question = components["schemas"]["QuestionAdmin"];
export type QuestionType = components["schemas"]["QuestionType"];

export { mutationHeaders };

/** PATCH is not idempotency-keyed (m2-conventions O): CSRF plus the integer version in `If-Match`. */
export const patchHeaders = (version: number) => ({ ...writeHeaders(), "If-Match": String(version) });

export const TYPE_LABELS: Readonly<Record<QuestionType, string>> = {
  text: "Text",
  number: "Number",
  yes_no: "Yes or No",
  single_choice: "Single Choice",
  multi_choice: "Multiple Choice",
  date: "Date",
  file: "File",
};
export const TYPES = Object.keys(TYPE_LABELS) as QuestionType[];

/** Editable points, kept as strings so a half-typed number is not lost. */
export type Choice = { value?: string; label: string; points: string };
export type Band = { min: string; max: string; points: string };
export type Points = { yes: string; no: string; choices: Choice[]; bands: Band[] };

export const emptyPoints = (type: QuestionType): Points => ({
  yes: "0",
  no: "0",
  choices: type === "single_choice" || type === "multi_choice" ? [{ label: "", points: "0" }] : [],
  bands: [],
});

type Raw = Record<string, unknown>;

export function pointsFrom(type: QuestionType, options: unknown): Points {
  const points = emptyPoints(type);
  if (type === "yes_no" && options && typeof options === "object") {
    const o = options as Raw;
    return { ...points, yes: String(o.yes ?? 0), no: String(o.no ?? 0) };
  }
  if (!Array.isArray(options)) return points;
  if (type === "number") {
    return { ...points, bands: (options as Raw[]).map(b => ({ min: String(b.min ?? ""), max: String(b.max ?? ""), points: String(b.points ?? 0) })) };
  }
  return { ...points, choices: (options as Raw[]).map(c => ({ value: String(c.value), label: String(c.label), points: String(c.points) })) };
}

const slug = (label: string) => label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);

/** A stable choice value: kept for an existing choice, derived once from the label for a new one. */
export function uniqueKey(label: string, taken: ReadonlySet<string>, fallback: string): string {
  const base = slug(label) || fallback;
  let key = base, n = 2;
  while (taken.has(key)) key = `${base.slice(0, 36)}-${n++}`;
  return key;
}

/** The `options` body for a type. The server validates it and answers with field errors. */
export function optionsFor(type: QuestionType, p: Points): unknown {
  if (type === "yes_no") return { yes: Number(p.yes), no: Number(p.no) };
  if (type === "number") {
    return p.bands.length === 0 ? null : p.bands.map(b => ({ min: b.min.trim() || null, max: b.max.trim() || null, points: Number(b.points) }));
  }
  if (type === "single_choice" || type === "multi_choice") {
    const taken = new Set(p.choices.flatMap(c => (c.value ? [c.value] : [])));
    return p.choices.map((c, i) => {
      const value = c.value ?? uniqueKey(c.label, taken, `choice-${i + 1}`);
      taken.add(value);
      return { value, label: c.label.trim(), points: Number(c.points) };
    });
  }
  return null;
}

export function pointsSummary(q: Question): string {
  const o = pointsFrom(q.type, q.options);
  if (q.type === "yes_no") return `Yes ${o.yes}, No ${o.no}`;
  if (q.type === "number") return o.bands.length ? o.bands.map(b => `${b.min || "any"} to ${b.max || "any"}: ${b.points}`).join("; ") : "No bands";
  if (o.choices.length && (q.type === "single_choice" || q.type === "multi_choice")) return o.choices.map(c => `${c.label}: ${c.points}`).join("; ");
  return "Not scored by answer";
}
