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

// Headings are Title Case: first, last and post-colon words and every word of four letters or more are capitalised;
// short articles, conjunctions, prepositions and helping verbs stay lower case. Acronyms, codes and numbers are left alone.
const MINOR = new Set('a an the and but or for nor so yet at by in of on to up as vs via per is are was were be been am do does did has have had will would can could shall should may might must if off out'.split(' '));
export function titleCaseViolations(text) {
  const words = text.split(/\s+/).filter(Boolean);
  const bad = [];
  words.forEach((raw, i) => {
    const w = raw.replace(/^[("“]+|[)"”,.:;]+$/g, '');
    if (!/^[A-Za-z][a-z'’-]*$/.test(w)) return;
    const pinned = i === 0 || i === words.length - 1 || (i > 0 && /:$/.test(words[i - 1]));
    const upper = /^[A-Z]/.test(w);
    const minor = MINOR.has(w.toLowerCase());
    if ((pinned || !minor) && !upper) bad.push(w);
    if (minor && !pinned && upper) bad.push(w);
  });
  return bad;
}

// Boards on one canvas page must not overlap; returns the offending pairs.
export function boardOverlaps(boards) {
  const out = [], e = Object.entries(boards);
  for (const [a, A] of e) for (const [b, B] of e) {
    if (a >= b || A.page !== B.page) continue;
    if (!(A.x + A.w <= B.x || B.x + B.w <= A.x || A.y + A.h <= B.y || B.y + B.h <= A.y)) out.push(a + ' / ' + b);
  }
  return out;
}

// MASTER 6.4: a reduced-motion block that collapses animation and transition duration.
export function hasReducedMotionBlock(css) {
  const m = css.match(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{([\s\S]*?)\n\}/);
  return !!m && /animation-duration/.test(m[1]) && /transition-duration/.test(m[1]);
}

// Required fields (DECISIONS 64 to 66): a label that carries a Required tag must point at a control that is `required` (or `data-req`
// for a conditional rule), and a `required` control must be tagged in its label, or be a line cell with its own aria-label.
export function requiredMarkViolations(html) {
  const body = html.replace(/<script[\s\S]*?<\/script>/g, '');
  const out = [];
  const control = (id) => body.match(new RegExp(`<(?:input|select|textarea)\\b[^>]*\\bid="${id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[^>]*>`));
  const tagged = new Set();
  for (const m of body.matchAll(/<label\b[^>]*\bfor="([^"]+)"[^>]*>[\s\S]*?<\/label>/g)) {
    if (!m[0].includes('req-tag')) continue;
    tagged.add(m[1]);
    const c = control(m[1]);
    if (!c) { out.push(`label for ${m[1]} has a Required tag but no control`); continue; }
    if (!/\srequired[\s>]|\sdata-req[\s>]/.test(c[0])) out.push(`${m[1]} is tagged Required but the control is not required`);
  }
  for (const m of body.matchAll(/<(?:input|select|textarea)\b[^>]*>/g)) {
    if (!/\srequired[\s>]/.test(m[0]) || /\sdata-req[\s>]/.test(m[0])) continue;
    const id = (m[0].match(/\bid="([^"]+)"/) || [])[1];
    if (id && tagged.has(id)) continue;
    if (/\saria-label="/.test(m[0])) continue;                                   // a line cell, named by its aria-label
    const at = body.indexOf(m[0]);
    const after = body.slice(at + m[0].length, at + m[0].length + 700);
    if (/type="(?:checkbox|radio)"/.test(m[0]) && /^[^<]*(?:<[^>]*>[^<]*){0,3}?<span class="req-tag"/.test(after)) continue;
    out.push(`required control ${id || m[0].slice(0, 40)} has no Required tag`);
  }
  return out;
}
