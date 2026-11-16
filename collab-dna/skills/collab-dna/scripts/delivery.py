#!/usr/bin/env python3
"""Measure how a project delivers, from its history, with the same
definitions every time. The project audit reads sessions; this reads what
those sessions shipped, so "did the way of working get better" has a
second answer that is not the auditor's opinion. Read-only.

Usage:
  delivery.py [--project DIR] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
              [--branch NAME] [--fix-pattern REGEX] [--out FILE]
  delivery.py --prs [--repo OWNER/NAME] [--prod BRANCH] [--integration BRANCH]
              [--plumbing REGEX] [--limit N] [same window and output options]

Two modes, because the rows mean different things:

  commits (default)  For a project where changes land straight on one
                     shared branch. Local git history only, no network.
  --prs              For a project where changes travel through pull
                     requests to a protected branch. Asks the host
                     through `gh`, which must be signed in.

Defaults: the current folder, the last 30 days, the branch checked out
(commits) or `main` (--prs). Output is a Markdown table.

What it cannot see is printed as rows that say so: time to restore and
incident count come from the project's incident log, and nothing here
measures friction. A hotfix is detected by name (--fix-pattern, default
hotfix|revert|rollback, in the commit subject, the pull request title or
its branch); a project that names them differently passes its own
pattern. People appear as a count only. Individual output is never the
point of this table.

No dependencies beyond the standard library.
"""
import argparse, json, os, re, statistics, subprocess, sys
from datetime import date, datetime, timedelta, timezone


def run(cmd, cwd=None, timeout=180):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def fmt_h(x):
    if x is None:
        return "n/a"
    return f"{x:.1f} h" if x < 48 else f"{x / 24:.1f} d"


def dist(xs):
    if not xs:
        return "no data"
    return f"median {fmt_h(pct(xs, .5))}, p75 {fmt_h(pct(xs, .75))}, p90 {fmt_h(pct(xs, .9))} (n={len(xs)})"


def window(a):
    until = a.until or date.today().isoformat()
    since = a.since or (date.fromisoformat(until) - timedelta(days=30)).isoformat()
    lo = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
    hi = datetime.fromisoformat(until).replace(tzinfo=timezone.utc) + timedelta(days=1)
    weeks = max((hi - lo).total_seconds() / (7 * 86400), 1e-9)
    return since, until, lo, hi, weeks


UNSEEN = [
    ("Time to restore", "not in this history: from the incident log, noticed to fixed, per incident"),
    ("Incidents", "not in this history: count from the incident log"),
]


def table(title, rows, notes):
    out = [title, "", "| Measure | Value |", "|---|---|"]
    out += [f"| {a} | {b} |" for a, b in rows]
    out += [""] + notes
    return "\n".join(out) + "\n"


def commits_mode(a):
    project = os.path.abspath(a.project)
    rc, _, _ = run(["git", "-C", project, "rev-parse", "--is-inside-work-tree"])
    if rc != 0:
        sys.exit(f"not a git repository: {project}")
    branch = a.branch or run(["git", "-C", project, "rev-parse", "--abbrev-ref", "HEAD"])[1].strip()
    since, until, lo, hi, weeks = window(a)
    rc, raw, err = run(["git", "-C", project, "log", branch, "--no-merges",
                        f"--since={since} 00:00", f"--until={until} 23:59:59",
                        "--format=%x1e%ct%x1f%ae%x1f%s", "--name-only"])
    if rc != 0:
        sys.exit(f"git log failed: {err.strip()[:200]}")
    commits = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        head, _, files = rec.partition("\n")
        ts, author, subject = (head.split("\x1f") + ["", ""])[:3]
        commits.append((int(ts), author, subject, [f for f in files.split("\n") if f.strip()]))
    commits.sort()
    n = len(commits)
    fix = re.compile(a.fix_pattern, re.I)
    fixes = [c for c in commits if fix.search(c[2])]
    testish = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|\.(test|spec)\.[a-z]+$", re.I)
    with_tests = [c for c in commits if any(testish.search(f) for f in c[3])]
    sizes = [len(c[3]) for c in commits if c[3]]
    gaps = [(b[0] - a_[0]) / 3600 for a_, b in zip(commits, commits[1:])]
    authors = {c[1] for c in commits}
    days = {datetime.fromtimestamp(c[0], timezone.utc).date() for c in commits}
    share = lambda k: f"{k} of {n} ({(100 * k / n) if n else 0:.0f}%)"
    rows = [
        (f"Commits to `{branch}`", f"{n} in {weeks:.1f} weeks = {n / weeks:.1f}/week, on {len(days)} distinct days, "
                                   f"by {len(authors)} {'person' if len(authors) == 1 else 'people'}"),
        ("Batch size, files per commit", f"median {statistics.median(sizes):g}, p90 {pct(sizes, .9):.1f}" if sizes else "n/a"),
        ("Commits named as a fix-after-failure", share(len(fixes))),
        ("Commits that touch a test file", share(len(with_tests))),
        ("Time between commits", f"median {fmt_h(pct(gaps, .5))}, longest quiet stretch {fmt_h(max(gaps) if gaps else None)}"),
    ] + UNSEEN
    notes = [f"Fix-after-failure is any commit whose subject matches `{a.fix_pattern}`. An ordinary `fix:` commit is "
             "normal work, not a failure.",
             "Read share and count together, and compare a window only with an equal-length window of the same project."]
    return table(f"Delivery, {since} to {until}, branch `{branch}` (direct commits, local history).", rows, notes)


def prs_mode(a):
    repo = a.repo
    if not repo:
        rc, out, _ = run(["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd=os.path.abspath(a.project))
        repo = out.strip()
        if rc != 0 or not repo:
            sys.exit("could not tell which repository this is; pass --repo OWNER/NAME (and check `gh auth status`)")
    since, until, lo, hi, weeks = window(a)
    prod, integ = a.prod, a.integration
    fields = "number,title,createdAt,mergedAt,headRefName,baseRefName"

    def merged(base):
        rc, out, err = run(["gh", "pr", "list", "-R", repo, "--base", base, "--state", "merged",
                            "--limit", str(a.limit), "--json", fields])
        if rc != 0:
            sys.exit(f"gh pr list failed for {repo}: {err.strip()[:200]}")
        return [p for p in json.loads(out or "[]") if p.get("mergedAt")]

    ts = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    hours = lambda x, y: (ts(y) - ts(x)).total_seconds() / 3600
    inwin = lambda p: lo <= ts(p["mergedAt"]) < hi
    all_prod = merged(prod)
    all_int = merged(integ) if integ else []
    prods = [p for p in all_prod if inwin(p)]
    ints = [p for p in all_int if inwin(p)]
    rc, out, _ = run(["gh", "pr", "list", "-R", repo, "--state", "open", "--limit", str(a.limit), "--json", "number,createdAt,isDraft"])
    opens = json.loads(out or "[]") if rc == 0 else []
    plumbing = re.compile(a.plumbing, re.I) if a.plumbing else None
    is_plumbing = lambda p: bool(plumbing and plumbing.search(p["headRefName"]))

    rows = [("Deploy frequency", f"{len(prods)} merges to `{prod}` in {weeks:.1f} weeks = {len(prods) / weeks:.1f}/week")]
    if integ:
        promos = sorted(ts(p["mergedAt"]) for p in all_prod if p["headRefName"] == integ)
        live = [hours(p["createdAt"], p["mergedAt"]) for p in prods if p["headRefName"] != integ and not is_plumbing(p)]
        waits = []
        for p in ints:
            if is_plumbing(p):
                continue
            nxt = next((x for x in promos if x >= ts(p["mergedAt"])), None)
            if nxt:
                waits.append((nxt - ts(p["mergedAt"])).total_seconds() / 3600)
                live.append((nxt - ts(p["createdAt"])).total_seconds() / 3600)
        rows.append((f"Lead time, opened to merged into `{integ}`", dist([hours(p["createdAt"], p["mergedAt"]) for p in ints])))
        rows.append((f"Wait, `{integ}` to the next promotion to `{prod}`", dist(waits)))
        rows.append(("Lead time, opened to live in production (every change)", dist(live)))
    else:
        rows.append((f"Lead time, opened to merged into `{prod}`", dist([hours(p["createdAt"], p["mergedAt"]) for p in prods])))
    fix = re.compile(a.fix_pattern, re.I)
    fails = [p for p in prods if fix.search(p["title"]) or fix.search(p["headRefName"])]
    line = (f"{len(fails)} of {len(prods)} production changes ({100 * len(fails) / len(prods):.0f}%), "
            f"{len(fails) / weeks:.1f}/week") if prods else "no data"
    if integ and prods:
        seen = {p["headRefName"] for p in all_int if ts(p["mergedAt"]) < hi}
        skipped = [p for p in prods if p["headRefName"] != integ and p["headRefName"] not in seen]
        line += f"; {100 * len(skipped) / len(prods):.0f}% of production changes did not pass through `{integ}`"
    rows.append(("Change failure, by name", line))
    now = datetime.now(timezone.utc)
    ages = sorted((now - ts(o["createdAt"])).total_seconds() / 3600 for o in opens)
    rows.append(("Open pull requests now", f"{len(opens)} ({sum(1 for o in opens if o.get('isDraft'))} drafts)"
                 + (f"; age median {fmt_h(pct(ages, .5))}, oldest {fmt_h(ages[-1])}" if ages else "")))
    rows += UNSEEN
    notes = [f"Change failure counts a production change whose title or branch matches `{a.fix_pattern}`. It is a proxy.",
             "Read share and count together: a falling share can sit beside a rising count when volume grows.",
             "Compare a window only with an equal-length window of the same project, and say when the way work travels changed "
             "(a staging branch added or dropped changes what every row means)."]
    if len(all_prod) >= a.limit:
        notes.append(f"The host returned {a.limit} merged pull requests, the limit; raise --limit or narrow the window.")
    return table(f"Delivery, {since} to {until}, `{repo}` (pull requests into `{prod}`).", rows, notes)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--branch", help="commits mode: branch to read (default: the one checked out)")
    ap.add_argument("--fix-pattern", default="hotfix|revert|rollback")
    ap.add_argument("--prs", action="store_true", help="read pull request history through gh instead of local commits")
    ap.add_argument("--repo", help="--prs: OWNER/NAME (default: the repository of --project)")
    ap.add_argument("--prod", default="main", help="--prs: the branch production deploys from")
    ap.add_argument("--integration", help="--prs: a staging branch changes pass through, if any")
    ap.add_argument("--plumbing", help="--prs: regex for branches that only sync branches and are not changes")
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--out")
    a = ap.parse_args()
    text = prs_mode(a) if a.prs else commits_mode(a)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        print(a.out)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
