import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import schema from "./openapi.json";

const generatedSource = readFileSync(new URL("./schema.ts", import.meta.url), "utf8");

describe("generated API contract", () => {
  it("contains the request and RFC 9457 schemas required by the type bridge", () => {
    const schemas = schema.components?.schemas ?? {};
    for (const name of ["LoginRequest", "ProblemDetails", "ProblemFieldError"]) {
      expect(schemas, `MISSING: OpenAPI component ${name}`).toHaveProperty(name);
      expect(generatedSource, `MISSING: generated TypeScript component ${name}`).toContain(
        `${name}: {`,
      );
    }
  });

  it("keeps every public operation on the generated RFC 9457 error contract", () => {
    const operations = Object.values(schema.paths).flatMap((path) =>
      Object.values(path).filter(
        (operation): operation is { responses: Record<string, unknown> } =>
          typeof operation === "object" && operation !== null && "responses" in operation,
      ),
    );

    expect(operations.length, "MISSING: public OpenAPI operations").toBeGreaterThan(0);
    for (const operation of operations) {
      expect(operation.responses, "MISSING: 4XX problem response").toHaveProperty("4XX");
      expect(operation.responses, "MISSING: 5XX problem response").toHaveProperty("5XX");
    }
  });
});
