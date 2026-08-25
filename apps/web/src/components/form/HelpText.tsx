import type { ReactNode } from "react";

type HelpTextProps = {
  children: ReactNode;
  id: string;
};

export default function HelpText({ children, id }: HelpTextProps) {
  return (
    <p className="field-help" id={id}>
      {children}
    </p>
  );
}
