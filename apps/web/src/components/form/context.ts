import { createContext, useContext } from "react";

export type FieldContextValue = {
  describedBy: string;
  error: string | undefined;
  id: string;
  required: boolean;
};

export const FieldContext = createContext<FieldContextValue | null>(null);

export function useFieldContext(): FieldContextValue {
  const context = useContext(FieldContext);
  if (context === null) throw new Error("Form controls must be rendered inside Field");
  return context;
}
