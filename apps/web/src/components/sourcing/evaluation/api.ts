import type { components } from "../../../api/generated/schema";
import { apiClient } from "../../../api/client";
import { failure, type Problem } from "../../requisitions/api";

export type Evaluation = components["schemas"]["EvaluationRead"];
export type EvaluationScore = components["schemas"]["EvaluationScoreRead"];

/** The server answers 409 `recused` instead of a view once the caller has declared a conflict; the page mirrors it. */
export const RECUSED = "recused" as const;

export async function loadEvaluation(id: string): Promise<Evaluation | typeof RECUSED> {
  const { data, error, response } = await apiClient.GET("/api/v1/sourcing/rfqs/{id}/evaluation", { params: { path: { id } } });
  if (data) return data;
  if (response?.status === 409 && (error as Problem | undefined)?.checks?.problem === "recused") return RECUSED;
  throw new Error(response?.status === 404 ? "This RFQ does not exist or you cannot see it." : failure(error, "Loading the evaluation"));
}

/** The cell key a score is held under in the form. */
export const cellKey = (vendorId: string, criterionId: string) => `${vendorId}|${criterionId}`;

/** A score is a whole number 0..10, as the server takes it. */
export const parseScore = (text: string): number | null => /^(?:[0-9]|10)$/.test(text.trim()) ? Number(text.trim()) : null;
