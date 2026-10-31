import type { ReactNode } from "react";

/** The existing tab keyboard pattern: arrows wrap, Home and End jump; only the selected tab is in the tab order. */
export default function Tabs<T extends string>({ label, idPrefix, tabs, value, onChange, children }: { label: string; idPrefix: string; tabs: readonly (readonly [T, string])[]; value: T; onChange: (next: T) => void; children: ReactNode }) {
  return <>
    <div role="tablist" aria-label={label} className="req-tabs">{tabs.map(([name, text], i) => <button key={name} type="button" role="tab" id={`${idPrefix}-tab-${name}`} aria-selected={value === name} aria-controls={`${idPrefix}-panel`} tabIndex={value === name ? 0 : -1} onClick={() => onChange(name)} onKeyDown={e => {
      const next = e.key === "ArrowRight" ? tabs[(i + 1) % tabs.length] : e.key === "ArrowLeft" ? tabs[(i + tabs.length - 1) % tabs.length] : e.key === "Home" ? tabs[0] : e.key === "End" ? tabs[tabs.length - 1] : undefined;
      if (next) { e.preventDefault(); onChange(next[0]); document.getElementById(`${idPrefix}-tab-${next[0]}`)?.focus(); }
    }}>{text}</button>)}</div>
    <div role="tabpanel" id={`${idPrefix}-panel`} aria-labelledby={`${idPrefix}-tab-${value}`}>{children}</div>
  </>;
}
