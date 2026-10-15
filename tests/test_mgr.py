import asyncio
import json
from pathlib import Path

from mgr import export, model, skill, store
from mgr.app import BoardScreen, MgrApp, PlanListScreen
from mgr.cli import main
from mgr.model import Plan, Status


def make_plan() -> Plan:
    plan = Plan(name="Cycle 1", linear_team="ENG", capacity=10)
    epic = plan.add("Eval harness", Status.COMMITTED)
    child = plan.add("Golden dataset", Status.COMMITTED)
    child.parent = epic.id
    child.estimate = 3
    done = plan.add("Already ticketed", Status.COMMITTED)
    done.linear_id = "ENG-1"
    plan.add("Fancy idea", Status.MAYBE)
    plan.add("Raw thought", Status.INBOX)
    return plan


def test_roundtrip_and_unknown_keys(tmp_path):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)
    data = json.loads(path.read_text())
    data["ideas"][0]["from_the_future"] = 1
    data["next_id"] = 1  # simulate a hand edit that broke the counter
    path.write_text(json.dumps(data))
    plan = model.load(path)
    assert plan.ideas[0].status is Status.COMMITTED
    assert plan.next_id == 6


def test_reorder_within_column_skips_other_statuses():
    plan = make_plan()
    plan.move_within_status(3, -1)  # "Already ticketed" up past "Golden dataset"
    assert [i.id for i in plan.in_status(Status.COMMITTED)] == [1, 3, 2]
    plan.move_within_status(1, -1)  # already first: no-op
    assert [i.id for i in plan.in_status(Status.COMMITTED)] == [1, 3, 2]


def test_export_orders_parents_first_and_skips_linked(tmp_path):
    plan = make_plan()
    # Put the child ahead of its parent in rank to prove reordering happens.
    plan.move_within_status(2, -1)
    text = export.render(plan, tmp_path / "plan.json")
    create = text.split("## Tickets to create")[1].split("## Context only")[0]
    assert create.index("mgr-1:") < create.index("mgr-2:")
    assert "mgr-3" not in create
    assert "**parent**: mgr-1" in create
    assert "ENG-1: Already ticketed" in text
    assert "Fancy idea" in text
    assert "Raw thought" not in text  # inbox is too raw to share


def test_cli_link_and_export(tmp_path, capsys):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)
    assert main(["link", "--plan", str(path), "mgr-2", "ENG-7"]) == 0
    assert model.load(path).get(2).linear_id == "ENG-7"
    assert main(["--plan", str(path), "link", "99", "ENG-8"]) == 1
    capsys.readouterr()
    assert main(["export", "--plan", str(path)]) == 0
    assert "Tickets to create (1)" in capsys.readouterr().out


def test_tui_quick_add_move_and_rank(tmp_path):
    path = tmp_path / "plan.json"

    async def run():
        app = MgrApp(tmp_path, path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("n", *"first", "enter", *"second", "enter", "escape")
            plan = model.load(path)
            assert [i.title for i in plan.in_status(Status.INBOX)] == ["first", "second"]

            # Cursor sits on "second"; rank it up, then move it two columns right.
            await pilot.press("K")
            assert [i.title for i in model.load(path).in_status(Status.INBOX)] == ["second", "first"]
            await pilot.press("L", "shift+right")
            assert model.load(path).get(2).status is Status.COMMITTED

            await pilot.press("p", "p")
            assert model.load(path).get(2).priority == 3

            await pilot.press("d")
            assert model.load(path).get(2).status is Status.DROPPED

    asyncio.run(run())


def test_tui_absorbs_external_link(tmp_path):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)

    async def run():
        app = MgrApp(tmp_path, path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            # Simulate an agent running `mgr link` while the board is open,
            # then a local edit landing before the poll notices.
            main(["link", "--plan", str(path), "1", "ENG-50"])
            assert isinstance(app.screen, BoardScreen)
            await pilot.press("n", *"new", "enter", "escape")
            plan = model.load(path)
            assert plan.get(1).linear_id == "ENG-50"
            assert any(i.title == "new" for i in plan.ideas)

    asyncio.run(run())


def test_tui_editor_saves_fields(tmp_path):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)

    async def run():
        app = MgrApp(tmp_path, path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            await pilot.press("right", "right", "e")  # Committed column, first idea
            await pilot.pause()
            editor = app.screen
            editor.query_one("#estimate").value = "8"
            editor.query_one("#labels").value = "eval, retrieval"
            await pilot.press("ctrl+s")
            await pilot.pause()
            idea = model.load(path).get(1)
            assert idea.estimate == 8 and idea.labels == ["eval", "retrieval"]

            # Invalid estimate keeps the editor open and changes nothing.
            await pilot.press("e")
            await pilot.pause()
            app.screen.query_one("#estimate").value = "lots"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert not isinstance(app.screen, BoardScreen)
            await pilot.press("escape", "y")  # dirty editor: confirm discard
            assert model.load(path).get(1).estimate == 8

            await pilot.press("s")
            await pilot.pause()
            app.screen.query_one("#capacity").value = "25"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert model.load(path).capacity == 25

    asyncio.run(run())


def test_tui_vim_motions_undo_and_escape_guard(tmp_path):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)

    async def run():
        app = MgrApp(tmp_path, path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            board = app.screen
            await pilot.press("$")
            assert board.focused_list().status is Status.PARKED
            await pilot.press("0", "l", "l", "G")
            assert board.current_idea().id == 3
            await pilot.press("g")
            assert board.current_idea().id == 1

            # Undo/redo round-trips a move, and undo keeps Linear ids.
            await pilot.press("L")
            assert model.load(path).get(1).status is Status.PARKED
            main(["link", "--plan", str(path), "1", "ENG-9"])
            await pilot.press("u")
            plan = model.load(path)
            assert plan.get(1).status is Status.COMMITTED and plan.get(1).linear_id == "ENG-9"
            await pilot.press("ctrl+r")
            assert model.load(path).get(1).status is Status.PARKED

            # esc in a dirty editor asks first; "n" keeps you editing.
            await pilot.press("i")
            await pilot.pause()
            app.screen.query_one("#title").value = "Renamed"
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert model.load(path).get(1).title == "Renamed"

            # A clean editor closes on esc with no prompt.
            await pilot.press("i")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is board

    asyncio.run(run())


def test_carry_over(tmp_path):
    plan = make_plan()  # 1,2 committed unticketed (2 is child of 1); 3 ticketed; 4 maybe; 5 inbox
    plan.start, plan.end = "2026-10-19", "2026-11-01"
    under_ticketed = plan.add("Follow-up to ticketed work", Status.MAYBE)
    under_ticketed.parent = 3
    plan.add("Bad idea", Status.DROPPED)
    plan.add("Someday", Status.PARKED).carry_count = 2

    nxt = model.carry_over(plan)
    assert (nxt.start, nxt.end) == ("2026-11-02", "2026-11-15")
    assert nxt.linear_team == "ENG" and nxt.capacity == 10
    assert sorted(i.id for i in nxt.ideas) == [1, 2, 4, 5, 6, 8]
    assert nxt.get(1).status is Status.MAYBE  # committed but never ticketed
    assert nxt.get(2).parent == 1  # both carried: local link kept
    assert nxt.get(6).parent is None and nxt.get(6).parent_linear_id == "ENG-1"
    assert nxt.get(8).status is Status.PARKED and nxt.get(8).carry_count == 3
    assert nxt.next_id == plan.next_id
    # The source plan is untouched.
    assert plan.get(1).status is Status.COMMITTED and plan.get(1).carry_count == 0

    # A Linear-only parent shows up in the handoff once re-committed.
    nxt.get(6).status = Status.COMMITTED
    assert "**parent**: ENG-1 (already in Linear)" in export.render(nxt, tmp_path / "n.json")


def test_cli_carry(tmp_path, capsys):
    path = tmp_path / "plan.json"
    plan = make_plan()
    plan.start, plan.end = "2026-10-19", "2026-11-01"
    model.save(plan, path)
    assert main(["carry", "--plan", str(path)]) == 0
    new_path = tmp_path / "2026-11-02.json"
    assert len(model.load(new_path).ideas) == 4
    out = capsys.readouterr().out
    assert "2 committed but never ticketed" in out and "1 already in Linear" in out
    # A second carry gets a fresh name; an explicit existing target is refused.
    assert main(["carry", "--plan", str(path)]) == 0
    assert (tmp_path / "2026-11-02-2.json").exists()
    assert main(["carry", "--plan", str(path), str(new_path)]) == 1


def test_store_lifecycle(tmp_path):
    from datetime import date

    assert store.current_plan(tmp_path) is None
    first = store.create_plan(tmp_path)
    plan = model.load(first)
    plan.start, plan.end, plan.linear_team, plan.goals = "2026-10-19", "2026-11-01", "ENG", ["g"]
    model.save(plan, first)
    first = first.rename(tmp_path / "2026-10-19.json")

    second = store.create_plan(tmp_path)  # follows the latest window, copies team settings, not goals
    nxt = model.load(second)
    assert second.name == "2026-11-02.json" and nxt.linear_team == "ENG" and nxt.goals == []

    (tmp_path / "broken.json").write_text("{not json")
    files = store.list_plans(tmp_path)
    assert [f.path.name for f in files] == ["2026-11-02.json", "2026-10-19.json", "broken.json"]
    assert files[-1].plan is None and files[-1].error

    assert store.current_plan(tmp_path, today=date(2026, 10, 20)) == first
    assert store.current_plan(tmp_path, today=date(2026, 10, 1)) == first  # next upcoming
    assert store.current_plan(tmp_path, today=date(2027, 1, 1)) == second  # all past: latest

    first.with_suffix(".handoff.md").write_text("x")
    store.archive(first)
    assert (tmp_path / "archive" / "2026-10-19.json").exists()
    assert (tmp_path / "archive" / "2026-10-19.handoff.md").exists()
    store.delete(second)
    assert [f.path.name for f in store.list_plans(tmp_path)] == ["broken.json"]


def test_agent_instructions_in_brief(tmp_path):
    path = tmp_path / "plan.json"
    model.save(make_plan(), path)
    # The fresh template is all comment, so it adds nothing.
    store.ensure_instructions(tmp_path)
    assert store.load_instructions(tmp_path) == ""
    text = export.render(model.load(path), path, store.load_instructions(tmp_path))
    assert "Team conventions" not in text

    rules = store.instructions_path(tmp_path)
    rules.write_text(rules.read_text() + "\n- Every ticket goes in the active cycle.\n")
    assert store.load_instructions(tmp_path) == "- Every ticket goes in the active cycle."
    text = export.render(model.load(path), path, store.load_instructions(tmp_path))
    assert "## Team conventions\n\n- Every ticket goes in the active cycle." in text
    assert "the conventions win" in text
    assert "wait for their go-ahead" in text


def test_cli_ls_new_and_skill(tmp_path, capsys):
    assert main(["--dir", str(tmp_path), "export"]) == 1  # no plans yet
    assert main(["--dir", str(tmp_path), "new"]) == 0
    created = Path(capsys.readouterr().out.strip())
    assert main(["--dir", str(tmp_path), "ls"]) == 0
    assert capsys.readouterr().out.startswith(f"* {created.name}")
    # Bare plan names resolve inside the folder; export defaults to the current plan.
    assert main(["--dir", str(tmp_path), "export", "--plan", created.stem]) == 0
    assert main(["--dir", str(tmp_path), "export"]) == 0
    capsys.readouterr()

    target = tmp_path / "skill" / "SKILL.md"
    skill.install(tmp_path, target)
    text = target.read_text()
    assert text.startswith("---\nname: mgr-linear-handoff\n")
    assert f"--dir {tmp_path.resolve()} ls" in text


def test_tui_picker_open_back_new(tmp_path):
    plan = make_plan()
    plan.start, plan.end = "2026-10-19", "2026-11-01"
    model.save(plan, tmp_path / "2026-10-19.json")

    async def run():
        app = MgrApp(tmp_path)
        async with app.run_test(size=(140, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, PlanListScreen)
            await pilot.press("enter")
            assert isinstance(app.screen, BoardScreen)
            await pilot.press("n", *"from board", "enter", "escape", "minus")
            assert isinstance(app.screen, PlanListScreen)
            assert "6 ideas" in str(app.screen.query_one("#plans").get_option_at_index(0).prompt)

            await pilot.press("c")  # carry over → new file, selected
            assert app.screen.selected().path.name == "2026-11-02.json"
            await pilot.press("A")
            await pilot.pause()
            app.screen.query_one("#instructions").text = "- Use the active cycle."
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert store.load_instructions(tmp_path) == "- Use the active cycle."

            await pilot.press("D", "y")
            assert [f.path.name for f in store.list_plans(tmp_path)] == ["2026-10-19.json"]

    asyncio.run(run())
