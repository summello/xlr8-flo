import { MinusCircle } from "@phosphor-icons/react";
import { formatMoney } from "../../lib/money";

export default function Money({ value, currency }: { value: string | null; currency: string }) {
  if (value === null) return <span>—<span className="visually-hidden">No access to balances</span></span>;
  const zero = /^0(?:\.0*)?$/.test(value);
  return <span className="project-money numeric" data-negative={value.startsWith("-") || undefined} data-zero={zero || undefined} title={`${value} ${currency}`}>
    {value.startsWith("-") && <MinusCircle aria-hidden="true" weight="regular" />}
    <span>{zero ? "—" : formatMoney(value, currency)}</span><span className="project-currency">{currency}</span>
  </span>;
}
