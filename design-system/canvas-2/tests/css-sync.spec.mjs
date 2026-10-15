// xlr8flo.css exists in both canvases; the first is the source. They must not drift.
import { test, expect } from 'playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = join(fileURLToPath(new URL('.', import.meta.url)), '..');
export const drifted = (a, b) => a !== b;

test('canvas-2 xlr8flo.css is byte-identical to the first canvas', () => {
  const mine = readFileSync(join(here, 'project', 'xlr8flo.css'), 'utf8');
  const first = readFileSync(join(here, '..', 'canvas', 'project', 'xlr8flo.css'), 'utf8');
  expect(drifted(mine, first), 'copy ../canvas/project/xlr8flo.css over this one').toBe(false);
  expect(drifted(mine + '\n.x { color: red; }', first)).toBe(true);       // planted drift is detected
});
