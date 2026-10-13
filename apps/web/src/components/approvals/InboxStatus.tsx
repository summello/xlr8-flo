import { ArrowsClockwise, CheckCircle, Circle, Warning, XCircle } from "@phosphor-icons/react";

import { statusTone } from "./inbox-model";

const ICONS = { neutral: Circle, info: ArrowsClockwise, success: CheckCircle, warning: Warning, danger: XCircle };

/** Icon and words, never colour alone. StatusPill's closed document vocabulary has no task words. */
export default function InboxStatus({ word }: { word: string }) {
  const tone = statusTone(word);
  const Icon = ICONS[tone];
  return (
    <span className="appr-pill" data-tone={tone}>
      <Icon aria-hidden="true" weight="regular" />
      <span>{word}</span>
    </span>
  );
}
