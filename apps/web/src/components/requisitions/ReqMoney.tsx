import { MinusCircle } from "@phosphor-icons/react";
import { formatMoney } from "../../lib/money";

/** Money with tabular figures, the currency visible, and negatives parenthesised, signed and coloured; a zero is shown as 0.00 (not a dash). */
export default function ReqMoney({ value, currency }: { value: string; currency: string }) {
  const negative = value.startsWith("-") && !/^-0(?:\.0*)?$/.test(value);
  const zero = /^-?0(?:\.0*)?$/.test(value);
  return <span className="project-money numeric" data-negative={negative || undefined} data-zero={zero || undefined} title={`${value} ${currency}`}>
    {negative && <MinusCircle aria-hidden="true" weight="regular" />}
    <span>{formatMoney(negative ? value : value.replace(/^-/, ""), currency)}</span><span className="project-currency">{currency}</span>
  </span>;
}
