import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import type { components } from "../generated/schema";

export type ProblemDetails = components["schemas"]["ProblemDetails"];

export function applyProblemErrors<TFields extends FieldValues>(
  problem: ProblemDetails | undefined,
  setError: UseFormSetError<TFields>,
): boolean {
  if (problem?.errors === undefined || problem.errors === null || problem.errors.length === 0) {
    return false;
  }

  problem.errors.forEach((error, index) => {
    setError(
      error.field as Path<TFields>,
      { message: error.message, type: "server" },
      { shouldFocus: index === 0 },
    );
  });
  return true;
}
