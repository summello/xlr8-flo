import { test, expect } from 'playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { layoutTransitions, rawColours, undefinedVars, hasReducedMotionBlock } from './lint.mjs';

const dir = join(fileURLToPath(new URL('.', import.meta.url)), '..', 'project');
const css = readFileSync(join(dir, 'xlr8flo.css'), 'utf8');
const boards = readdirSync(dir).filter((f) => f.endsWith('.dc.html'));
const html = Object.fromEntries(boards.map((f) => [f, readFileSync(join(dir, f), 'utf8')]));
// ThemePanel is the token spec sheet, so it shows swatch hex values on purpose.
const appBoards = boards.filter((f) => f !== 'ThemePanel.dc.html');

test.describe('static gates, each proven against a planted violation', () => {
  test('no layout property is transitioned or keyframed', () => {
    expect(layoutTransitions(css)).toEqual([]);
    expect(layoutTransitions('.x { transition: width 200ms ease; }')).toEqual(['width']);
    expect(layoutTransitions('.x { transition: opacity 90ms, margin-left 90ms; }')).toEqual(['margin-left']);
    expect(layoutTransitions('@keyframes k { from { left: 0; } to { left: 9px; } }')).toEqual(['left', 'left']);
  });

  test('no colour literal in an app artboard', () => {
    for (const f of appBoards) expect(rawColours(html[f]), f).toEqual([]);
    expect(rawColours('<div style="color:#ff0000">x</div>')).toEqual(['#ff0000']);
    expect(rawColours('<i style="background: oklch(0.5 0.1 200)"></i>')).toEqual(['oklch(']);
  });

  test('every var(--token) resolves', () => {
    expect(undefinedVars(css, Object.values(html))).toEqual([]);
    expect(undefinedVars('.a { color: var(--nope); }', [])).toEqual(['--nope']);
  });

  test('a reduced-motion block collapses animation and transition duration', () => {
    expect(hasReducedMotionBlock(css)).toBe(true);
    expect(hasReducedMotionBlock('.a { color: red; }')).toBe(false);
    expect(hasReducedMotionBlock('@media (prefers-reduced-motion: reduce) {\n  .a { color: red; }\n}')).toBe(false);
  });
});
