"""Search across plans and notes.

No index: a plans folder holds at most a few hundred ideas and notes, so
loading everything and filtering takes milliseconds. What this adds over
grep is structure — results say which plan, column and mgr-N matched (or
which note and line), and archived files can be included on request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mgr import notes, store
from mgr.model import week_label


@dataclass
class Hit:
    kind: str  # "idea" or "note"
    path: Path
    label: str  # e.g. "2026-10-19 · committed · mgr-3: Title", or a note title
    snippet: str  # the best matching line, for context
    idea_id: int | None = None


def _matches(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return all(t in lowered for t in terms)


def _snippet(text: str, terms: list[str]) -> str:
    # The line matching the most terms; ties go to the earliest line.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    best = max(lines, key=lambda line: sum(t in line.lower() for t in terms), default="")
    return best if any(t in best.lower() for t in terms) else ""


def search(folder: Path, query: str, include_archived: bool = False) -> list[Hit]:
    """Every idea and note containing all words of `query` (case-insensitive)."""
    terms = query.lower().split()
    if not terms:
        return []
    plan_dirs = [folder] + ([folder / store.ARCHIVE_DIR] if include_archived else [])
    note_dirs = [notes.notes_dir(folder)] + ([notes.notes_dir(folder) / "archive"] if include_archived else [])

    hits: list[Hit] = []
    for d in plan_dirs:
        for f in store.list_plans(d) if d.exists() else []:
            if f.plan is None:
                continue
            for idea in f.plan.ideas:
                haystack = "\n".join(
                    [idea.title, idea.notes, idea.assignee, idea.kind, idea.linear_id, " ".join(idea.labels)]
                )
                if _matches(haystack, terms):
                    weeks = week_label(f.plan.start, f.plan.end)
                    label = f"{f.path.stem} ({weeks}) · {idea.status} · mgr-{idea.id}: {idea.title}"
                    hits.append(Hit("idea", f.path, label, _snippet(idea.notes, terms), idea.id))
    for d in note_dirs:
        for n in sorted(d.glob("*.md"), reverse=True) if d.exists() else []:
            note = notes.Note(n)
            text = note.text
            if _matches(text, terms):
                # Snippet from the body: the title is already in the label.
                body = "\n".join(line for line in text.splitlines() if line.strip("# ").strip() != note.title)
                hits.append(Hit("note", n, note.title, _snippet(body, terms)))
    return hits
