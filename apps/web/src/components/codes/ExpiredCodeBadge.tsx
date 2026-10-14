import { WarningCircle } from "@phosphor-icons/react";
import "../../styles/requisitions.css";

/** D-M2-25: icon and text, never colour alone (A11Y-004). */
export default function ExpiredCodeBadge() {
  return <p className="req-pill" data-tone="danger"><WarningCircle aria-hidden="true" weight="regular" />Expired code</p>;
}
