import type { ReactNode } from "react";
import type { Rfq } from "../api";
import AttachmentsCard from "./AttachmentsCard";
import CriteriaCard from "./CriteriaCard";
import DeliveryCard from "./DeliveryCard";
import EvaluatorsCard from "./EvaluatorsCard";
import InvitedVendorsCard from "./InvitedVendorsCard";
import ItemsCard from "./ItemsCard";
import RequirementsCard from "./RequirementsCard";
import type { Form, Mode } from "./api";

export type CardProps = {
  rfq: Rfq;
  mode: Mode;
  form: Form;
  setForm: (change: (form: Form) => Form) => void;
  /** Field errors by summary field id, from the last save, publish or amend. */
  errors: Record<string, string>;
  /** The lines card carries the server's `requisition_sourced` refusal, which does not name a line. */
  linesNote: string | null;
  onRfq: (next: Rfq) => void;
  /** Changes a card reports to the page summary (a refused invite, a failed upload). */
  report: (message: string | null) => void;
};

/** The editor's cards, in order. */
export const CARDS: readonly { id: string; render: (props: CardProps) => ReactNode }[] = [
  { id: "items", render: p => <ItemsCard {...p} /> },
  { id: "delivery", render: p => <DeliveryCard {...p} /> },
  { id: "requirements", render: p => <RequirementsCard {...p} /> },
  { id: "attachments", render: p => <AttachmentsCard {...p} /> },
  { id: "invitees", render: p => <InvitedVendorsCard {...p} /> },
  { id: "criteria", render: p => <CriteriaCard {...p} /> },
  { id: "evaluators", render: p => <EvaluatorsCard {...p} /> },
];
