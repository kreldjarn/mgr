"""Command-line entry point.

    mgr                       open the plan picker for the plans folder
    mgr --plan P              open plan P directly on the board
    mgr ls                    list plans (current one marked *)
    mgr show                  print a plan: every idea with its id
    mgr new [--name ...]      create a plan (default: the window after the latest)
    mgr add TITLE [...]       add an idea to a plan (inbox by default)
    mgr export [-o FILE]      print (or write) the LLM handoff brief
    mgr link N LINEAR-ID      record that idea mgr-N became a Linear issue
    mgr carry [NEW]           start the next window's plan from unfinished ideas
    mgr search WORDS [--all]  find ideas and notes containing all WORDS
    mgr notes                 list notes, grouped by week
    mgr note TITLE [--body B] create a note (B may be - for stdin)
    mgr append NOTE --body B  append to a note
    mgr promote NOTE [--into PLAN]   turn a note into a new plan, or add it to PLAN
    mgr instructions          print the path of the agent instructions file
    mgr skill [NAME] [--install]   print (or install) the Claude Code skills

Commands that need a plan use --plan, or else the current plan in the folder:
the one whose window contains today, otherwise the latest.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from mgr import export, model, notes, search, skill, store


def _idea_ref(value: str) -> int:
    # Accept both "12" and "mgr-12", since the handoff brief shows the latter
    # and an LLM will happily copy whichever form it sees.
    try:
        return int(value.removeprefix("mgr-"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an idea number like 12 or mgr-12, got {value!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mgr", description="Spitball a planning window, then hand it to Linear.")
    parser.add_argument("--dir", type=Path, help=f"plans folder (default: $MGR_DIR or {store.default_dir()})")
    parser.add_argument("--plan", type=Path, help="plan file, or a plan name inside the plans folder")

    # Subcommands also accept --dir/--plan (the handoff brief puts --plan after
    # the subcommand). SUPPRESS stops their default from clobbering the
    # top-level value, which is argparse's behaviour when both define a dest.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dir", type=Path, default=argparse.SUPPRESS, help="plans folder")
    common.add_argument("--plan", type=Path, default=argparse.SUPPRESS, help="plan file or name")

    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("ls", parents=[common], help="list plans")
    sub.add_parser("show", parents=[common], help="print a plan with every idea and its id")
    new = sub.add_parser("new", parents=[common], help="create a plan (default: window after the latest)")
    new.add_argument("--name", help="plan name")
    new.add_argument("--start", type=_iso_date, help="YYYY-MM-DD (default: day after the latest plan ends)")
    new.add_argument("--end", type=_iso_date, help="YYYY-MM-DD (default: same length as the latest plan)")
    new.add_argument("--weeks", help="ISO week numbers instead of dates, e.g. 45 or 45-46")
    new.add_argument("--goal", action="append", default=[], help="a goal (repeatable)")
    new.add_argument("--capacity", type=float, help="team points for the window")
    new.add_argument("--team", help="Linear team name or key")
    add = sub.add_parser("add", parents=[common], help="add an idea to a plan")
    add.add_argument("title", help="idea title")
    add.add_argument("--notes", help="description (Markdown); - reads stdin")
    add.add_argument("--status", choices=[s.value for s in model.BOARD_ORDER], default="inbox")
    add.add_argument("--kind", choices=model.KINDS)
    add.add_argument("--priority", type=int, choices=range(5), help="0 none, 1 urgent … 4 low")
    add.add_argument("--estimate", type=float, help="points")
    add.add_argument("--assignee")
    add.add_argument("--labels", help="comma separated")
    add.add_argument("--parent", type=_idea_ref, help="parent idea, e.g. mgr-3")
    exp = sub.add_parser("export", parents=[common], help="render the LLM handoff brief")
    exp.add_argument("-o", "--output", type=Path, help="write to FILE instead of stdout")
    link = sub.add_parser("link", parents=[common], help="record the Linear id for an idea")
    link.add_argument("idea", type=_idea_ref, help="idea number, e.g. 12 or mgr-12")
    link.add_argument("linear_id", help="Linear issue identifier, e.g. ENG-345")
    carry = sub.add_parser("carry", parents=[common], help="start the next window from unfinished ideas")
    carry.add_argument("new", type=Path, nargs="?", help="new plan file (default: <next start date>.json)")
    carry.add_argument("--open", action="store_true", help="open the new plan in the board afterwards")
    find = sub.add_parser("search", parents=[common], help="find ideas and notes containing all the words")
    find.add_argument("words", nargs="+")
    find.add_argument("--all", action="store_true", help="include archived plans and notes")
    sub.add_parser("notes", parents=[common], help="list notes")
    note = sub.add_parser("note", parents=[common], help="create a note and print its path")
    note.add_argument("title", nargs="+", help="note title")
    note.add_argument("--body", help="note content (Markdown); - reads stdin")
    append = sub.add_parser("append", parents=[common], help="append text to a note")
    append.add_argument("note", help="note path, file name, or a unique part of its name")
    append.add_argument("--body", required=True, help="text to append (Markdown); - reads stdin")
    promote = sub.add_parser("promote", parents=[common], help="turn a note into a plan")
    promote.add_argument("note", help="note path, file name, or a unique part of its name")
    promote.add_argument("--into", type=Path, help="add to this existing plan instead of creating one")
    sub.add_parser("instructions", parents=[common], help="create if needed and print the agent instructions path")
    sk = sub.add_parser("skill", parents=[common], help="print or install the Claude Code skills")
    sk.add_argument("name", nargs="?", choices=sorted(skill.SKILLS), help="print just this skill")
    sk.add_argument("--install", action="store_true", help=f"install all skills under {skill.skills_root()}")
    return parser


def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None


def _text_arg(value: str | None) -> str | None:
    # "-" means stdin, so agents can pass multi-line Markdown via a heredoc
    # without fighting shell quoting.
    return sys.stdin.read() if value == "-" else value


def _resolve_plan(arg: Path | None, folder: Path) -> Path | None:
    if arg is None:
        return store.current_plan(folder)
    if arg.exists() or arg.is_absolute() or len(arg.parts) > 1:
        return arg
    # A bare name like "2026-10-19" means a plan inside the plans folder.
    candidate = folder / (arg.name if arg.suffix == ".json" else f"{arg.name}.json")
    return candidate if candidate.exists() else arg


def _resolve_note(ref: str, folder: Path) -> Path | None:
    """Accept a path, an exact file name/stem, or a unique substring of one."""
    if Path(ref).is_file():
        return Path(ref)
    candidates = [n.path for n in notes.list_notes(folder)]
    exact = [p for p in candidates if ref in (p.name, p.stem)]
    if exact:
        return exact[0]
    partial = [p for p in candidates if ref.lower() in p.name.lower()]
    return partial[0] if len(partial) == 1 else None


def _print_carry_summary(old: model.Plan, nxt: model.Plan, new_path: Path) -> None:
    carried = {i.id for i in nxt.ideas}
    left = [i for i in old.ideas if i.id not in carried]
    ticketed = sum(1 for i in left if i.linear_id)
    print(f"{new_path}: {model.window_label(nxt.start, nxt.end)}, carried {len(carried)} idea(s)")
    for status in model.BOARD_ORDER:
        if n := len(nxt.in_status(status)):
            print(f"  {status:<9} {n}")
    demoted = [i for i in old.in_status(model.Status.COMMITTED) if i.id in carried]
    if demoted:
        print(f"  ({len(demoted)} committed but never ticketed → moved back to maybe)")
    print(f"left behind: {ticketed} already in Linear, {len(left) - ticketed} dropped")
    slipping = [i for i in nxt.ideas if i.carry_count >= 3]
    if slipping:
        print("slipping (carried 3+ times):")
        for i in slipping:
            print(f"  mgr-{i.id} ⟳{i.carry_count} {i.title}")


def _fail(msg: str) -> int:
    print(f"mgr: {msg}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # An existing --plan file implies its own folder, so `mgr --plan ~/x/a.json`
    # shows ~/x in the picker, matching where export reads conventions from.
    if args.dir:
        folder: Path = args.dir
    elif args.plan and args.plan.exists():
        folder = args.plan.resolve().parent
    else:
        folder = store.default_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = _resolve_plan(args.plan, folder)

    if args.cmd == "ls":
        current = store.current_plan(folder)
        for f in store.list_plans(folder):
            mark = "*" if f.path == current else " "
            if f.plan is None:
                print(f"{mark} {f.path.name:<20} UNREADABLE: {f.error}")
            else:
                p = f.plan
                print(f"{mark} {f.path.name:<20} {model.window_label(p.start, p.end)}  {p.name}  ({store.summary(p)})")
        return 0

    if args.cmd == "new":
        start, end = args.start, args.end
        try:
            if args.weeks:
                start, end = model.weeks_to_window(args.weeks)
            new_path = store.create_plan(folder, start, end)
        except ValueError as exc:
            return _fail(str(exc))
        plan = model.load(new_path)
        if args.name:
            plan.name = args.name
        plan.goals = args.goal or plan.goals
        if args.capacity is not None:
            plan.capacity = args.capacity
        if args.team:
            plan.linear_team = args.team
        model.save(plan, new_path)
        print(f"{new_path}  ({model.window_label(plan.start, plan.end)}, {plan.name})")
        return 0

    if args.cmd == "search":
        hits = search.search(folder, " ".join(args.words), include_archived=args.all)
        for hit in hits:
            where = hit.label if hit.kind == "idea" else f"note {hit.path.name} · {hit.label}"
            print(where)
            if hit.snippet and hit.snippet not in hit.label:
                print(f"    {hit.snippet}")
        if not hits:
            print("no matches", file=sys.stderr)
        return 0 if hits else 1

    if args.cmd == "notes":
        for i, (_, week) in enumerate(notes.group_by_week(notes.list_notes(folder))):
            print(("\n" if i else "") + notes.week_heading(week[0].created))
            for n in week:
                promoted = f"  → {', '.join(n.promoted_to)}" if n.promoted_to else ""
                print(f"  {n.path.name:<45} {n.title}{promoted}")
        return 0

    if args.cmd == "note":
        note_path = notes.create_note(folder, " ".join(args.title))
        if body := _text_arg(args.body):
            notes.append_note(note_path, body)
        print(note_path)
        return 0

    if args.cmd == "append":
        note_path = _resolve_note(args.note, folder)
        if note_path is None:
            return _fail(f"no single note matches {args.note!r}; see `mgr notes`")
        notes.append_note(note_path, _text_arg(args.body) or "")
        print(f"appended to {note_path}")
        return 0

    if args.cmd == "promote":
        note_path = _resolve_note(args.note, folder)
        if note_path is None:
            return _fail(f"no single note matches {args.note!r}; see `mgr notes`")
        into = _resolve_plan(args.into, folder) if args.into else None
        if into is not None and not into.exists():
            return _fail(f"no plan at {into}")
        plan_path, ex = notes.promote(note_path, into, folder)
        print(f"{note_path.name} → {plan_path}: {len(ex.ideas)} idea(s) to inbox, {len(ex.goals)} goal(s)")
        return 0

    if args.cmd == "instructions":
        print(store.ensure_instructions(folder))
        return 0

    if args.cmd == "skill":
        if args.install:
            for installed in skill.install_all(folder):
                print(f"installed {installed}", file=sys.stderr)
        elif args.name:
            sys.stdout.write(skill.SKILLS[args.name](folder))
        else:
            print("\n\n".join(render(folder) for render in skill.SKILLS.values()), end="")
        return 0

    if args.cmd in ("export", "link", "carry", "show", "add"):
        if path is None:
            return _fail(f"no plans in {folder}; create one with `mgr new`")
        if not path.exists():
            return _fail(f"no plan at {path}")

    if args.cmd == "export":
        text = export.render(model.load(path), path, store.load_instructions(path.parent))
        if args.output:
            args.output.write_text(text)
            print(f"wrote {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0

    if args.cmd == "show":
        sys.stdout.write(export.overview(model.load(path)))
        return 0

    if args.cmd == "add":
        plan = model.load(path)
        if args.parent is not None and plan.get(args.parent) is None:
            return _fail(f"no idea mgr-{args.parent} in {path}")
        idea = plan.add(args.title, model.Status(args.status))
        idea.notes = _text_arg(args.notes) or ""
        idea.kind = args.kind or idea.kind
        idea.priority = args.priority if args.priority is not None else idea.priority
        idea.estimate = args.estimate
        idea.assignee = args.assignee or ""
        idea.labels = [s.strip() for s in (args.labels or "").split(",") if s.strip()]
        idea.parent = args.parent
        model.save(plan, path)
        print(f"added mgr-{idea.id} to {idea.status} in {path.name}: {idea.title}")
        return 0

    if args.cmd == "link":
        plan = model.load(path)
        idea = plan.get(args.idea)
        if idea is None:
            return _fail(f"no idea mgr-{args.idea} in {path}")
        if idea.linear_id and idea.linear_id != args.linear_id:
            print(f"mgr: note: mgr-{idea.id} was linked to {idea.linear_id}, overwriting", file=sys.stderr)
        idea.linear_id = args.linear_id
        model.save(plan, path)
        print(f"mgr-{idea.id} → {args.linear_id}: {idea.title}")
        return 0

    if args.cmd == "carry":
        try:
            old, nxt, new_path = store.carry(path, args.new)
        except FileExistsError:
            return _fail(f"{args.new} already exists; pass a different path")
        _print_carry_summary(old, nxt, new_path)
        if not args.open:
            return 0
        path = new_path

    # Imported lazily so the subcommands (often run by an agent) stay fast.
    from mgr.app import MgrApp

    # Without an explicit --plan (or `carry --open`), start on the picker.
    open_path = path if (args.plan or args.cmd == "carry") else None
    MgrApp(folder, open_path).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
