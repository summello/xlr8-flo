import type { components } from "../../api/generated/schema";
import Signal from "../ui/Signal";

type Server = components["schemas"]["Eligibility"];

export type EligibilityProps = {
  /** The server's answer, rendered as is. Nothing here works out eligibility. */
  eligibility: Server;
  variant: "cell" | "header" | "picker" | "field";
  /** Names the unit in the header form: `Not Eligible In Plant 4`. */
  unitName?: string;
};

/** MASTER 7.14. `eligible` decides the words; the reasons only explain them. */
export default function Eligibility({
  eligibility,
  variant,
  unitName,
}: EligibilityProps) {
  const { eligible } = eligibility;
  const reasons = eligibility.reasons ?? [];
  const warnings = eligibility.warnings ?? [];
  const shown = eligible ? warnings : reasons;
  const tone = !eligible
    ? "danger"
    : warnings.length > 0
      ? "warning"
      : "success";
  const place = variant === "header" && unitName ? `In ${unitName}` : "";
  const label =
    `${eligible ? "Eligible" : "Not Eligible"} ${place || (eligible ? "Here" : "")}`.trim();

  if (variant === "header") {
    return (
      <Signal
        className="elig"
        tone={tone}
        label={label}
        why={
          shown.length > 0 ? (
            <ul className="sig-why">
              {shown.map((r) => (
                <li key={`${r.code}-${r.document_type ?? ""}`}>{r.text}</li>
              ))}
            </ul>
          ) : null
        }
      />
    );
  }

  return (
    <Signal
      tone={tone}
      label={label}
      why={
        shown.length > 0 ? (
          <span className="sig-why">
            {shown[0]?.text}
            {shown.length > 1 ? ` and ${shown.length - 1} more` : ""}
          </span>
        ) : null
      }
    />
  );
}
