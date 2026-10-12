import { Check } from "@phosphor-icons/react";
import { useEffect, useRef, useState } from "react";
import type { components } from "../../../api/generated/schema";
import { apiClient } from "../../../api/client";
import Sheet from "../history/Sheet";
import Field from "../../form/Field";
import Input from "../../form/Input";
import Select from "../../form/Select";
import Money from "../../ui/Money";
type Workflow = components["schemas"]["WorkflowRead"];
type Route = components["schemas"]["RoutePreview"];
export default function Preview({ workflow, version, onClose, onRoute }: {
    workflow: Workflow;
    version: number;
    onClose: () => void;
    onRoute: (route: Route) => void;
}) {
    const [amount, setAmount] = useState(""), [currency, setCurrency] = useState("USD"), [unit, setUnit] = useState(workflow.scope_type === "bu" ? workflow.scope_id : "");
    const [units, setUnits] = useState<components["schemas"]["OrgUnitRead"][]>([]), [cursor, setCursor] = useState<string | null>(null), [unitError, setUnitError] = useState("");
    const [route, setRoute] = useState<Route>(), [estimate, setEstimate] = useState<{
        amount: string;
        currency: string;
    }>(), [error, setError] = useState(""), [fieldError, setFieldError] = useState<string>(), [busy, setBusy] = useState(false);
    const [currencyError, setCurrencyError] = useState<string>();
    const [focusRequest, setFocusRequest] = useState(0);
    const alert = useRef<HTMLDivElement>(null);
    useEffect(() => { if (focusRequest) alert.current?.focus(); }, [focusRequest]);
    async function loadUnits(next?: string) {
        try {
        const r = await apiClient.GET("/api/v1/org/units", { params: { query: { active: true, cursor: next } } });
        if (r.data) {
            setUnits(old => next ? [...old, ...r.data!.rows] : r.data!.rows);
            setCursor(r.data.next_cursor);
            setUnitError("");
        }
        else
            setUnitError("Business units could not load. Your bound scope is kept. Try again.");
        } catch {
            setUnitError("Business units could not load. Your bound scope is kept. Try again.");
        }
    }
    useEffect(() => { void Promise.resolve().then(() => loadUnits()); }, []);
    async function run() {
        setError("");
        setFieldError(undefined);
        setCurrencyError(undefined);
        setBusy(true);
        try {
            const r = await apiClient.GET("/api/v1/workflows/{id}/versions/{version}/route-preview", { params: { path: { id: workflow.id, version }, query: { amount, currency, bu_id: unit || undefined } } });
            if (!r.data) {
                const amountError = r.error?.errors?.find(e => e.field === "/amount")?.message;
                setFieldError(amountError);
                setCurrencyError(r.error?.errors?.find(e => e.field === "/currency")?.message);
                throw new Error(r.error?.detail ?? "Preview could not run.");
            }
            setRoute(r.data);
            setEstimate({ amount, currency });
            onRoute(r.data);
        }
        catch (e) {
            setError(`${e instanceof Error ? e.message : "Preview could not run."} Your estimate is preserved. Correct the fields or try again.`);
            setFocusRequest(n => n + 1);
        }
        finally {
            setBusy(false);
        }
    }
    return <Sheet title="Route Preview" onClose={onClose}><p>This preview uses the estimate only. Other attributes are not part of the sample.</p><form noValidate onSubmit={e => { e.preventDefault(); void run(); }}>{error && <div role="alert" tabIndex={-1} ref={alert}>{error}{fieldError && <a href="#preview-estimate" onClick={e => { e.preventDefault(); document.getElementById("preview-estimate")?.focus(); }}>Estimate: {fieldError}</a>}{currencyError && <a href="#preview-currency" onClick={e => { e.preventDefault(); document.getElementById("preview-currency")?.focus(); }}>Currency: {currencyError}</a>}</div>}<Field id="preview-estimate" label="Estimate" required error={fieldError} help="Enter a non-negative estimate with up to four decimal places."><Input required inputMode="decimal" autoComplete="off" value={amount} onChange={e => setAmount(e.target.value)}/><Check aria-hidden="true" className="wfl-complete" weight="regular" /></Field><Field id="preview-currency" error={currencyError} label="Currency code" help="Use the currency in the workflow condition."><Input inputMode="text" value={currency} autoComplete="off" onChange={e => setCurrency(e.target.value.toUpperCase())}/></Field><Field label="BU/OU" help="Defaults to the workflow's bound scope."><Select value={unit} onChange={e => setUnit(e.target.value)}><option value="">Organization</option>{unit && !units.some(u => u.id === unit) && <option value={unit}>Bound scope</option>}{units.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</Select></Field>{unitError && <p role="alert">{unitError}<button type="button" onClick={() => void loadUnits()}>Retry units</button></p>}{cursor && <button type="button" onClick={() => void loadUnits(cursor)}>More business units</button>}<button type="submit" aria-busy={busy}>Run Preview</button></form>{busy && <p role="status">Computing route…</p>}{route && <><h3>Preview steps</h3>{estimate && <Money value={estimate.amount} currency={estimate.currency}/>}<ol>{route.steps.map((step, index) => <li key={step.node_id}><strong>{index + 1}. {step.label}</strong>{step.reached_via && <p>{step.reached_via}</p>}<p>{step.mode === "quorum" ? `${step.quorum} approvals needed` : step.mode === "all" ? "All approve" : "Any one approves"} · {step.rule}</p><ul>{step.approver_emails.map(email => <li key={email}>{email}</li>)}{step.excluded.map(e => <li key={e.email}>{e.email} · Excluded: {e.reason.replaceAll("_", " ")}</li>)}</ul></li>)}</ol>{route.steps.length === 0 && route.problems.length === 0 && <p>No approval steps on this route.</p>}{route.problems.length > 0 && <p>Preview could not complete. Your estimate is preserved. Correct the workflow issues or scope and run the preview again.</p>}{route.problems.map((p, i) => <p role="alert" key={i}>{p.message}</p>)}{route.separation_of_duty_applied === false && <p>Separation of duty is applied when a request is submitted.</p>}</>}</Sheet>;
}
