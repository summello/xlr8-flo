import { test, expect } from 'playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { layoutTransitions, rawColours, undefinedVars, hasReducedMotionBlock, titleCaseViolations, requiredMarkViolations } from './lint.mjs';

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

  test('title case: the checker, then every static title in the sources', () => {
    for (const ok of ['Spend vs Budget', 'Actual vs Allocated by Business Unit', 'Cumulative Spend Over Time', 'Budget is Inversely Proportional to Frequency',
      'Tags (2.4, 7.2): Outline, Pill Radius, Dot, Never an Icon', 'Seen On', 'XLR8 FLO Design System', 'Good Afternoon, Sonam', 'Used of Allocated', 'Loading: Skeleton Rows at Exact Final Height'])
      expect(titleCaseViolations(ok), ok).toEqual([]);
    expect(titleCaseViolations('Spend vs budget')).toEqual(['budget']);
    expect(titleCaseViolations('cumulative Spend Over Time')).toEqual(['cumulative']);
    expect(titleCaseViolations('Budget Is Inversely Proportional')).toEqual(['Is']);
    expect(titleCaseViolations('Used Of Allocated')).toEqual(['Of']);
    expect(titleCaseViolations('Status: closed Vocabulary')).toEqual(['closed']);
    const titles = [
      ...boards.map((f) => html[f].match(/<title>([^<]+)<\/title>/)[1]),
      ...Object.values(JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8')).boards).map((b) => b.title),
      ...Object.values(JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8')).notes).map((n) => n.text),
    ];
    for (const t of titles) expect(titleCaseViolations(t), t).toEqual([]);
  });

  test('a reduced-motion block collapses animation and transition duration', () => {
    expect(hasReducedMotionBlock(css)).toBe(true);
    expect(hasReducedMotionBlock('.a { color: red; }')).toBe(false);
    expect(hasReducedMotionBlock('@media (prefers-reduced-motion: reduce) {\n  .a { color: red; }\n}')).toBe(false);
  });
});

test.describe('required fields are marked, and the mark matches the control', () => {
  test('every app board: each Required tag points at a required control and each required control is tagged', () => {
    for (const f of appBoards) expect(requiredMarkViolations(html[f]), f).toEqual([]);
  });
  test('the gate fails on planted violations', () => {
    const tag = '<span class="req-tag"><span class="rq-need">Required</span></span>';
    expect(requiredMarkViolations(`<label for="a">A${tag}</label><input id="a">`)).toEqual(['a is tagged Required but the control is not required']);
    expect(requiredMarkViolations('<label for="a">A</label><input required id="a">')).toEqual(['required control a has no Required tag']);
    expect(requiredMarkViolations(`<label for="a">A${tag}</label><input required id="a">`)).toEqual([]);
    expect(requiredMarkViolations(`<label for="a">A${tag}</label><textarea data-req id="a"></textarea>`)).toEqual([]);
    expect(requiredMarkViolations('<input required aria-label="Quantity, line 1">')).toEqual([]);
    expect(requiredMarkViolations(`<label><input required type="checkbox" id="t">I agree${tag}</label>`)).toEqual([]);
  });
});
