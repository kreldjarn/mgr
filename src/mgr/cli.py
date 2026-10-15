"""Command-line entry point.

    mgr                       open the plan picker for the plans folder
    mgr --plan P              open plan P directly on the board
    mgr ls                    list plans (current one marked *)
    mgr new                   create an empty plan for the next window
    mgr export [-o FILE]      print (or write) the LLM handoff brief
    mgr link N LINEAR-ID      record that idea mgr-N became a Linear issue
    mgr carry [NEW]           start the next window's plan from unfinished ideas
    mgr instructions          print the path of the agent instructions file
    mgr skill [--install]     print (or install) the Claude Code skill

Commands that need a plan use --plan, or else the current plan in the folder:
the one whose window contains today, otherwise the latest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mgr import export, model, skill, store


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
    sub.add_parser("new", parents=[common], help="create an empty plan for the next window")
    exp = sub.add_parser("export", parents=[common], help="render the LLM handoff brief")
    exp.add_argument("-o", "--output", type=Path, help="write to FILE instead of stdout")
    link = sub.add_parser("link", parents=[common], help="record the Linear id for an idea")
    link.add_argument("idea", type=_idea_ref, help="idea number, e.g. 12 or mgr-12")
    link.add_argument("linear_id", help="Linear issue identifier, e.g. ENG-345")
    carry = sub.add_parser("carry", parents=[common], help="start the next window from unfinished ideas")
    carry.add_argument("new", type=Path, nargs="?", help="new plan file (default: <next start date>.json)")
    carry.add_argument("--open", action="store_true", help="open the new plan in the board afterwards")
    sub.add_parser("instructions", parents=[common], help="create if needed and print the agent instructions path")
    sk = sub.add_parser("skill", parents=[common], help="print the Claude Code skill for ticket creation")
    sk.add_argument("--install", action="store_true", help=f"write it to {skill.default_install_path()}")
    return parser


def _resolve_plan(arg: Path | None, folder: Path) -> Path | None:
    if arg is None:
        return store.current_plan(folder)
    if arg.exists() or arg.is_absolute() or len(arg.parts) > 1:
        return arg
    # A bare name like "2026-10-19" means a plan inside the plans folder.
    candidate = folder / (arg.name if arg.suffix == ".json" else f"{arg.name}.json")
    return candidate if candidate.exists() else arg


def _print_carry_summary(old: model.Plan, nxt: model.Plan, new_path: Path) -> None:
    carried = {i.id for i in nxt.ideas}
    left = [i for i in old.ideas if i.id not in carried]
    ticketed = sum(1 for i in left if i.linear_id)
    print(f"{new_path}: {nxt.start} → {nxt.end}, carried {len(carried)} idea(s)")
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
                print(f"{mark} {f.path.name:<20} {p.start} → {p.end}  {p.name}  ({store.summary(p)})")
        return 0

    if args.cmd == "new":
        print(store.create_plan(folder))
        return 0

    if args.cmd == "instructions":
        print(store.ensure_instructions(folder))
        return 0

    if args.cmd == "skill":
        if args.install:
            print(f"installed {skill.install(folder)}", file=sys.stderr)
        else:
            sys.stdout.write(skill.render(folder))
        return 0

    if args.cmd in ("export", "link", "carry"):
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
