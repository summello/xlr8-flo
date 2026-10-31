import "./signal.css";

const named = (name: string | null | undefined) => (name?.trim() ? name.trim() : null);

/** Text-only positions (an `<option>`, an `aria-label`, a sentence): "Name (email)" or the email alone. */
// eslint-disable-next-line react-refresh/only-export-components -- the packet keeps both exports in one module
export function personLabel(name: string | null | undefined, email: string): string {
  const n = named(name);
  return n ? `${n} (${email})` : email;
}

/** A person by name with the email as secondary text; the email alone when there is no name (UX-007). */
export default function Person({ name, email }: { name: string | null | undefined; email: string }) {
  const n = named(name);
  if (!n) return <span className="person" title={email}><span>{email}</span></span>;
  return <span className="person" title={`${n} ${email}`}><span className="person-name">{n}</span><span className="person-email">{email}</span></span>;
}
