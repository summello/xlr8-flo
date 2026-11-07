import { useEffect, useRef, useState } from "react";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const ms = (name: string) => parseFloat(css(name)) || 0;

const FRAMES = ["The order is ready to send.", "Sealed: this version is issued.", "Email queued to the vendor."] as const;

/**
 * MASTER 7.16.2: the document lifts, a seal marks it, a trace runs to the vendor. `step` (0..3) drives the finished
 * look in CSS; each play step animates from the previous look to it and chains on the previous animation's `finished`
 * promise. The drawing is decoration: the Frames table carries every frame in words (A11Y-009).
 */
export default function IssueStage({ step, play, onStep, failed, number, version, vendor, failure }: {
  step: number; play: boolean; onStep: (n: number) => void; failed: boolean; number: string; version: number; vendor: string; failure: string | null;
}) {
  const doc = useRef<HTMLDivElement>(null), seal = useRef<HTMLSpanElement>(null), trace = useRef<HTMLSpanElement>(null);
  const [shown, setShown] = useState(1);
  useEffect(() => {
    if (!play || !doc.current || !seal.current || !trace.current) return;
    const running: Animation[] = [];
    const duration = ms("--dur-slow"), easing = css("--ease-out");
    // Each frame animates from its old look to its new one; the step is committed (CSS takes the new look) when it finishes.
    const go = async (el: HTMLElement, keyframes: Keyframe[], n: number) => {
      setShown(n);
      const a = el.animate(keyframes, { duration, easing, fill: "forwards" });
      running.push(a);
      await a.finished;
      onStep(n);
    };
    void (async () => {
      await go(doc.current!, [{ transform: "none" }, { transform: "translateY(calc(var(--space-2) * -1))" }], 1);
      await go(seal.current!, [{ opacity: 0, transform: "scale(1.6)" }, { opacity: 1, transform: "none" }], 2);
      await go(trace.current!, [{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], 3);
    })().catch(() => { /* cancelled on unmount */ });
    return () => running.forEach(a => a.cancel());
    // eslint-disable-next-line react-hooks/exhaustive-deps -- plays once per mount
  }, [play]);
  const current = play ? shown : 3;

  const frames = [FRAMES[0], FRAMES[1], failure ?? FRAMES[2].replace("the vendor", vendor)];
  return <>
    <div className="po-stage" data-step={step} data-failed={failed || undefined} aria-hidden="true">
      <div className="po-doc" ref={doc}><strong>{number}</strong><span>v{version}</span><span className="po-seal" ref={seal}>Issued</span></div>
      <span className="po-trace" ref={trace} />
      <div className="po-vendor">{vendor}</div>
    </div>
    <table className="po-frames"><caption>Frames</caption><thead><tr><th scope="col">Step</th><th scope="col">What happens</th></tr></thead>
      <tbody>{frames.map((text, i) => <tr key={text} aria-current={current === i + 1 ? "step" : undefined}><td>{i + 1}</td><td>{text}</td></tr>)}</tbody></table>
  </>;
}
