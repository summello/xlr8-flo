import type { ReactNode } from "react";

import { TONES, type StatusTone } from "../status/tones";
import "./signal.css";

const CLASS = {
  success: "is-ok",
  warning: "is-warn",
  danger: "is-bad",
  info: "is-info",
  neutral: "is-n",
} as const;

type SignalProps = {
  tone: StatusTone;
  /** The words. They carry the meaning; the icon and its tone only repeat it (A11Y-004). */
  label: string;
  /** Reasons, in words, from the server. */
  why?: ReactNode;
  className?: string;
  role?: "status";
};

/** MASTER 7.14: one icon in the tone, then words. Never a button, never inside a pill. */
export default function Signal({
  tone,
  label,
  why,
  className,
  role,
}: SignalProps) {
  const Icon = TONES[tone].icon;
  return (
    <div
      className={["sig", CLASS[tone], className].filter(Boolean).join(" ")}
      role={role}
    >
      <Icon aria-hidden="true" weight="regular" />
      <span>{label}</span>
      {why}
    </div>
  );
}
