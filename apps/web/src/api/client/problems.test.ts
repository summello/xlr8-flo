import { describe, expect, it, vi } from "vitest";

import type { components } from "../generated/schema";
import { applyProblemErrors } from "./problems";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginProblem = components["schemas"]["ProblemDetails"];

describe("applyProblemErrors", () => {
  it("maps every generated RFC 9457 field error without a field-name lookup table", () => {
    const setError = vi.fn();
    const problem: LoginProblem = {
      correlation_id: "correlation-123",
      errors: [
        { field: "email", message: "Use an active account email and try again." },
        { field: "password", message: "Enter the current password and try again." },
      ],
      status: 422,
    };

    expect(applyProblemErrors<LoginRequest>(problem, setError)).toBe(true);
    expect(setError).toHaveBeenNthCalledWith(
      1,
      "email",
      { message: "Use an active account email and try again.", type: "server" },
      { shouldFocus: true },
    );
    expect(setError).toHaveBeenNthCalledWith(
      2,
      "password",
      { message: "Enter the current password and try again.", type: "server" },
      { shouldFocus: false },
    );
  });

  it("does not manufacture field errors when the generated problem has none", () => {
    const setError = vi.fn();
    expect(applyProblemErrors<LoginRequest>({ correlation_id: "correlation-123" }, setError)).toBe(
      false,
    );
    expect(setError).not.toHaveBeenCalled();
  });
});
