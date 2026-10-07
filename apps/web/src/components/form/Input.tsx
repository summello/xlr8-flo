import { forwardRef, type InputHTMLAttributes } from "react";

import { useFieldContext } from "./context";

export type InputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "aria-describedby" | "aria-invalid" | "aria-required" | "autoComplete" | "inputMode"
> & {
  autoComplete: string;
  inputMode: NonNullable<InputHTMLAttributes<HTMLInputElement>["inputMode"]>;
};

const Input = forwardRef<HTMLInputElement, InputProps>(function Input(props, ref) {
  const field = useFieldContext();
  return (
    <input
      {...props}
      aria-describedby={field.describedBy}
      aria-invalid={field.error === undefined ? undefined : true}
      aria-required={field.required || undefined}
      id={field.id}
      ref={ref}
    />
  );
});

export default Input;
