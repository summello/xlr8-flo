import Field from "../../form/Field";
import Input from "../../form/Input";
import Select from "../../form/Select";
import type { CardProps } from "./cards";
import Textarea from "./Textarea";

/** The deadline is entered in the RFQ's own zone, as `ExtendDeadlineDialog` does; choosing another zone is a later story. */
export default function DeliveryCard({ rfq, form, setForm, errors }: CardProps) {
  const zone = rfq.deadline_tz ?? "UTC";
  return <section className="req-card" aria-labelledby="rfq-card-delivery">
    <header className="req-card-head"><h2 id="rfq-card-delivery">Delivery, Deadline and Terms</h2></header>
    <Field id="rfq-delivery_location" label="Delivery Location" required help="Where the goods or service arrive." error={errors["rfq-delivery_location"]}>
      <Input autoComplete="off" inputMode="text" value={form.delivery_location} onChange={e => setForm(f => ({ ...f, delivery_location: e.target.value }))} />
    </Field>
    <Field id="rfq-delivery_terms" label="Delivery Terms" required help="Incoterms or the delivery conditions you require." error={errors["rfq-delivery_terms"]}>
      <Input autoComplete="off" inputMode="text" value={form.delivery_terms} onChange={e => setForm(f => ({ ...f, delivery_terms: e.target.value }))} />
    </Field>
    <div className="rfq-filters">
      <Field id="rfq-date" label="Response Deadline Date" required help="YYYY-MM-DD." error={errors["rfq-date"]}>
        <Input autoComplete="off" inputMode="numeric" placeholder="YYYY-MM-DD" value={form.date} onChange={e => setForm(f => ({ ...f, date: e.target.value }))} />
      </Field>
      <Field id="rfq-time" label="Time (24-hour)" required help="HH:MM.">
        <Input autoComplete="off" inputMode="numeric" placeholder="HH:MM" value={form.time} onChange={e => setForm(f => ({ ...f, time: e.target.value }))} />
      </Field>
      <Field id="rfq-zone" label="Time Zone" help="The zone this RFQ closes in.">
        <Select value={zone} onChange={() => undefined}><option value={zone}>{zone}</option></Select>
      </Field>
    </div>
    <Field id="rfq-terms" label="Terms" required help="Commercial terms the vendor accepts by bidding." error={errors["rfq-terms"]}>
      <Textarea rows={5} value={form.terms} onChange={e => setForm(f => ({ ...f, terms: e.target.value }))} />
    </Field>
  </section>;
}
