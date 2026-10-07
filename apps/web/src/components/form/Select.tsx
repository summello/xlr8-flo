import { forwardRef, type SelectHTMLAttributes } from "react";

import { useFieldContext } from "./context";

export type SelectProps = Omit<
  SelectHTMLAttributes<HTMLSelectElement>,
  "aria-describedby" | "aria-invalid" | "aria-required"
>;

const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(props, ref) {
  const field = useFieldContext();
  return (
    <select
      {...props}
      aria-describedby={field.describedBy}
      aria-invalid={field.error === undefined ? undefined : true}
      aria-required={field.required || undefined}
      id={field.id}
      ref={ref}
    />
  );
});

export default Select;
