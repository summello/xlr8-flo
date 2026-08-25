import { forwardRef, useId, type InputHTMLAttributes } from "react";

import { useFieldContext } from "./context";

export type ComboboxOption = {
  label: string;
  value: string;
};

export type ComboboxProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  | "aria-describedby"
  | "aria-invalid"
  | "aria-required"
  | "autoComplete"
  | "inputMode"
  | "list"
> & {
  autoComplete: string;
  inputMode: NonNullable<InputHTMLAttributes<HTMLInputElement>["inputMode"]>;
  options: readonly ComboboxOption[];
};

const Combobox = forwardRef<HTMLInputElement, ComboboxProps>(function Combobox(
  { options, ...props },
  ref,
) {
  const field = useFieldContext();
  const generatedId = useId().replaceAll(":", "");
  const listId = `${field.id}-${generatedId}-options`;

  return (
    <>
      <input
        {...props}
        aria-describedby={field.describedBy}
        aria-invalid={field.error === undefined ? undefined : true}
        aria-required={field.required || undefined}
        id={field.id}
        list={listId}
        ref={ref}
      />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </datalist>
    </>
  );
});

export default Combobox;
