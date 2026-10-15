"""Claude Code skills that let any session work with mgr through its CLI.

- mgr-planning:       capture notes and ideas from a conversation, create
                      plans, read plans back.
- mgr-linear-handoff: create Linear tickets from a plan.

Both embed the absolute path of the running `mgr` and the plans folder, so a
session in any directory drives the same install and the same data. They
only describe *how to call the CLI*; anything team-specific lives in the
plans folder (agent-instructions.md), so editing that never requires
reinstalling the skills.
"""

from __future__ import annotations

import shlex
from collections.abc import Callable
from pathlib import Path

from mgr.export import mgr_command


def skills_root() -> Path:
    return Path.home() / ".claude" / "skills"


def _cmd(plans_dir: Path) -> str:
    return f"{mgr_command()} --dir {shlex.quote(str(plans_dir.resolve()))}"


def render_planning(plans_dir: Path) -> str:
    mgr = _cmd(plans_dir)
    return f"""\
---
name: mgr-planning
description: Capture notes and ideas into the user's mgr planning tool and manage plans. Use when the user asks to note something down, save or capture something from the conversation, add an idea/task to their plan or backlog, start or create a new plan, or asks what is in their plan or notes.
---

# Working with mgr plans and notes

`mgr` is the user's planning TUI. A **plan** is one planning window (usually
two weeks) holding ideas in columns: inbox → maybe → committed (plus parked,
dropped). **Notes** are free-form Markdown. Data lives in `{plans_dir}`.

Always go through the CLI below — never edit the JSON or note files
directly; the user may have the board open, and the CLI's writes are merged
into it safely. Every command accepts `--plan <name>`; without it, commands
use the current plan (the one marked `*` in `ls`).

    {mgr} ls                     # plans; * = current
    {mgr} show [--plan P]        # every idea in a plan, with mgr-N ids
    {mgr} notes                  # notes grouped by ISO week (v.N), newest first
    {mgr} search WORDS [--all]   # ideas and notes containing all WORDS (--all: archived too)

## Capture into a note

Use a note for anything that isn't yet a concrete piece of work: meeting
takeaways, decisions, context, half-formed ideas.

    {mgr} note "Title" --body - <<'EOF'
    ...Markdown...
    EOF
    {mgr} append <note> --body - <<'EOF'
    ...
    EOF

`<note>` is a file name from `notes` or a unique part of it. Prefer appending
to an existing note on the same topic over creating a near-duplicate.

## Add ideas to a plan

Use a plan idea for concrete work someone could pick up.

    {mgr} add "Title" [--plan P] [--notes - <<'EOF' ... EOF] [--status inbox|maybe|committed|parked]
        [--kind feature|experiment|research|infra|data|bug|chore] [--priority 0-4]
        [--estimate N] [--assignee NAME] [--labels a,b] [--parent mgr-N]

Run `show` (or `search`) first so you don't add duplicates, and check the plan's window
fits. Ideas go to **inbox** unless the user explicitly says it's committed
or a maybe; deciding what to commit is the user's job at the board. Don't
invent estimates, priorities or assignees the user didn't give.

## Create a plan

    {mgr} new [--name N] [--weeks 45-46 | --start YYYY-MM-DD --end YYYY-MM-DD]
        [--goal G]... [--capacity N] [--team T]

The user counts in ISO (Swedish) week numbers, "v.45"; pass those with
`--weeks`. Without dates it takes the window after the latest plan. To turn a note into
a plan (bullets → inbox ideas, `## Goals` bullets → goals, the note itself →
background), use `{mgr} promote <note> [--into P]`.

## Writing well for later

Whatever you write will be read days later without this conversation, by
the user or by the agent that creates Linear tickets from it. Make each note
and idea self-contained: say what, why, and any decision or constraint from
the conversation; name people and systems explicitly; prefer specifics
("p95 latency on /search regressed from 120ms to 300ms") over references
("the issue above"). Keep idea titles short and imperative.

Finish by telling the user exactly what you created or changed (note file
names, mgr-N ids and which plan), so they can find it on the board.

To create Linear tickets from a plan, use the mgr-linear-handoff skill.
"""


def render_handoff(plans_dir: Path) -> str:
    mgr = _cmd(plans_dir)
    return f"""\
---
name: mgr-linear-handoff
description: Create Linear tickets from an mgr planning session. Use when the user asks to create, push or sync tickets from their mgr plan, planning board, or "the plan for next sprint/cycle/window".
---

# Create Linear tickets from an mgr plan

`mgr` is the user's planning TUI. Each planning window is a JSON plan in
`{plans_dir}`. Never edit those files directly; go through the `mgr` CLI.

1. List the plans:

       {mgr} ls

   The plan marked `*` is the current one. Use it unless the user named a
   different one, and tell them which plan you picked.

2. Get the handoff brief for that plan:

       {mgr} export --plan <plan file>

3. Follow the brief's "Instructions for the assistant" and "Team conventions"
   exactly. They tell you which tickets to create, how to map fields, to
   confirm with the user first, and how to record each Linear id with
   `mgr link` (always do this, it is what prevents duplicates on the next run).

If the brief says there is nothing to create, say so and stop.
"""


SKILLS: dict[str, Callable[[Path], str]] = {
    "mgr-planning": render_planning,
    "mgr-linear-handoff": render_handoff,
}


def install_all(plans_dir: Path, root: Path | None = None) -> list[Path]:
    root = root or skills_root()
    written = []
    for name, render in SKILLS.items():
        path = root / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(plans_dir))
        written.append(path)
    return written
