import {
  ArrowsClockwise,
  CheckCircle,
  Circle,
  Warning,
  XCircle,
  type Icon,
} from "@phosphor-icons/react";

export const STATUS_TONES = ["neutral", "info", "success", "warning", "danger"] as const;

export type StatusTone = (typeof STATUS_TONES)[number];

export type StatusToneDefinition = {
  backgroundToken: `--status-${StatusTone}-tint`;
  foregroundToken: `--status-${StatusTone}`;
  icon: Icon;
};

export const TONES = {
  neutral: {
    backgroundToken: "--status-neutral-tint",
    foregroundToken: "--status-neutral",
    icon: Circle,
  },
  info: {
    backgroundToken: "--status-info-tint",
    foregroundToken: "--status-info",
    icon: ArrowsClockwise,
  },
  success: {
    backgroundToken: "--status-success-tint",
    foregroundToken: "--status-success",
    icon: CheckCircle,
  },
  warning: {
    backgroundToken: "--status-warning-tint",
    foregroundToken: "--status-warning",
    icon: Warning,
  },
  danger: {
    backgroundToken: "--status-danger-tint",
    foregroundToken: "--status-danger",
    icon: XCircle,
  },
} as const satisfies Record<StatusTone, StatusToneDefinition>;
