# design/

One packet per story: `design/<STORY_ID>.md`, written by Opus in a batched pass per milestone
(`flo design <MILESTONE>`). Format and rules: `agents/plan.md` §3 and `agents/claude.md` §1.

**These are tracked.** They are the most expensive artifact in the repository — Opus runs on the
scarcest plan in the fleet, and a packet is the specification a story is judged against at review
and at gate time. Tracking them means:

- every story worktree contains its own packet, so an agent reads it locally rather than reaching
  back to the repository root
- a reviewer can diff the implementation against the specification as of the commit it was built from
- a defect found months later has the spec that produced it, not a reconstruction
- losing the machine does not lose the milestone's design pass

`flo start` refuses a story with no packet. A packet that needs an undecided business rule marks its
story `blocked` and surfaces the question rather than guessing.
