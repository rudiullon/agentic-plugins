#!/usr/bin/env python3
"""Inventory everything that shapes a Claude Code session before the human
types: models and effort, permission mode and rules, hooks, every
CLAUDE.md in scope, project rules, skills, commands, agents, plugins,
MCP servers, the project's memory, and the repository's own gates (git
hooks, CI, instruction files below the root, what the repo carries).
Read-only and local. Secrets are masked.

Usage:
  inspect_setup.py [--project DIR] [--out FILE]

Prints markdown to stdout (or --out). The setup audit reads this, not
the files themselves, so the same report can be produced on any
machine and pasted without leaking credentials.
"""
import argparse, json, os, re, subprocess, sys, glob

HOME = os.path.expanduser("~")
SECRET = re.compile(r"(?i)(token|secret|key|password|passwd|auth|credential|cookie|bearer)")
INLINE_SECRET = re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|xox[a-z]-[A-Za-z0-9-]{8,}|[A-Za-z0-9_-]{32,})\b")


def mask(obj, key=""):
    if isinstance(obj, dict):
        return {k: ("<masked>" if SECRET.search(k) and not isinstance(v, (dict, list)) else mask(v, k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v, key) for v in obj]
    if isinstance(obj, str):
        if SECRET.search(key):
            return "<masked>"
        return INLINE_SECRET.sub("<masked>", obj)
    return obj


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def read_text(path, limit=400):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    n = len(lines)
    body = "\n".join(lines[:limit])
    body = INLINE_SECRET.sub("<masked>", body)
    if n > limit:
        body += f"\n… [{n - limit} more lines]"
    return n, body


def frontmatter(path):
    t = read_text(path, 40)
    if not t:
        return {}
    body = t[1]
    if not body.startswith("---"):
        return {}
    out = {}
    key = None
    for line in body.split("\n")[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            key = m.group(1)
            val = m.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val.strip('"').strip("'")
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def section(title):
    return [f"\n## {title}\n"]


def fenced(text, lang=""):
    return [f"```{lang}", text, "```"]


def plugin_entries(gs, project):
    ip = load_json(os.path.join(HOME, ".claude", "plugins", "installed_plugins.json")) or {}
    plugins = ip.get("plugins") or {}
    ep = gs.get("enabledPlugins") or {}
    items = plugins.items() if isinstance(plugins, dict) else [(p.get("name", "?"), [p]) for p in plugins]
    out = []
    for name, entries in items:
        entries = entries if isinstance(entries, list) else [entries]
        for e in entries:
            scope = e.get("scope", "?")
            if scope == "project" and e.get("projectPath") not in (None, project):
                continue
            path = e.get("installPath", "")
            state = ep.get(name)
            state = "on" if state else ("off" if state is False else "installed, not listed")
            hooks = os.path.exists(os.path.join(path, "hooks", "hooks.json")) or os.path.exists(os.path.join(path, "hooks.json"))
            mcp = load_json(os.path.join(path, ".mcp.json")) or {}
            out.append(dict(name=name, version=e.get("version", "?"), scope=scope, state=state, path=path,
                            skills=glob.glob(os.path.join(path, "skills", "*", "SKILL.md")),
                            cmds=glob.glob(os.path.join(path, "commands", "*.md")),
                            hooks=hooks, mcp=list((mcp.get("mcpServers") or {}).keys())))
    return out


def git(root, *args, timeout=20):
    try:
        r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def workflow_summary(path):
    """Triggers and run commands of one CI workflow, without a YAML parser."""
    t = read_text(path, 400)
    if not t:
        return [], []
    lines = t[1].split("\n")
    triggers, runs = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(?:on|\"on\"|'on'):\s*(.*)$", line)
        if m:
            if m.group(1).strip():
                triggers.append(m.group(1).strip())
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                if re.match(r"^\s{2}[\w-]+:", lines[j]) or re.match(r"^\s{2}-\s*\w", lines[j]):
                    triggers.append(lines[j].strip().rstrip(":").lstrip("- "))
                j += 1
        m = re.match(r"^\s*(?:-\s*)?run:\s*(.*)$", line)
        if m:
            cmd = m.group(1).strip()
            if cmd in ("|", ">", "|-", ">-", ""):
                block = []
                j = i + 1
                while j < len(lines) and len(block) < 4 and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    if lines[j].strip():
                        block.append(lines[j].strip())
                    j += 1
                cmd = "; ".join(block)
            if cmd:
                runs.append(cmd[:160])
        i += 1
    return triggers, runs


def repo_gates(project):
    """What stands between a change on this machine and the people who use
    the product, for every person and tool and not only inside Claude Code:
    git hooks, CI, and what the repository itself would teach the next
    agent. Local and read-only; nothing here asks the host."""
    out = section("Repository gates")
    root = git(project, "rev-parse", "--show-toplevel")
    if not root:
        out.append("- not a git repository: no git hooks or CI to inventory")
        return out
    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD") or "?"
    default = git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    out.append(f"- current branch: `{branch}`; default branch on origin: `{default or '(origin/HEAD not set)'}`")

    # Git hooks: what is active on this machine, and what is committed.
    configured = git(root, "config", "--get", "core.hooksPath")
    active = git(root, "rev-parse", "--git-path", "hooks") or os.path.join(".git", "hooks")
    active_abs = active if os.path.isabs(active) else os.path.join(root, active)
    out.append(f"- core.hooksPath: `{configured or '(unset: .git/hooks, which is not committed)'}`")
    live = []
    if os.path.isdir(active_abs):
        live = sorted(f for f in os.listdir(active_abs)
                      if not f.endswith(".sample") and os.path.isfile(os.path.join(active_abs, f)))
    out.append(f"- active git hooks on this machine: {', '.join(live) if live else 'none'}")
    committed = []
    for d in (".githooks", ".husky"):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            files = sorted(f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f)) and not f.startswith("."))
            reached = os.path.abspath(active_abs).startswith(os.path.abspath(p))
            committed.append((d, files, reached))
            out.append(f"- committed hook directory `{d}/`: {', '.join(files) or '(empty)'}; "
                       + ("the active hooks path points at it" if reached else
                          "NOT ACTIVE on this machine: the hooks path does not point at it"))
    if not committed:
        out.append("- no committed hook directory (.githooks/, .husky/): any hook above exists on this machine only")
    shown = set()
    for d, files, _ in committed:
        for f in files:
            if f in ("pre-push", "pre-commit", "commit-msg") and f not in shown:
                shown.add(f)
                t = read_text(os.path.join(root, d, f), 40)
                out.append(f"\n**{d}/{f}** ({t[0]} lines)\n")
                out += fenced(t[1], "bash")
    for f in live:
        if f in ("pre-push", "pre-commit", "commit-msg") and f not in shown:
            t = read_text(os.path.join(active_abs, f), 40)
            if t:
                out.append(f"\n**{f}** (active, {t[0]} lines)\n")
                out += fenced(t[1], "bash")

    # CI: which commands run, and when.
    flows = sorted(glob.glob(os.path.join(root, ".github", "workflows", "*.yml")) +
                   glob.glob(os.path.join(root, ".github", "workflows", "*.yaml")))
    out.append(f"\n- GitHub Actions workflows: {len(flows)}")
    for w in flows:
        triggers, runs = workflow_summary(w)
        out.append(f"  - `{os.path.relpath(w, root)}`: on {', '.join(triggers) or '?'}; runs: "
                   + ("; ".join(f"`{r}`" for r in runs[:8]) if runs else "(no run steps found)"))
    other = [n for n in (".gitlab-ci.yml", os.path.join(".circleci", "config.yml"), "bitbucket-pipelines.yml",
                         "Jenkinsfile", "azure-pipelines.yml") if os.path.exists(os.path.join(root, n))]
    if other:
        out.append(f"- other CI configuration: {', '.join(other)}")
    out.append("- branch protection: not checked (this inventory is local). To see whether the default branch "
               "requires a check or a review: `gh api repos/<owner>/<repo>/branches/<branch>/protection`")

    # What the repository teaches: instruction files below the root, and weight.
    listing = git(root, "ls-files", "-z", timeout=60)
    tracked = [f for f in (listing or "").split("\0") if f]
    nested = [f for f in tracked
              if os.path.basename(f) in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")
              and os.path.dirname(f) not in ("", ".claude")]
    out.append(f"- instruction files below the root (tracked): {len(nested)}"
               + (": " + ", ".join(f"`{f}`" for f in nested[:20]) if nested else ""))
    sizes = []
    for f in tracked:
        try:
            sizes.append((os.path.getsize(os.path.join(root, f)), f))
        except OSError:
            pass
    sizes.sort(reverse=True)
    total = sum(s for s, _ in sizes)
    out.append(f"- tracked files: {len(sizes)}, {total / 1e6:.1f} MB. Largest: "
               + ("; ".join(f"`{f}` ({s / 1024:.0f} KB)" for s, f in sizes[:5]) or "none"))
    docs = []
    for s, f in sizes:
        if f.lower().endswith((".md", ".mdx")) and s <= 2_000_000:
            try:
                with open(os.path.join(root, f), encoding="utf-8", errors="replace") as fh:
                    docs.append((len(fh.read().split()), f))
            except OSError:
                pass
    docs.sort(reverse=True)
    out.append(f"- tracked Markdown: {len(docs)} files, {sum(w for w, _ in docs):,} words. Longest: "
               + ("; ".join(f"`{f}` ({w:,} words)" for w, f in docs[:5]) or "none"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--out")
    a = ap.parse_args()
    project = os.path.abspath(a.project)
    out = [f"# Setup inventory: {project}", ""]

    # Version and environment
    out += section("Claude Code")
    try:
        v = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        v = "claude not on PATH"
    out.append(f"- Version: {v}")
    env = sorted(k for k in os.environ if k.startswith(("CLAUDE", "ANTHROPIC", "MCP_")))
    out.append(f"- Environment variables set (names only): {', '.join(env) if env else 'none'}")

    # Global settings
    out += section("Global settings (~/.claude/settings.json)")
    gs = load_json(os.path.join(HOME, ".claude", "settings.json")) or {}
    if not gs:
        out.append("- (no file)")
    else:
        out.append(f"- model: `{gs.get('model', '(unset: Claude Code default)')}`")
        out.append(f"- effortLevel: `{gs.get('effortLevel', '(unset)')}`")
        perms = gs.get("permissions", {}) or {}
        out.append(f"- permissions.defaultMode: `{perms.get('defaultMode', '(unset: default, prompts per tool call)')}`")
        for k in ("allow", "deny", "ask"):
            rules = perms.get(k) or []
            out.append(f"- permissions.{k}: {len(rules)} rules" + (f": `{'`, `'.join(rules[:40])}`" if rules else ""))
        hooks = gs.get("hooks") or {}
        out.append(f"- user-defined hooks: {sum(len(v) for v in hooks.values()) if hooks else 0} across events {list(hooks.keys()) or 'none'}")
        if hooks:
            out += fenced(json.dumps(mask(hooks), indent=1), "json")
        ph = [p["name"] for p in plugin_entries(gs, project) if p["hooks"] and p["state"] == "on"]
        out.append(f"- plugin-supplied hooks (enabled plugins): {', '.join(ph) if ph else 'none'}")
        ep = gs.get("enabledPlugins") or {}
        on = [k for k, v in ep.items() if v]
        off = [k for k, v in ep.items() if not v]
        out.append(f"- enabledPlugins: {len(on)} on ({', '.join(on) or 'none'}); {len(off)} off ({', '.join(off) or 'none'})")
        envv = gs.get("env") or {}
        out.append(f"- env (names only): {', '.join(envv.keys()) if envv else 'none'}")
        other = {k: v for k, v in gs.items() if k not in ("model", "effortLevel", "permissions", "hooks", "enabledPlugins", "env")}
        if other:
            out.append("- other keys:")
            out += fenced(json.dumps(mask(other), indent=1), "json")
    gl = load_json(os.path.join(HOME, ".claude", "settings.local.json"))
    if gl:
        out.append("- ~/.claude/settings.local.json also present:")
        out += fenced(json.dumps(mask(gl), indent=1), "json")

    # ~/.claude.json: mcp servers global and per project
    cj = load_json(os.path.join(HOME, ".claude.json")) or {}
    out += section("MCP servers")
    gm = cj.get("mcpServers") or {}
    out.append(f"- user-scope servers (~/.claude.json): {len(gm)}" + (f": {', '.join(gm.keys())}" if gm else ""))
    pj = (cj.get("projects") or {}).get(project) or {}
    pm = pj.get("mcpServers") or {}
    out.append(f"- project-scope servers registered for this path: {len(pm)}" + (f": {', '.join(pm.keys())}" if pm else ""))
    mcpj = load_json(os.path.join(project, ".mcp.json")) or {}
    ms = mcpj.get("mcpServers") or {}
    out.append(f"- .mcp.json in the repo: {len(ms)}" + (f": {', '.join(ms.keys())}" if ms else ""))
    pm2 = [f"{p['name']} ({', '.join(p['mcp'])})" for p in plugin_entries(gs, project) if p["mcp"] and p["state"] == "on"]
    out.append(f"- plugin-supplied servers (enabled plugins): {len(pm2)}" + (f": {'; '.join(pm2)}" if pm2 else "") +
               ". A server whose only tools are authenticate/complete_authentication is not signed in.")
    out.append(f"- approved .mcp.json servers: {pj.get('approvedMcpjsonServers') or []}; rejected: {pj.get('rejectedMcpjsonServers') or []}")
    at = pj.get("allowedTools") or []
    out.append(f"- per-project allowedTools (~/.claude.json): {len(at)}" + (f": `{'`, `'.join(at[:40])}`" if at else ""))

    # CLAUDE.md files in scope: home, ancestors, project, local
    out += section("Instruction files in scope")
    candidates = [os.path.join(HOME, ".claude", "CLAUDE.md")]
    parts = project.split(os.sep)
    for i in range(2, len(parts) + 1):
        d = os.sep.join(parts[:i]) or os.sep
        if d == HOME:
            continue
        for name in ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md"):
            candidates.append(os.path.join(d, name))
    for c in candidates:
        if os.path.exists(c):
            t = read_text(c)
            label = c.replace(HOME, "~")
            scope = "global" if c.startswith(os.path.join(HOME, ".claude")) else ("project" if os.path.dirname(c) == project else "ancestor")
            out.append(f"\n### {label} ({scope}, {t[0]} lines)\n")
            out += fenced(t[1], "markdown")
    rules = sorted(glob.glob(os.path.join(project, ".claude", "rules", "*.md")))
    if rules:
        out.append(f"\n### .claude/rules ({len(rules)} files)\n")
        for r in rules:
            t = read_text(r, 80)
            out.append(f"\n**{os.path.basename(r)}** ({t[0]} lines)\n")
            out += fenced(t[1], "markdown")

    # Project settings
    out += section("Project settings")
    for name in ("settings.json", "settings.local.json"):
        p = os.path.join(project, ".claude", name)
        d = load_json(p)
        if d is None:
            out.append(f"- .claude/{name}: (none)")
        else:
            out.append(f"- .claude/{name}:")
            out += fenced(json.dumps(mask(d), indent=1), "json")

    # Project skills, commands, agents
    out += section("Project skills, commands, agents")
    for kind, pattern in (("skills", ".claude/skills/*/SKILL.md"), ("commands", ".claude/commands/*.md"), ("agents", ".claude/agents/*.md")):
        files = sorted(glob.glob(os.path.join(project, pattern)))
        out.append(f"- {kind}: {len(files)}")
        for f in files:
            fm = frontmatter(f)
            name = fm.get("name") or os.path.basename(os.path.dirname(f) if kind == "skills" else f)
            desc = (fm.get("description") or "")[:160]
            out.append(f"  - `{name}`: {desc}")

    # Plugins
    out += section("Plugins")
    rows = []
    entries = plugin_entries(gs, project)
    seen = {}
    for p in entries:
        seen.setdefault(p["name"], []).append(p)
    for name, group in seen.items():
        group.sort(key=lambda p: (p["state"] != "on", p["scope"] != "user"))
        p = group[0]
        extra = f" (+{len(group) - 1} other copies, off)" if len(group) > 1 else ""
        descs = "; ".join((frontmatter(s).get("description") or "")[:90] for s in p["skills"][:3])
        rows.append(f"| {name}{extra} | {p['version']} | {p['scope']} | {p['state']} | {len(p['skills'])} | {len(p['cmds'])} | {'yes' if p['hooks'] else ''} | {', '.join(p['mcp'])} | {descs} |")
    out.append("| plugin | version | scope | state | skills | commands | hooks | mcp | skill descriptions (first 3, truncated) |")
    out.append("| --- | --- | --- | --- | ---: | ---: | --- | --- | --- |")
    out += rows or ["| (none) | | | | | | | | |"]

    # Memory
    out += section("Project memory")
    slug = project.replace("/", "-")
    mem = os.path.join(HOME, ".claude", "projects", slug, "memory")
    files = sorted(glob.glob(os.path.join(mem, "*.md")))
    if not files:
        out.append("- no memory directory for this project (nothing is carried between sessions except the repo)")
    else:
        idx = os.path.join(mem, "MEMORY.md")
        out.append(f"- {len(files)} files" + (" (MEMORY.md index present)" if os.path.exists(idx) else " (no MEMORY.md index)"))
        for f in files:
            if os.path.basename(f) == "MEMORY.md":
                continue
            fm = frontmatter(f)
            d = fm.get("description") or ""
            out.append(f"  - `{os.path.basename(f)}`: {d[:140]}{'…' if len(d) > 140 else ''}")

    # Repository gates: git hooks, CI, and what the repo teaches
    out += repo_gates(project)

    # Sessions for this project: models used
    out += section("Sessions on this machine for this project (model that answered)")
    out.append("Every session on disk for this checkout and its git worktrees, newest first. Claude Code keeps "
               "transcripts until it cleans them up; an empty or one-row table means few sessions were run from "
               "this path on this machine, not that the project is idle.")
    here = os.path.dirname(os.path.abspath(__file__))
    ext = os.path.join(here, "extract_session.py")
    try:
        r = subprocess.run([sys.executable, ext, "list", "--project", project, "--worktrees"], capture_output=True, text=True, timeout=120)
        out += fenced((r.stdout or r.stderr).strip()[:3000] or "(none)")
    except Exception as e:
        out.append(f"- could not list sessions: {e}")

    text = "\n".join(out) + "\n"
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        print(a.out)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
