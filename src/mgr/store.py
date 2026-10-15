"""The plans folder: one JSON file per planning window, plus agent instructions.

Layout of a plans folder:

    2026-10-19.json            one plan per window, named by start date
    2026-10-19.handoff.md      last export of that plan (from `x` in the board)
    agent-instructions.md      team conventions appended to every handoff brief
    archive/                   plans you're done with, moved out of the list
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from mgr import model
from mgr.model import Plan, Status

INSTRUCTIONS_FILE = "agent-instructions.md"
ARCHIVE_DIR = "archive"

# Everything is inside an HTML comment, so a fresh file contributes nothing to
# the brief until the user writes real conventions outside the comment.
INSTRUCTIONS_TEMPLATE = """\
<!--
Agent instructions for creating Linear tickets.

Anything outside HTML comments is added to every handoff brief generated from
plans in this folder, as conventions the agent must follow. They override the
brief's default field mapping where the two conflict. Write them as plain
Markdown bullets, for example:

- Add every ticket to the team's current active cycle.
- Tickets of kind "research" or "experiment" get the "Research" label, and
  their description must end with a "Done when:" line.
- Start every description with a one-line "Why:" linking to the plan goal.
- If an assignee is on the on-call rotation this week, ask before assigning.
-->
"""


def default_dir() -> Path:
    return Path(os.environ.get("MGR_DIR", Path.home() / ".local" / "share" / "mgr")).expanduser()


@dataclass
class PlanFile:
    path: Path
    plan: Plan | None  # None when the file couldn't be parsed
    error: str = ""

    def contains(self, day: date) -> bool:
        return self.plan is not None and self.plan.start <= day.isoformat() <= self.plan.end


def list_plans(folder: Path) -> list[PlanFile]:
    """All plans in `folder`, newest window first. Unreadable files are kept
    (with an error) so the picker can show them instead of silently hiding data."""
    found: list[PlanFile] = []
    for path in folder.glob("*.json"):
        try:
            found.append(PlanFile(path, model.load(path)))
        except (ValueError, TypeError, KeyError) as exc:
            found.append(PlanFile(path, None, f"{type(exc).__name__}: {exc}"))
    # Broken files sort last; their start date is unknown.
    return sorted(found, key=lambda f: f.plan.start if f.plan else "", reverse=True)


def current_plan(folder: Path, today: date | None = None) -> Path | None:
    """The plan to use when none is named: the one whose window contains today;
    else the next upcoming one (planning usually happens just ahead of a
    window); else, if every window is in the past, the most recent."""
    plans = [f for f in list_plans(folder) if f.plan]  # newest start first
    today = today or date.today()
    for f in plans:
        if f.contains(today):
            return f.path
    upcoming = [f for f in plans if f.plan.start > today.isoformat()]
    if upcoming:
        return upcoming[-1].path  # earliest start among future windows
    return plans[0].path if plans else None


def unique_path(folder: Path, start: str) -> Path:
    path = folder / f"{start}.json"
    n = 2
    while path.exists():
        path = folder / f"{start}-{n}.json"
        n += 1
    return path


def create_plan(folder: Path, start: str | None = None, end: str | None = None) -> Path:
    """Create an empty plan for the window after the latest existing one.

    Team settings are copied from that plan, since they rarely change between
    windows; ideas and goals are not (use carry-over for those)."""
    plans = [f.plan for f in list_plans(folder) if f.plan]
    plan = Plan()
    if plans:
        latest = plans[0]
        following = date.fromisoformat(latest.end) + timedelta(days=1)
        length = date.fromisoformat(latest.end) - date.fromisoformat(latest.start)
        plan.start, plan.end = following.isoformat(), (following + length).isoformat()
        plan.name = latest.name
        plan.capacity = latest.capacity
        plan.linear_team = latest.linear_team
        plan.linear_project = latest.linear_project
    if start:
        # An explicit start keeps the inferred window length unless end is given too.
        length = date.fromisoformat(plan.end) - date.fromisoformat(plan.start)
        plan.start = date.fromisoformat(start).isoformat()
        plan.end = (date.fromisoformat(start) + length).isoformat()
    if end:
        plan.end = date.fromisoformat(end).isoformat()
    if plan.end < plan.start:
        raise ValueError(f"end {plan.end} is before start {plan.start}")
    path = unique_path(folder, plan.start)
    model.save(plan, path)
    return path


def carry(path: Path, new_path: Path | None = None) -> tuple[Plan, Plan, Path]:
    """Carry `path`'s unfinished ideas into a new plan file. Returns (old, new, new_path)."""
    old = model.load(path)
    nxt = model.carry_over(old)
    if new_path is None:
        new_path = unique_path(path.parent, nxt.start)
    elif new_path.exists():
        # Never clobber: the target may already hold a half-planned window.
        raise FileExistsError(new_path)
    model.save(nxt, new_path)
    return old, nxt, new_path


def _companions(path: Path) -> list[Path]:
    # The handoff export belongs to its plan and should travel with it.
    handoff = path.with_suffix(".handoff.md")
    return [path] + ([handoff] if handoff.exists() else [])


def archive(path: Path) -> Path:
    target_dir = path.parent / ARCHIVE_DIR
    target_dir.mkdir(exist_ok=True)
    for f in _companions(path):
        target = target_dir / f.name
        if target.exists():
            raise FileExistsError(target)
        shutil.move(f, target)
    return target_dir / path.name


def delete(path: Path) -> None:
    for f in _companions(path):
        f.unlink()


def summary(plan: Plan) -> str:
    committed = plan.in_status(Status.COMMITTED)
    ticketed = sum(1 for i in committed if i.linear_id)
    cap = f"/{plan.capacity:g}" if plan.capacity else ""
    return (
        f"{len(plan.ideas)} ideas · {plan.committed_points():g}{cap} pts committed · "
        f"{ticketed}/{len(committed)} ticketed"
    )


# ----- agent instructions ----------------------------------------------------


def instructions_path(folder: Path) -> Path:
    return folder / INSTRUCTIONS_FILE


def ensure_instructions(folder: Path) -> Path:
    path = instructions_path(folder)
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_text(INSTRUCTIONS_TEMPLATE)
    return path


def load_instructions(folder: Path) -> str:
    """Team conventions for the brief: the file minus HTML comments."""
    path = instructions_path(folder)
    if not path.exists():
        return ""
    return re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.DOTALL).strip()
