"""Render a plan as a Markdown handoff brief for an LLM with Linear MCP access.

The brief is deliberately explicit: field names match Linear's issue fields,
the instructions spell out ordering (parents before children) and idempotency
(skip anything already linked), and the agent is told how to write Linear ids
back so a second run never creates duplicates.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path

from mgr.model import PRIORITIES, Idea, Plan, Status


def _fmt_points(points: float | None) -> str:
    if points is None:
        return "unestimated"
    return f"{points:g}"


def mgr_command() -> str:
    """How an agent in an arbitrary working directory should invoke mgr.

    Use an absolute path to the `mgr` script: its shebang pins the right
    interpreter, so it works from any directory — unlike `uv run mgr`, which
    only works inside the repo and, with asdf, only where a uv version is
    pinned.

    Prefer the script that is running right now: the installer invokes it by
    full path before it is necessarily on PATH, and a PATH lookup could find
    a different install. Fall back to PATH when not running as the script
    (e.g. under pytest).
    """
    running = Path(sys.argv[0])
    if running.name == "mgr" and running.is_absolute() and running.exists():
        return shlex.quote(str(running))
    found = shutil.which("mgr")
    return shlex.quote(found) if found else "mgr"


def _ticket_order(tickets: list[Idea]) -> list[Idea]:
    """Order tickets so every parent precedes its children, otherwise keeping rank.

    The agent needs a parent's Linear id before it can create a sub-issue, so
    listing them in dependency order saves it from having to work this out.
    """
    by_id = {t.id: t for t in tickets}
    ordered: list[Idea] = []
    seen: set[int] = set()

    def visit(t: Idea, stack: frozenset[int]) -> None:
        if t.id in seen or t.id in stack:  # `stack` breaks accidental cycles
            return
        if t.parent in by_id:
            visit(by_id[t.parent], stack | {t.id})
        seen.add(t.id)
        ordered.append(t)

    for t in tickets:
        visit(t, frozenset())
    return ordered


def _ticket_block(idea: Idea, plan: Plan, all_ids: dict[int, Idea]) -> str:
    lines = [f"### mgr-{idea.id}: {idea.title}", ""]
    fields = {
        "priority": f"{idea.priority} ({PRIORITIES.get(idea.priority, '?')})",
        "estimate": _fmt_points(idea.estimate),
        "kind": idea.kind,
        "labels": ", ".join(idea.labels) or "-",
        "assignee": idea.assignee or "-",
        "project": idea.project or plan.linear_project or "-",
    }
    if idea.parent is not None:
        parent = all_ids.get(idea.parent)
        if parent is None:
            ref = f"mgr-{idea.parent} (missing)"
        elif parent.linear_id:
            ref = f"{parent.linear_id} (already in Linear)"
        else:
            ref = f"mgr-{parent.id}"
        fields["parent"] = ref
    elif idea.parent_linear_id:
        fields["parent"] = f"{idea.parent_linear_id} (already in Linear)"
    lines += [f"- **{k}**: {v}" for k, v in fields.items()]
    lines.append("")
    lines.append(idea.notes.strip() or "_No description._")
    lines.append("")
    return "\n".join(lines)


def render(plan: Plan, plan_path: Path, conventions: str = "") -> str:
    """`conventions` is free-form Markdown from the plans folder's agent
    instructions file (see `store.load_instructions`)."""
    all_ids = {i.id: i for i in plan.ideas}
    committed = plan.in_status(Status.COMMITTED)
    to_create = _ticket_order([i for i in committed if not i.linear_id])
    already = [i for i in committed if i.linear_id]

    team = plan.linear_team or "(not set — ask the user which team)"
    plan_arg = shlex.quote(str(plan_path.resolve()))
    link_cmd = f"{mgr_command()} link --plan {plan_arg} <mgr-number> <LINEAR-ID>"

    # Pointing at the conventions from the top matters: they come after the
    # numbered steps, and an agent skimming the steps could otherwise miss them.
    conventions_note = (
        "\nAlso follow **Team conventions** below; where they conflict with these\nsteps, the conventions win.\n"
        if conventions
        else ""
    )

    out: list[str] = []
    out.append(f"# Planning handoff: {plan.name}")
    out.append("")
    out.append(f"- **Window**: {plan.start} → {plan.end}")
    out.append(f"- **Linear team**: {team}")
    if plan.linear_project:
        out.append(f"- **Default Linear project**: {plan.linear_project}")
    cap = f"{plan.capacity:g}" if plan.capacity else "not set"
    out.append(f"- **Capacity**: {cap} points · **Committed**: {plan.committed_points():g} points")
    out.append("")

    if plan.goals:
        out.append("## Goals for this window")
        out.append("")
        out += [f"- {g}" for g in plan.goals]
        out.append("")

    out.append("## Instructions for the assistant")
    out.append("")
    out.append(
        f"""\
You are turning this plan into Linear issues using the Linear MCP tools.
{conventions_note}
1. Before creating anything, show the user a short list of the tickets you
   are about to create (title, estimate, assignee, parent) and anything you
   can't map, then wait for their go-ahead.
2. Create exactly one Linear issue per entry under **Tickets to create**, in the
   order listed, in team **{team}**. Do not create issues for anything under
   **Context only**.
3. Use the heading text after `mgr-N:` as the issue title and the body below
   the field list as the description (Markdown).
4. Field mapping:
   - `priority` → Linear priority (0 None, 1 Urgent, 2 High, 3 Medium, 4 Low).
   - `estimate` → issue estimate; omit if `unestimated`.
   - `labels` → existing team labels matched by name. Also apply `kind` as a
     label if a label with that name already exists. Do not create new labels
     without asking.
   - `assignee` → match against workspace users by name. If there is no
     single clear match, leave the issue unassigned and mention it at the end.
   - `project` → Linear project matched by name; omit if `-`.
   - `parent` → if it is `mgr-N`, use the Linear id you created for that
     entry earlier in this run; if it is already a Linear id, use that.
5. Immediately after creating each issue, record its id by running:
   `{link_cmd}`
   (e.g. `... link 12 ENG-345`). This makes re-running the handoff safe.
6. Finish with a table of mgr number → Linear id → title, plus anything you
   skipped or could not map."""
    )
    out.append("")

    if conventions:
        out.append("## Team conventions")
        out.append("")
        out.append(conventions)
        out.append("")

    out.append(f"## Tickets to create ({len(to_create)})")
    out.append("")
    if not to_create:
        out.append("_Nothing to create — every committed idea is already linked._")
        out.append("")
    out += [_ticket_block(i, plan, all_ids) for i in to_create]

    out.append("## Context only — do not create tickets")
    out.append("")
    if already:
        out.append("### Already in Linear")
        out.append("")
        out += [f"- {i.linear_id}: {i.title}" for i in already]
        out.append("")
    for status, heading in [(Status.MAYBE, "Maybe (not committed)"), (Status.PARKED, "Parked for later")]:
        items = plan.in_status(status)
        if items:
            out.append(f"### {heading}")
            out.append("")
            out += [f"- mgr-{i.id}: {i.title}" for i in items]
            out.append("")

    return "\n".join(out).rstrip() + "\n"
