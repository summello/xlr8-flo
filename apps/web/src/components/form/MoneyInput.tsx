import { forwardRef } from "react";

import Input, { type InputProps } from "./Input";

export type MoneyInputProps = Omit<InputProps, "inputMode" | "type"> & {
  currency: string;
};

const MoneyInput = forwardRef<HTMLInputElement, MoneyInputProps>(function MoneyInput(
  { currency, ...props },
  ref,
) {
  return (
    <div className="money-input">
      <Input {...props} inputMode="decimal" ref={ref} type="text" />
      <span className="money-input-currency">{currency}</span>
    </div>
  );
});

export default MoneyInput;
