"""Free-form Markdown notes, and promoting them into plans.

Notes are plain `.md` files in `<plans folder>/notes/`, so they can be edited
in any editor and read by an LLM as-is. There is deliberately no hidden
metadata: a promoted note simply gains a visible "> Promoted to …" line.

Promotion reads the note's structure:

    # Title                     → plan name (new plans only)
    ## Goals
    - Ship eval v1              → plan goal
    - Retrieval harness         → Inbox idea
      details, sub-bullets      → that idea's notes
    - [ ] Profile reranker      → Inbox idea
    - [x] Already done          → skipped
    everything                  → appended to the plan's Background
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from mgr import model, store

NOTES_DIR = "notes"

_TITLE = re.compile(r"^#\s+(.+?)\s*$")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
# A list item at any indent: "-", "*", "+" or "1." / "1)", optionally a task box.
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(?:\[( |x|X)\]\s+)?(.*)$")
_PROMOTED = re.compile(r"^> Promoted to (\S+)", re.MULTILINE)


def notes_dir(folder: Path) -> Path:
    return folder / NOTES_DIR


@dataclass
class Note:
    path: Path

    @property
    def text(self) -> str:
        return self.path.read_text()

    @property
    def title(self) -> str:
        for line in self.text.splitlines():
            if m := _TITLE.match(line):
                return m.group(1)
        return self.path.stem

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime)

    @property
    def created(self) -> date:
        """The note's date: its YYYY-MM-DD filename prefix (set at creation,
        and editable by renaming), else the file's modification date for
        notes added by hand under other names."""
        try:
            return date.fromisoformat(self.path.name[:10])
        except ValueError:
            return self.modified.date()

    @property
    def promoted_to(self) -> list[str]:
        return _PROMOTED.findall(self.text)


def list_notes(folder: Path) -> list[Note]:
    """Notes in the folder, most recently edited first."""
    d = notes_dir(folder)
    if not d.exists():
        return []
    return sorted((Note(p) for p in d.glob("*.md")), key=lambda n: n.path.stat().st_mtime, reverse=True)


def append_note(path: Path, text: str) -> None:
    body = path.read_text().rstrip("\n")
    path.write_text(f"{body}\n\n{text.strip()}\n")


def archive_note(path: Path) -> Path:
    target = path.parent / "archive" / path.name
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        raise FileExistsError(target)
    path.rename(target)
    return target


def week_heading(day: date, today: date | None = None) -> str:
    """E.g. "v.42 · 12–18 Oct 2026 · this week"."""
    today = today or date.today()
    monday = day - timedelta(days=day.weekday())
    sunday = monday + timedelta(days=6)
    iso = day.isocalendar()
    span = (
        f"{monday.day}–{sunday:%-d %b %Y}" if monday.month == sunday.month else f"{monday:%-d %b} – {sunday:%-d %b %Y}"
    )
    this_monday = today - timedelta(days=today.weekday())
    relative = {0: " · this week", 1: " · last week", -1: " · next week"}.get((this_monday - monday).days // 7, "")
    return f"v.{iso.week} · {span}{relative}"


def group_by_week(items: list[Note]) -> list[tuple[tuple[int, int], list[Note]]]:
    """Notes grouped by the ISO week of their date, newest week first; within a
    week, newest first. Keys are (iso year, iso week) so year ends sort right."""
    groups: dict[tuple[int, int], list[Note]] = {}
    for n in sorted(items, key=lambda n: (n.created, n.path.name), reverse=True):
        iso = n.created.isocalendar()
        groups.setdefault((iso.year, iso.week), []).append(n)
    return list(groups.items())


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:50] or "note"


def create_note(folder: Path, title: str) -> Path:
    d = notes_dir(folder)
    d.mkdir(parents=True, exist_ok=True)
    # Date prefix keeps the folder sorted chronologically in plain `ls` too.
    base = f"{date.today().isoformat()}-{_slug(title)}"
    path, n = d / f"{base}.md", 2
    while path.exists():
        path, n = d / f"{base}-{n}.md", n + 1
    path.write_text(f"# {title}\n\n")
    return path


@dataclass
class Extracted:
    title: str
    goals: list[str]
    ideas: list[tuple[str, str]]  # (title, notes)
    background: str


def extract(text: str, fallback_title: str) -> Extracted:
    title = fallback_title
    goals: list[str] = []
    ideas: list[tuple[str, str]] = []
    in_goals = False
    # Indent of the current section's top-level items. List items are only
    # "top level" relative to the first one seen after a heading, so notes
    # that indent everything by two spaces still work.
    top_indent: int | None = None
    current: list[str] | None = None  # body lines of the idea being collected

    def flush() -> None:
        nonlocal current
        if current is not None and ideas:
            ideas[-1] = (ideas[-1][0], textwrap.dedent("\n".join(current)).strip())
        current = None

    for line in text.splitlines():
        if heading := _HEADING.match(line):
            flush()
            if line.startswith("# ") and title == fallback_title:
                title = heading.group(1)
            in_goals = heading.group(1).strip().lower() in ("goal", "goals")
            top_indent = None
            continue
        if _PROMOTED.match(line):
            continue
        item = _ITEM.match(line)
        if item:
            indent, box, body = len(item.group(1)), item.group(2), item.group(3).strip()
            if top_indent is None:
                top_indent = indent
            if indent <= top_indent:
                flush()
                if not body or (box and box.lower() == "x"):
                    continue  # skip empty bullets and completed tasks
                if in_goals:
                    goals.append(body)
                else:
                    ideas.append((body, ""))
                    current = []
                continue
        if current is not None:
            # Anything indented under an idea (sub-bullets, paragraphs) is its
            # notes; a non-indented, non-blank line ends the idea.
            if line.strip() and not line[:1].isspace():
                flush()
            else:
                current.append(line)
    flush()

    background = "\n".join(
        line for line in text.splitlines() if not _PROMOTED.match(line) and not _TITLE.match(line)
    ).strip()
    return Extracted(title, goals, ideas, background)


def promote(note_path: Path, plan_path: Path | None, folder: Path) -> tuple[Path, Extracted]:
    """Promote a note into `plan_path`, or into a new plan when None.

    Ideas land in Inbox (they still need shaping); goals are added if not
    already present; the note text is appended to the plan's Background.
    Returns the plan's path and what was extracted.
    """
    note = Note(note_path)
    ex = extract(note.text, note.path.stem)

    if plan_path is None:
        plan_path = store.create_plan(folder)
        plan = model.load(plan_path)
        plan.name = ex.title
    else:
        plan = model.load(plan_path)

    for title, idea_notes in ex.ideas:
        plan.add(title).notes = idea_notes
    plan.goals += [g for g in ex.goals if g not in plan.goals]
    if ex.background:
        section = f"From note “{ex.title}” ({note.path.name}):\n\n{ex.background}"
        plan.background = f"{plan.background}\n\n---\n\n{section}" if plan.background else section
    model.save(plan, plan_path)

    stamp = f"> Promoted to {plan_path.name} on {date.today().isoformat()}"
    note.path.write_text(note.text.rstrip("\n") + f"\n\n{stamp}\n")
    return plan_path, ex
