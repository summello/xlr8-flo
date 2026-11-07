/** MASTER §7.15: a sensitive value in the mono face with fixed tracking, so the width does not change on reveal. */
export default function MaskedValue({ value }: { value: string | null | undefined }) {
  return value ? <span className="mask">{value}</span> : <span className="req-sub">Not set</span>;
}
