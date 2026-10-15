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
