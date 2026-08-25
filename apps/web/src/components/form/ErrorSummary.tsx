import { WarningCircle } from "@phosphor-icons/react";
import { useId } from "react";

export type SummaryError = {
  fieldId: string;
  label: string;
  message: string;
};

type ErrorSummaryProps = {
  errors: readonly SummaryError[];
};

export default function ErrorSummary({ errors }: ErrorSummaryProps) {
  const titleId = `error-summary-${useId().replaceAll(":", "")}`;
  if (errors.length === 0) return null;

  const focusField = (fieldId: string) => {
    document.getElementById(fieldId)?.focus();
  };

  return (
    <section aria-labelledby={titleId} className="error-summary" role="alert">
      <h2 id={titleId}>
        <WarningCircle aria-hidden="true" weight="regular" />
        Review the fields below
      </h2>
      <p>Your entries were preserved. Correct each field, then submit again.</p>
      <ul>
        {errors.map((error) => (
          <li key={error.fieldId}>
            <a
              href={`#${error.fieldId}`}
              onClick={(event) => {
                event.preventDefault();
                focusField(error.fieldId);
              }}
            >
              {error.label}: {error.message}
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
