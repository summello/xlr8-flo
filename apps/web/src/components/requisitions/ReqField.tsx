import { Check, WarningCircle } from "@phosphor-icons/react";
import type { ReactNode } from "react";

export type ControlProps = { id: string; required: boolean; "aria-invalid"?: true; "aria-describedby": string };

/** MASTER 7.11: the control carries `required`; the tag turns to Done from the control's own validity (CSS :has). */
export default function ReqField({ id, label, help, error, required = true, wide, children }: {
  id: string; label: string; help: ReactNode; error?: string; required?: boolean; wide?: boolean; children: (props: ControlProps) => ReactNode;
}) {
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  return <div className="req-field" data-wide={wide || undefined} data-invalid={error ? "true" : undefined}>
    <label className="t-label" htmlFor={id}>{label}
      {required && <span className="req-tag" aria-hidden="true"><span className="rq-need">Required</span><span className="rq-done"><Check weight="regular" />Done</span></span>}
    </label>
    {children({ id, required, ...(error ? { "aria-invalid": true as const } : {}), "aria-describedby": error ? `${helpId} ${errorId}` : helpId })}
    <p className="field-help" id={helpId}>{help}</p>
    {error && <p className="field-error" id={errorId}><WarningCircle aria-hidden="true" weight="regular" /><span>{error}</span></p>}
  </div>;
}
