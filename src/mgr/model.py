"""Plan data model and JSON persistence.

A plan is one planning window (typically a two-week cycle) holding a loose pile
of ideas. Ideas move between statuses as they firm up; only `committed` ideas
become Linear tickets.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path


def week_label(start: str, end: str) -> str:
    """ISO 8601 week numbers for a window, Swedish style: "v.43", "v.43–44",
    or "v.52–1" across a new year. ISO weeks start on Monday and week 1 is
    the week containing the year's first Thursday — the Swedish convention,
    which is exactly what `date.isocalendar()` implements."""
    first = date.fromisoformat(start).isocalendar().week
    last = date.fromisoformat(end).isocalendar().week
    return f"v.{first}" if first == last else f"v.{first}–{last}"


def weeks_to_window(spec: str, today: date | None = None) -> tuple[str, str]:
    """ "45" or "45-46" → (Monday of the first week, Sunday of the last).

    Week numbers name the *next* occurrence: in December, "2" means week 2
    of the coming year, since nobody plans weeks that are already over.
    """
    first_s, _, last_s = spec.replace("–", "-").partition("-")
    first, last = int(first_s), int(last_s or first_s)
    today = today or date.today()
    year = today.isocalendar().year
    start = date.fromisocalendar(year, first, 1)
    if start + timedelta(days=6) < today:
        year += 1
        start = date.fromisocalendar(year, first, 1)
    # A range like 52-1 wraps into the following ISO year.
    end_year = year + 1 if last < first else year
    end = date.fromisocalendar(end_year, last, 7)
    return start.isoformat(), end.isoformat()


def window_label(start: str, end: str) -> str:
    return f"{start} → {end} · {week_label(start, end)}"


class Status(StrEnum):
    INBOX = "inbox"
    MAYBE = "maybe"
    COMMITTED = "committed"
    PARKED = "parked"
    DROPPED = "dropped"


# Left-to-right order on the board. Dropped is last so it can be hidden by
# simply truncating the list.
BOARD_ORDER = [Status.INBOX, Status.MAYBE, Status.COMMITTED, Status.PARKED, Status.DROPPED]

KINDS = ["feature", "experiment", "research", "infra", "data", "bug", "chore"]

# Mirrors Linear's priority scale so the export maps 1:1 onto the API.
# Note Linear's ordering is unintuitive: 0 means "no priority", 1 is the highest.
PRIORITIES = {0: "None", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}


@dataclass
class Idea:
    id: int
    title: str
    notes: str = ""
    status: Status = Status.INBOX
    kind: str = "feature"
    priority: int = 0
    # Story points; None means "not estimated yet", which is distinct from 0.
    estimate: float | None = None
    assignee: str = ""
    labels: list[str] = field(default_factory=list)
    project: str = ""
    # Local id of another idea; exported so the LLM can create sub-issues.
    parent: int | None = None
    # Linear id of a parent that only exists in Linear. Set by carry-over
    # when the local parent was already ticketed and so stayed behind.
    parent_linear_id: str = ""
    # Set once a Linear issue exists (e.g. "ENG-123"), so re-exports skip it.
    linear_id: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    # How many planning windows this idea has rolled over without becoming
    # a ticket — a signal that it keeps slipping.
    carry_count: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> Idea:
        # Ignore unknown keys so older/newer files stay loadable.
        known = {f.name for f in fields(cls)}
        idea = cls(**{k: v for k, v in data.items() if k in known})
        idea.status = Status(idea.status)
        return idea


def _default_start() -> str:
    # Default to next Monday: planning usually happens ahead of the cycle.
    today = date.today()
    return (today + timedelta(days=(7 - today.weekday()) % 7 or 7)).isoformat()


def _default_end() -> str:
    # Two-week window ending on the Sunday after next.
    return (date.fromisoformat(_default_start()) + timedelta(days=13)).isoformat()


@dataclass
class Plan:
    name: str = "Team plan"
    start: str = field(default_factory=_default_start)
    end: str = field(default_factory=_default_end)
    capacity: float = 0
    goals: list[str] = field(default_factory=list)
    # Free-form context for the window (e.g. promoted notes); exported so the
    # ticket-creating agent can write better descriptions.
    background: str = ""
    linear_team: str = ""
    linear_project: str = ""
    ideas: list[Idea] = field(default_factory=list)
    next_id: int = 1

    def add(self, title: str, status: Status = Status.INBOX) -> Idea:
        idea = Idea(id=self.next_id, title=title, status=status)
        self.next_id += 1
        self.ideas.append(idea)
        return idea

    def get(self, idea_id: int) -> Idea | None:
        return next((i for i in self.ideas if i.id == idea_id), None)

    def remove(self, idea_id: int) -> None:
        self.ideas = [i for i in self.ideas if i.id != idea_id]
        # Orphan children rather than leaving dangling parent references.
        for idea in self.ideas:
            if idea.parent == idea_id:
                idea.parent = None

    def in_status(self, status: Status) -> list[Idea]:
        # List order within `ideas` doubles as the rank within each column.
        return [i for i in self.ideas if i.status == status]

    def committed_points(self) -> float:
        return sum(i.estimate or 0 for i in self.in_status(Status.COMMITTED))

    def move_within_status(self, idea_id: int, delta: int) -> None:
        """Swap an idea with its neighbour in the same column (delta = -1 or +1)."""
        idea = self.get(idea_id)
        if idea is None:
            return
        column = self.in_status(idea.status)
        pos = column.index(idea)
        target = pos + delta
        if not 0 <= target < len(column):
            return
        other = column[target]
        a, b = self.ideas.index(idea), self.ideas.index(other)
        self.ideas[a], self.ideas[b] = self.ideas[b], self.ideas[a]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        known = {f.name for f in fields(cls)}
        plan = cls(**{k: v for k, v in data.items() if k in known and k != "ideas"})
        plan.ideas = [Idea.from_dict(d) for d in data.get("ideas", [])]
        # Guard against hand-edited files where next_id fell behind.
        plan.next_id = max([plan.next_id, *(i.id + 1 for i in plan.ideas)])
        return plan


def load(path: Path) -> Plan:
    if not path.exists():
        return Plan()
    return Plan.from_dict(json.loads(path.read_text()))


def save(plan: Plan, path: Path) -> None:
    # Write atomically: the TUI autosaves constantly, and `mgr link` may write
    # the same file from another process, so a torn write would lose data.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(plan.to_dict(), f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# Where each status lands in the next window. Committed-but-unticketed ideas
# drop back to Maybe: they weren't actually started, so they should compete
# for the new window's capacity rather than silently pre-filling it.
CARRY_STATUS = {
    Status.INBOX: Status.INBOX,
    Status.MAYBE: Status.MAYBE,
    Status.PARKED: Status.PARKED,
    Status.COMMITTED: Status.MAYBE,
}


def carry_over(plan: Plan) -> Plan:
    """Build the next window's plan from the unfinished ideas of `plan`.

    "Unfinished" means never ticketed. Ideas with a Linear id are left behind
    (Linear tracks them from here) and so are dropped ones. Ids are preserved
    so `mgr-N` references stay meaningful across windows.
    """
    start = date.fromisoformat(plan.end) + timedelta(days=1)
    length = date.fromisoformat(plan.end) - date.fromisoformat(plan.start)
    nxt = Plan(
        name=plan.name,
        start=start.isoformat(),
        end=(start + length).isoformat(),
        capacity=plan.capacity,
        goals=list(plan.goals),
        linear_team=plan.linear_team,
        linear_project=plan.linear_project,
        next_id=plan.next_id,
    )

    carried = {i.id for i in plan.ideas if i.status in CARRY_STATUS and not i.linear_id}
    for old in plan.ideas:
        if old.id not in carried:
            continue
        idea = Idea.from_dict(asdict(old))  # deep copy
        idea.status = CARRY_STATUS[old.status]
        idea.carry_count += 1
        if idea.parent is not None and idea.parent not in carried:
            parent = plan.get(idea.parent)
            # Keep the link if the parent lives on in Linear; otherwise the
            # parent was dropped or deleted and the reference is meaningless.
            if parent is not None and parent.linear_id:
                idea.parent_linear_id = parent.linear_id
            idea.parent = None
        nxt.ideas.append(idea)
    return nxt


def merge(base: Plan, ours: Plan, theirs: Plan) -> Plan:
    """Three-way merge of a plan: `ours` (in-memory edits) and `theirs` (the
    file on disk, changed by another process such as an agent running the
    CLI) both descend from `base` (the last state we saved).

    Field-level, so concurrent edits to *different* fields of the same idea
    both survive (e.g. we move an idea while an agent links it to Linear).
    Where both sides changed the same field, ours wins: the person at the
    board is the one looking at it.
    """
    b, o, t = base.to_dict(), ours.to_dict(), theirs.to_dict()

    def pick(bv: dict, ov: dict, tv: dict, key: str):
        return ov.get(key) if ov.get(key) != bv.get(key) else tv.get(key)

    merged = {k: pick(b, o, t, k) for k in o if k not in ("ideas", "next_id")}

    b_ideas = {i["id"]: i for i in b["ideas"]}
    t_ideas = {i["id"]: i for i in t["ideas"]}
    o_ids = {i["id"] for i in o["ideas"]}
    ideas: list[dict] = []
    for oi in o["ideas"]:
        bi, ti = b_ideas.get(oi["id"]), t_ideas.get(oi["id"])
        if bi is None:
            ideas.append(oi)  # new on our side
        elif ti is None:
            # Deleted on disk: honour that unless we edited it meanwhile.
            if oi != bi:
                ideas.append(oi)
        else:
            ideas.append({k: pick(bi, oi, ti, k) for k in oi})
    # Ideas added on disk. If an id collides with one we just created, ours
    # moves: theirs may already have been reported (e.g. "added mgr-12"),
    # while ours was created moments ago and nobody has seen its number.
    theirs_new = [ti for ti in t["ideas"] if ti["id"] not in b_ideas]
    clashing = {ti["id"] for ti in theirs_new} & o_ids
    next_id = max([o["next_id"], t["next_id"], *(i["id"] + 1 for i in ideas + theirs_new)])
    for idea in ideas:
        if idea["id"] in clashing:
            for child in ideas:  # keep our own parent links pointing at it
                if child["parent"] == idea["id"] and child["id"] not in b_ideas:
                    child["parent"] = next_id
            idea["id"] = next_id
            next_id += 1
    ideas += theirs_new

    merged["ideas"] = ideas
    merged["next_id"] = max([next_id, *(i["id"] + 1 for i in ideas)])
    return Plan.from_dict(merged)
