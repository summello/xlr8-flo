// Static checks for the canvas sources. Each returns findings; lint.spec.mjs proves each one fails on a planted violation.

// Layout properties may never be animated (MASTER 6.1: transform and opacity only).
const LAYOUT = /^(width|height|min-|max-|top|left|right|bottom|margin|padding|inset|flex-basis|gap|font-size|border-width)/;

export function layoutTransitions(css) {
  const hits = [];
  for (const m of css.matchAll(/transition(?:-property)?\s*:\s*([^;}]+)/g)) {
    for (const part of m[1].split(/,(?![^(]*\))/)) {
      const prop = part.trim().split(/\s+/)[0];
      if (LAYOUT.test(prop)) hits.push(prop);
    }
  }
  for (const k of css.matchAll(/@keyframes\s+[\w-]+\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g)) {
    for (const d of k[1].matchAll(/([\w-]+)\s*:/g)) if (LAYOUT.test(d[1])) hits.push(d[1]);
  }
  return hits;
}

// No colour literal outside the token file (MASTER 2.7). Pass an artboard's html.
export function rawColours(html) {
  const body = html.replace(/<script[\s\S]*?<\/script>/g, '');
  return [...body.matchAll(/#[0-9a-fA-F]{3,8}\b|\b(?:oklch|rgba?|hsla?)\(/g)].map((m) => m[0]);
}

// Every var(--x) must resolve: defined in the token file or set inline on an element.
export function undefinedVars(css, htmls) {
  const defined = new Set([...[css, ...htmls].join('\n').matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]));
  const used = new Set([...[css, ...htmls].join('\n').matchAll(/var\((--[\w-]+)\s*\)/g)].map((m) => m[1]));
  return [...used].filter((v) => !defined.has(v));
}

// MASTER 6.4: a reduced-motion block that collapses animation and transition duration.
export function hasReducedMotionBlock(css) {
  const m = css.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/);
  return !!m && /animation-duration/.test(m[1]) && /transition-duration/.test(m[1]);
}
