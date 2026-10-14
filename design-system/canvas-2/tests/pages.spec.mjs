// The second canvas: its pages, that every board is listed and none overlap, and that no registered page is silently empty.
import { test, expect } from 'playwright/test';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { boardOverlaps } from './lint.mjs';

const root = join(fileURLToPath(new URL('.', import.meta.url)), '..');
const dir = join(root, 'project');
const canvas = JSON.parse(readFileSync(join(dir, 'canvas.json'), 'utf8'));
const onPage = (id) => Object.entries(canvas.boards).filter(([, b]) => b.page === id).map(([f]) => f);

// a page is registered properly when it has boards, or a brief file and at least one note on it
export const emptyPages = (c, hasBrief) => c.pages.filter((p) => !Object.values(c.boards).some((b) => b.page === p.id) && !(hasBrief(p.id) && Object.values(c.notes).some((n) => n.page === p.id))).map((p) => p.id);

test.describe('second canvas', () => {
  test('eight pages, in module order: the two built ones, then the six registered', () => {
    expect(canvas.pages.map((p) => p.name)).toEqual(['Common Screens', 'Vendors', 'Home and Reporting', 'Organization and Administration', 'Projects and Budget', 'Requisitions and Approvals', 'Assets and Warehouses', 'Cross-Cutting Surfaces']);
    expect(onPage('common-screens')).toHaveLength(27);
    expect(onPage('vendors')).toHaveLength(60);
    for (const [f, b] of Object.entries(canvas.boards)) expect(canvas.pages.map((p) => p.id), f).toContain(b.page);
  });

  test('every board file is listed, and no two boards on a page overlap', () => {
    const files = readdirSync(dir).filter((f) => f.endsWith('.dc.html')).sort();
    expect(Object.keys(canvas.boards).sort()).toEqual(files);
    expect([...canvas.order].sort()).toEqual(files);
    expect(boardOverlaps(canvas.boards)).toEqual([]);
    expect(boardOverlaps({ 'a.dc.html': { x: 0, y: 0, w: 100, h: 100, page: 'p' }, 'b.dc.html': { x: 50, y: 50, w: 100, h: 100, page: 'p' } })).toEqual(['a.dc.html / b.dc.html']);
  });

  test('a registered page has a brief and a note, and an empty page is caught', () => {
    const hasBrief = (id) => existsSync(join(root, `HANDOFF-${id}.md`));
    expect(emptyPages(canvas, hasBrief)).toEqual([]);
    // planted violations: a page with neither boards nor a brief, and one with a brief but no note
    expect(emptyPages({ pages: [{ id: 'x' }], boards: {}, notes: {} }, () => false)).toEqual(['x']);
    expect(emptyPages({ pages: [{ id: 'x' }], boards: {}, notes: {} }, () => true)).toEqual(['x']);
  });

  test('each brief names its screens with a story and requirement text', () => {
    for (const id of ['common-screens', 'home-reporting', 'org-admin', 'projects-budget', 'requisitions-approvals', 'assets-warehouses', 'cross-cutting']) {
      const t = readFileSync(join(root, `HANDOFF-${id}.md`), 'utf8');
      expect(t, id).toContain('## Screens');
      expect(t, id).toMatch(/E\d\d-S\d\d/);
      expect(t, id).not.toContain('text not found');
    }
  });
});
