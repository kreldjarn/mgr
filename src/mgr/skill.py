"""A Claude Code skill that teaches any session how to run an mgr handoff.

The skill is deliberately thin: it only says how to find and fetch the brief.
The brief itself carries the real instructions (field mapping, team
conventions, write-back), so there is one source of truth and editing
agent-instructions.md never requires reinstalling the skill.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from mgr.export import mgr_command

SKILL_NAME = "mgr-linear-handoff"


def default_install_path() -> Path:
    return Path.home() / ".claude" / "skills" / SKILL_NAME / "SKILL.md"


def render(plans_dir: Path) -> str:
    mgr = mgr_command()
    folder = shlex.quote(str(plans_dir.resolve()))
    return f"""\
---
name: {SKILL_NAME}
description: Create Linear tickets from an mgr planning session. Use when the user asks to create, push or sync tickets from their mgr plan, planning board, or "the plan for next sprint/cycle/window".
---

# Create Linear tickets from an mgr plan

`mgr` is the user's planning TUI. Each planning window is a JSON plan in
`{plans_dir}`. Never edit those files directly; go through the `mgr` CLI.

1. List the plans:

       {mgr} --dir {folder} ls

   The plan marked `*` is the current one. Use it unless the user named a
   different one, and tell them which plan you picked.

2. Get the handoff brief for that plan:

       {mgr} --dir {folder} export --plan <plan file>

3. Follow the brief's "Instructions for the assistant" and "Team conventions"
   exactly. They tell you which tickets to create, how to map fields, to
   confirm with the user first, and how to record each Linear id with
   `mgr link` (always do this, it is what prevents duplicates on the next run).

If the brief says there is nothing to create, say so and stop.
"""


def install(plans_dir: Path, path: Path | None = None) -> Path:
    path = path or default_install_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(plans_dir))
    return path
