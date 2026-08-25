import { WarningCircle } from "@phosphor-icons/react";
import { useId, type ReactNode } from "react";

import { FieldContext } from "./context";
import HelpText from "./HelpText";

export type FieldProps = {
  children: ReactNode;
  error?: string;
  help: ReactNode;
  id?: string;
  label: string;
  required?: boolean;
};

export default function Field({
  children,
  error,
  help,
  id: providedId,
  label,
  required = false,
}: FieldProps) {
  const generatedId = useId();
  const id = providedId ?? `field-${generatedId.replaceAll(":", "")}`;
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const describedBy = error === undefined ? helpId : `${helpId} ${errorId}`;

  return (
    <div className="form-field" data-invalid={error === undefined ? undefined : "true"}>
      <label htmlFor={id}>
        {label}
        {required ? (
          <span aria-hidden="true" className="field-required">
            *
          </span>
        ) : undefined}
      </label>
      <FieldContext.Provider value={{ describedBy, error, id, required }}>
        {children}
      </FieldContext.Provider>
      <HelpText id={helpId}>{help}</HelpText>
      {error === undefined ? undefined : (
        <p className="field-error" id={errorId}>
          <WarningCircle aria-hidden="true" weight="regular" />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}
