"""Board chrome that is not data: favicon, tab bar, copy buttons, and the release runbook.

The runbook is the operator's checklist for shipping a milestone. Commands are real: they come
from infra/SETUP.md and the neondb-staging-rehearse skill, with the pull-request number and
branch filled in from the live status document so nothing has to be edited before pasting.
"""

# ruff: noqa: ISC004  (command strings are prose; implicit concatenation keeps them readable)
from __future__ import annotations

import html
from urllib.parse import quote

ORIGIN = "https://xlr8flo.summello.com"

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#4759D1"/>'
    '<path d="M9 23V9h13v3.2H12.4v2.9h8.2v3.1h-8.2V23z" fill="#fff"/>'
    '</svg>'
)
FAVICON = f'<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,{quote(_FAVICON_SVG)}">'

CSS = """
.tabs{display:flex;gap:4px;margin-top:20px;border-bottom:1px solid var(--border)}
.tabs button{appearance:none;background:none;border:0;border-bottom:2px solid transparent;
color:var(--fg2);font:inherit;font-weight:560;padding:10px 14px;cursor:pointer;margin-bottom:-1px}
.tabs button:hover{color:var(--fg)}
.tabs button[aria-selected=true]{color:var(--pl);border-bottom-color:var(--pl)}
.tabs button:focus-visible{outline:2px solid var(--ring);outline-offset:-2px;border-radius:6px}
.tab[hidden]{display:none}
.stale{color:var(--warn);background:var(--warnT);border-radius:6px;padding:2px 8px;font-size:12px}
.fresh{color:var(--fg3);font-size:12px}
.pill.ready{color:var(--warn);background:var(--warnT)}
.pill.inpr{color:var(--info);background:var(--infoT)}
.pill.cmt{color:var(--pl);background:var(--infoT)}
.pill.mrg{color:var(--cm);background:var(--okT)}
.pill.dep{color:var(--ok);background:var(--okT)}
.pill.train{color:var(--info);background:var(--infoT)}
.pill.rtm{color:var(--pl);background:var(--infoT)}
.note{font-size:11.5px;color:var(--fg3);margin-left:8px}
.rb{counter-reset:s;list-style:none;margin:0;padding:0;display:grid;gap:14px;grid-template-columns:minmax(0,1fr)}
.rb>li{min-width:0}
.rb>li{counter-increment:s;padding:16px 18px 16px 58px;position:relative}
.rb>li::before{content:counter(s);position:absolute;left:16px;top:15px;width:28px;height:28px;
border-radius:50%;background:var(--pl);color:#fff;font-weight:620;display:grid;place-items:center;
font-size:13px}
.rb h3{margin:0 0 4px;font-size:15px;font-weight:620;letter-spacing:-.01em}
.rb p{margin:4px 0 10px;color:var(--fg2);max-width:70ch}
.who{display:inline-block;font-size:10px;font-weight:560;letter-spacing:.07em;text-transform:uppercase;
border-radius:999px;padding:1px 8px;margin-left:8px;vertical-align:middle}
.who.you{color:var(--warn);background:var(--warnT)}.who.cl{color:var(--info);background:var(--infoT)}
.code{position:relative;margin:8px 0}
.code pre{margin:0;background:var(--sunken);border:1px solid var(--border);border-radius:8px;
padding:12px 46px 12px 14px;overflow-x:auto;font:12.5px/1.6 'JetBrains Mono',ui-monospace,monospace;
color:var(--fg)}
.copy{position:absolute;right:8px;top:8px;appearance:none;border:1px solid var(--bs);
background:var(--surface);color:var(--fg2);border-radius:6px;width:30px;height:28px;cursor:pointer;
display:grid;place-items:center}
.copy:hover{color:var(--fg)}.copy:focus-visible{outline:2px solid var(--ring)}
.copy.ok{color:var(--ok);border-color:var(--ok)}
.expect{font-size:12.5px;color:var(--fg2);border-left:3px solid var(--ok);padding:2px 0 2px 10px;
margin:8px 0 0}
.expect.bad{border-left-color:var(--dgr)}
.expect b{color:var(--fg)}
.rbhead{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap;margin-bottom:14px}
"""

SCRIPT = """
(function(){
var tabs=document.querySelectorAll('.tabs button'),panes=document.querySelectorAll('.tab');
function show(id){tabs.forEach(function(b){b.setAttribute('aria-selected',String(b.dataset.tab===id))});
panes.forEach(function(p){p.hidden=p.id!=='tab-'+id});
try{sessionStorage.setItem('flo.tab',id)}catch(e){}history.replaceState(null,'','#'+id)}
tabs.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab)})});
var start=(location.hash||'').slice(1);
try{start=start||sessionStorage.getItem('flo.tab')||''}catch(e){}
show(document.getElementById('tab-'+start)?start:'delivery');
document.querySelectorAll('.copy').forEach(function(b){b.addEventListener('click',function(){
var t=b.parentNode.querySelector('code').innerText;
function done(){b.classList.add('ok');b.setAttribute('aria-label','Copied');
setTimeout(function(){b.classList.remove('ok');b.setAttribute('aria-label','Copy command')},1400)}
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,fb)}else{fb()}
function fb(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();
try{document.execCommand('copy');done()}catch(e){}document.body.removeChild(a)}})});
var g=document.body.dataset.generated,el=document.getElementById('age');
function age(){if(!g||!el)return;var m=Math.round((Date.now()-Date.parse(g))/60000);
el.textContent=m<1?'updated just now':'updated '+m+' min ago';
el.className=m>10?'stale':'fresh';if(m>10)el.textContent+=' - is Mission Control or flo board --watch running?'}
age();setInterval(age,30000);
// reload every minute, keeping the tab and scroll position
setInterval(function(){try{sessionStorage.setItem('flo.scroll',String(window.scrollY))}catch(e){}location.reload()},60000);
try{var y=sessionStorage.getItem('flo.scroll');if(y){window.scrollTo(0,Number(y));sessionStorage.removeItem('flo.scroll')}}catch(e){}
})();
"""

_ICON = ('<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
         'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
         '<rect x="9" y="9" width="13" height="13" rx="2"/>'
         '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>')


def code(cmd):
    return (f'<div class="code"><pre><code>{html.escape(cmd)}</code></pre>'
            f'<button class="copy" type="button" aria-label="Copy command">{_ICON}</button></div>')


def _step(title, who, why, blocks, good, bad=None):
    tag = ('<span class="who you">You</span>' if who == "you"
           else '<span class="who cl">Claude</span>')
    body = "".join(code(b) for b in blocks)
    exp = f'<p class="expect"><b>Good:</b> {good}</p>'
    if bad:
        exp += f'<p class="expect bad"><b>If not:</b> {bad}</p>'
    return f'<li class="card"><h3>{html.escape(title)}{tag}</h3><p>{why}</p>{body}{exp}</li>'


def release_target(doc):
    """The milestone to release next: the earliest one not yet on main that has started."""
    ms = doc.get("milestones", {})
    order = list(ms)
    for want in ("ready_to_merge", "on_train"):
        for mid in order:
            if ms[mid]["state"] == want and (want == "ready_to_merge" or ms[mid].get("pr")):
                return mid, ms[mid]
    for mid in order:
        if ms[mid]["state"] != "deployed":
            return mid, ms[mid]
    return None, None


def runbook_html(doc):
    mid, m = release_target(doc)
    if m is None:
        return ('<section><h2>Release steps</h2><div class="card empty">'
                'Every milestone is deployed. Nothing to release.</div></section>')
    pr = (m.get("pr") or {}).get("number")
    n = str(pr) if pr else "<PR_NUMBER>"
    branch = m["branch"]
    state = {"ready_to_merge": "Ready to Merge", "on_train": "On Train"}.get(m["state"], m["state"])
    note = f' - {html.escape(m["note"])}' if m.get("note") else ""
    steps = [
        _step("Confirm it is ready", "you",
              "The board should say <b>Ready to Merge</b> for this milestone, and the pull "
              "request should be green and mergeable.",
              [f"gh pr checks {n}", f"gh pr view {n} --json isDraft,mergeStateStatus,mergeable"],
              "every check passes; <code>mergeStateStatus</code> is <code>CLEAN</code>.",
              "a check is red or pending: do not merge. Ask Claude to look at it."),
        _step("Check the release reviews", "you",
              "Two independent reviewers and Claude review the whole milestone. A blocker "
              "stops the release.",
              [f'grep -H \'"verdict"\' reviews/{mid}.*.json'],
              "every line says <code>approve</code>.",
              "<code>changes_requested</code> or a missing file: ask Claude for the status."),
        _step("Rehearse the database changes on Neon staging", "you",
              "Runs the new migrations on a throwaway copy of production first. In Claude, say "
              "<b>rehearse the migrations on Neon staging</b> and it walks you through every "
              "step. The commands below are the core of it; create the <code>staging</code> "
              "branch from production in the Neon console first, with pooling on.",
              ['security add-generic-password -U -a "$USER" -s FLO_STAGING_DATABASE_URL -w',
               'cd ~/Workbench/xlr8flo/apps/api\n'
               'export MIGRATIONS_DIR="$PWD/../../migrations"\n'
               'uv run python -m flo.kernel.migrate --check; echo "exit code: $?"',
               'for run in apply "apply again"; do\n  echo "== $run"\n'
               '  DATABASE_URL="$(security find-generic-password -a "$USER" -s '
               'FLO_STAGING_DATABASE_URL -w)" \\\n    uv run python -m flo.kernel.migrate; '
               'echo "exit code: $?"\ndone'],
              "the check and both applies print <code>exit code: 0</code>, and the second apply "
              "changes nothing.",
              "any other number: stop, send Claude the last 15 lines, and do not merge. Delete "
              "the staging branch when finished."),
        _step("Confirm the open decisions", "you",
              "Design choices made on your behalf are listed for confirmation. Unconfirmed "
              "ones are still reversible by a follow-up story, but read them before shipping.",
              ["ls docs/*decisions*.md", "less docs/m1-decisions-to-confirm.md"],
              "you have read the list, or decided it can wait."),
        _step("Take the pull request out of draft", "you",
              "Only needed if GitHub shows it as a draft.",
              [f"gh pr ready {n}"], "the command prints the pull request as ready for review."),
        _step("Merge", "you",
              "Merging deploys and migrates production. Use a <b>merge commit</b>: never squash "
              "or rebase a milestone, the one-commit-per-story history is the record.",
              [f"gh pr merge {n} --merge"],
              "GitHub says the pull request was merged. A deploy starts on its own.",
              "it refuses: a required check is not green yet. Do not use <code>--admin</code>."),
        _step("Watch the deploy", "you",
              "Migrations run first, then the API, then the website and edge Worker. The run "
              "ends with the <code>deploy</code> job.",
              ["gh run list --branch main --limit 3",
               "gh run watch $(gh run list --branch main --limit 1 --json databaseId "
               "--jq '.[0].databaseId')"],
              "every job, including <code>deploy</code>, finishes with a tick.",
              "<code>deploy</code> fails: tell Claude. The API may already be live, so say "
              "which step failed (<code>gh run view &lt;id&gt; --log-failed</code>)."),
        _step("Smoke test production", "you",
              "Three quick checks that the service is up and serving the new website.",
              [f"curl -s -o /dev/null -w '%{{http_code}}\\n' {ORIGIN}/readyz",
               f"curl -s -o /dev/null -w '%{{http_code}}\\n' {ORIGIN}/api/readyz",
               f"open {ORIGIN}"],
              "<code>200</code> twice, then the sign-in page. Sign in and open a project "
              "dashboard."),
        _step("Tell Claude it is merged", "claude",
              "Claude confirms the deploy, merges <code>main</code> into the next milestone "
              "branch, updates the project memory and refreshes this board. Milestone state "
              "turns to Deployed.",
              ["git fetch origin && git merge origin/main"],
              "this board shows the milestone as <b>Deployed</b> and every story as "
              "Deployed. The command is for whoever owns the next milestone branch."),
        _step("If something is wrong: roll back", "you",
              "Moves API traffic to the previous revision. It does not undo database changes, "
              "so every production migration must stay compatible with the previous revision. "
              "For the website or Worker use Cloudflare, Workers &amp; Pages, flo-web, "
              "Deployments, Rollback.",
              ["gcloud run revisions list --service=flo-api --region=us-central1 "
               "--sort-by='~metadata.creationTimestamp' "
               "--format='table(metadata.name,status.conditions[0].status,metadata.creationTimestamp)'",
               "gcloud run services update-traffic flo-api --region=us-central1 "
               "--to-revisions=PREVIOUS_REVISION=100"],
              "<code>/readyz</code> returns 200 on the previous revision."),
    ]
    head = (f'<div class="rbhead"><h2 style="margin:0">Release {html.escape(mid)} '
            f'&middot; {html.escape(m["name"])}</h2>'
            f'<span class="pill {"rtm" if m["state"] == "ready_to_merge" else "train"}">'
            f'<i></i>{state}</span><span class="note">'
            f'{("PR #" + str(pr)) if pr else "no pull request yet"} &middot; '
            f'<span class="mono">{html.escape(branch)}</span>{note}</span></div>')
    intro = ('<p class="hint" style="padding:0 0 14px">Do these in order. Each box has a copy '
             'button. <span class="who you">You</span> steps are yours; '
             '<span class="who cl">Claude</span> steps Claude does. Commands assume the repo '
             'is at <span class="mono">~/Workbench/xlr8flo</span>.</p>')
    return f'<section>{head}{intro}<ol class="rb">{"".join(steps)}</ol></section>'
