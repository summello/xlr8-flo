import type { CSSProperties } from "react";

import { UNKNOWN_STATUS_LABEL, statusLabel } from "./labels";
import { type DocType, type StatusFor, statusTone } from "./map";
import { TONES, type StatusTone } from "./tones";

export type StatusPillProps = {
  [D in DocType]: {
    docType: D;
    status: StatusFor<D>;
  };
}[DocType];

type ResolvedStatus = {
  label: string;
  tone: StatusTone;
};

function resolveStatus(docType: DocType, status: string): ResolvedStatus {
  const tone = statusTone(docType, status);
  const label = statusLabel(status);

  if (tone === undefined || label === undefined) {
    if (import.meta.env.DEV) {
      throw new Error(`Unmapped ${docType} status: ${status}`);
    }
    return { label: UNKNOWN_STATUS_LABEL, tone: "neutral" };
  }

  return { label, tone };
}

export default function StatusPill({ docType, status }: StatusPillProps) {
  const resolved = resolveStatus(docType, status);
  const tone = TONES[resolved.tone];
  const Icon = tone.icon;
  const style = {
    backgroundColor: `var(${tone.backgroundToken})`,
    borderRadius: "var(--pill-radius)",
    color: `var(${tone.foregroundToken})`,
    gap: "var(--pill-gap)",
    height: "var(--pill-h)",
    paddingInline: "var(--pill-pad-x)",
  } satisfies CSSProperties;

  return (
    <span
      className="status-pill"
      data-doc-type={docType}
      data-status={status}
      data-tone={resolved.tone}
      style={style}
    >
      <Icon aria-hidden="true" weight="regular" />
      <span>{resolved.label}</span>
    </span>
  );
}
