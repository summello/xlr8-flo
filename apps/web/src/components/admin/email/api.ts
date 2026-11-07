import type { components } from "../../../api/generated/schema";

export type SendingDomain = components["schemas"]["SendingDomainRead"];
export type Suppression = components["schemas"]["SuppressionRead"];
export type SuppressionPage = components["schemas"]["SuppressionPage"];

export const REASONS: Record<Suppression["reason"], string> = { bounced: "Hard Bounce", complained: "Spam Complaint" };

type Result = SendingDomain["spf"];
/** Words and tone for one DNS signal; the server decides pass or fail, the browser only words it. */
export function dnsSignal(name: "SPF" | "DKIM", value: Result) {
  if (value === "pass") return { tone: "success", label: `${name} Passes` } as const;
  if (value === "fail") return { tone: "danger", label: `${name} Fails` } as const;
  if (value === "unknown") return { tone: "warning", label: `${name} Not Confirmed` } as const;
  return { tone: "neutral", label: "Not Checked Yet" } as const;
}
