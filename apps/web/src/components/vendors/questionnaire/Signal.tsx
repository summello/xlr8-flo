import Signal from "../../ui/Signal";

/** MASTER 7.14. `total` is the server's figure, never summed here. */
export default function WeightSignal({ total }: { total: number }) {
  const ok = total === 100;
  return (
    <Signal
      role="status"
      tone={ok ? "success" : "warning"}
      label={`Weights total ${total} of 100`}
      why={ok ? null : <span className="sig-why">Publishing needs exactly 100.</span>}
    />
  );
}
