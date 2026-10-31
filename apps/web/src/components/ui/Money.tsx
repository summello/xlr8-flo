import { MinusCircle } from "@phosphor-icons/react";
import { formatMoney } from "../../lib/money";

/** `zero="dash"` (default) shows a zero amount as a dash; `zero="value"` shows it as 0.00. */
export default function Money({ value, currency, zero: zeroMode = "dash" }: { value: string | null; currency: string; zero?: "dash" | "value" }) {
  if (value === null) return <span>—<span className="visually-hidden">No access to balances</span></span>;
  const zero = /^-?0(?:\.0*)?$/.test(value);
  const negative = value.startsWith("-") && !zero;
  return <span className="project-money numeric" data-negative={negative || undefined} data-zero={zero || undefined} title={`${value} ${currency}`}>
    {negative && <MinusCircle aria-hidden="true" weight="regular" />}
    <span>{zero && zeroMode === "dash" ? "—" : formatMoney(negative ? value : value.replace(/^-/, ""), currency)}</span><span className="project-currency">{currency}</span>
  </span>;
}
