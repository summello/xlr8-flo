import { CheckCircle, Warning } from "@phosphor-icons/react";

/** MASTER 7.14: icon in the tone, words in the foreground. `total` is the server's figure, never summed here. */
export default function Signal({ total }: { total: number }) {
  const ok = total === 100;
  const Icon = ok ? CheckCircle : Warning;
  return (
    <p className={`sig ${ok ? "is-ok" : "is-warn"}`} role="status">
      <Icon aria-hidden="true" weight="regular" />
      <span>Weights total {total} of 100</span>
      {ok ? null : <span className="sig-why">Publishing needs exactly 100.</span>}
    </p>
  );
}
