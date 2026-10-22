# Sonnet round churn: is it a Sonnet problem?

Finding for E23-S02, which decides the shape of E23-S04 (the `author_priority` reorder).
Data: `agents/scorecard.json` as of commit `39644ac` (22 Oct 2026), 106 merged stories.

## Answer

**Sonnet's extra rounds are concentrated in gated work, but this data cannot say the cause is
Sonnet.** Four confounds are open, so the right recommendation is a bounded A/B test, not a
change to Sonnet's allowed work.

## The numbers

```
window: records from E09-S06 (index 76) on; 30 records

1. Author, whole history
  codex              n=67  mean=1.04  clean=97%
  opencode-nemotron  n=10  mean=1.00  clean=100%
  sonnet             n=29  mean=1.72  clean=59%

1b. Author, window only
  codex              n=1   mean=—     clean=—
  sonnet             n=29  mean=1.72  clean=59%

2. Sonnet by kind
  api          n=1   mean=—     clean=—
  chore        n=3   mean=1.00  clean=100%
  crud         n=4   mean=1.00  clean=100%
  engine       n=7   mean=1.86  clean=43%
  ledger       n=3   mean=2.67  clean=0%
  security     n=2   mean=—     clean=—
  test         n=1   mean=—     clean=—
  ui           n=8   mean=1.62  clean=62%

3. Sonnet by tag (a story counts once per tag)
  (none)       n=8   mean=1.25  clean=88%
  auth         n=6   mean=2.33  clean=33%
  concurrency  n=2   mean=—     clean=—
  migration    n=4   mean=3.00  clean=0%
  money        n=8   mean=2.25  clean=25%
  security     n=3   mean=2.00  clean=67%
  ui           n=12  mean=1.75  clean=58%

4. Sonnet, gated vs ungated
  gated    n=12  mean=2.17  clean=33%
  ungated  n=17  mean=1.41  clean=76%
  gated mean - ungated mean = 0.75; gated n = 12; rule result: CONCENTRATED

5. Sonnet, rounds against what the reviews recorded
  rounds = 1   n=17  findings=15  blockers=13  dismissed=1
  rounds >= 2  n=12  findings=0   blockers=0   dismissed=0
  rounds distribution: [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4]
```

Decision rule (set in the packet before the numbers were seen): *concentrated* if
`gated mean - ungated mean >= 0.5` and gated n >= 8. Result: **concentrated** (0.75, n = 12).
Gated means any of money, auth, security, migration, concurrency. A dash means fewer than 3 records
(the scorecard's `min_sample`).

## Why this is not yet a verdict on Sonnet

| # | Confound | Status | Evidence |
|---|---|---|---|
| 1 | **No like-for-like comparison with Codex.** Codex's 1.04 comes from M0 and M1, when review was Opus alone (25 Aug and 10 Oct arrangements) and many stories were foundations under a sole-author contract. Since 13 Oct every Sonnet story is reviewed by Codex, and gated ones by a fresh Opus too. In the window, 29 of 30 records are Sonnet and 1 is Codex (table 1b). | **Open** | Author and review regime move together. Nothing here separates them. |
| 2 | **The scorecard's `rounds` is not written where the rounds happen.** In this repository the only code that has ever raised `task.json`'s `rounds` is `flo escalate`, and the scorecard records that value plus 1. The supervisor never writes `task.json`, and no skill or script does either (checked 22 Oct 2026 across the history of `agents/scripts` and `.claude/skills/xlr8flo-*`). Yet seven Sonnet stories show 3 or 4 rounds. The six records after 21 Oct that also carry observed review-run counts all say `rounds: 1` while their reviewers ran 2, 2, 2, 1, 7 and 6 times, and every Sonnet record above 1 predates that change. The value is most likely set by hand in planner sessions (an inference). Table 5 fits: stories with `rounds >= 2` hold no recorded findings, and one-round stories hold 13 blockers. | **Open for the numbers above; fixed going forward by E23-S13** | New records take `rounds` from the round files the supervisor counts and carry `rounds_basis: "round_files"`. The A/B must read those records only. |
| 3 | **Not every round is the author's.** `project-memory.md` (22 Oct) records three Codex reviews that hit the command cap and left `approve, ran_tests: false`; each cost another review. Whether those were counted as rounds depends on the unknown writer in row 2. | **Open** | Cannot be separated from committed data. |
| 4 | **Small cells.** Most kind and tag cells have n < 8. | **Partly** | The gated vs ungated split (n = 12 and 17) is the only one with enough records to read. |

## Recommendation

Rule result *concentrated*, confounds 1 to 3 *open*, so the row that applies is:

> **Do not narrow Sonnet yet.** Run a bounded A/B: the next 6 gated stories go to Codex as author;
> compare their rounds with Sonnet's 2.17 under the same Codex-plus-Opus review. E23-S04 ships the
> reorder for ungated work only.

Why not narrow now: it would move gated work (12 of Sonnet's 29 stories) to Codex, whose weekly
budget is 18 stories (`weekly_story_budget` in `agents.yaml`), on evidence that cannot tell author
quality from review strictness. Six stories is a third of one week's Codex budget, enough to
see whether Codex-authored gated stories also take about two rounds under this review regime.

What settles it: E23-S13 derives `rounds` from the round files the supervisor already counts, does not charge a round that sent no itemised finding (`void_rounds`), and exposes `review_runs`, so reviewer failures can be told from author rework. `SCORECARD.md` marks every older record as untrustworthy and rewrites none. Until a gated Sonnet or Codex story has a record with `rounds_basis`, there is nothing to compare.

## What this means for E23-S04

The reorder puts nemotron first. Nemotron cannot take gated work (`avoid_tags`), so **the reorder
does not touch the stories where Sonnet churns.** It changes who takes ungated crud, ui, api, chore,
docs and test work of size S or M, where Sonnet is already at 1.41 rounds and 76% clean. The case for
it is cost and capacity, not a demonstrated quality gain. See the amendment in `design/E23-S04.md`.

## Reproduce

Run from the repository root; stdlib only. It prints the numbers above for the committed
`agents/scorecard.json`.

```python
import json
import statistics

GATED = {"money", "auth", "security", "migration", "concurrency"}
data = json.load(open("agents/scorecard.json"))
stories, floor = data["stories"], data["min_sample"]


def cell(group):
    n = len(group)
    if n < floor:
        return f"n={n:<3} mean=—     clean=—"
    rounds = [s["rounds"] for s in group]
    clean = sum(1 for s in group if s["rounds"] == 1 and not s["gate_failures"])
    return f"n={n:<3} mean={statistics.mean(rounds):.2f}  clean={100 * clean / n:.0f}%"


def gated(s):
    return bool(set(s["tags"]) & GATED)


first = next(i for i, s in enumerate(stories) if s["author"] == "sonnet")
window = stories[first:]
print(f"window: records from {stories[first]['story']} (index {first}) on; {len(window)} records")

print("\n1. Author, whole history")
for a in sorted({s["author"] for s in stories}):
    print(f"  {a:<18} {cell([s for s in stories if s['author'] == a])}")
print("\n1b. Author, window only")
for a in sorted({s["author"] for s in window}):
    print(f"  {a:<18} {cell([s for s in window if s['author'] == a])}")

son = [s for s in stories if s["author"] == "sonnet"]
print("\n2. Sonnet by kind")
for k in sorted({s["kind"] for s in son}):
    print(f"  {k:<12} {cell([s for s in son if s['kind'] == k])}")
print("\n3. Sonnet by tag (a story counts once per tag)")
for t in sorted({t for s in son for t in (s["tags"] or ['(none)'])}):
    print(f"  {t:<12} {cell([s for s in son if t in (s['tags'] or ['(none)'])])}")

g = [s for s in son if gated(s)]
u = [s for s in son if not gated(s)]
print("\n4. Sonnet, gated vs ungated")
print(f"  gated    {cell(g)}")
print(f"  ungated  {cell(u)}")
diff = statistics.mean(s["rounds"] for s in g) - statistics.mean(s["rounds"] for s in u)
verdict = "CONCENTRATED" if diff >= 0.5 and len(g) >= 8 else "SPREAD"
print(f"  gated mean - ungated mean = {diff:.2f}; gated n = {len(g)}; rule result: {verdict}")

print("\n5. Sonnet, rounds against what the reviews recorded")
for label, grp in (("rounds = 1", [s for s in son if s["rounds"] == 1]),
                   ("rounds >= 2", [s for s in son if s["rounds"] >= 2])):
    reviews = [r for s in grp for r in s["review"].values()]
    print(f"  {label:<12} n={len(grp):<3} findings={sum(r['findings'] for r in reviews):<3} "
          f"blockers={sum(r['blockers'] for r in reviews):<3} "
          f"dismissed={sum(r['blockers_dismissed'] for r in reviews)}")
print(f"  rounds distribution: {sorted(s['rounds'] for s in son)}")
```
