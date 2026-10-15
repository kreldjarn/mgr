"""Textual TUI: a kanban board for spitballing a planning window."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Input, Label, Markdown, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from mgr import export, model, store
from mgr.model import BOARD_ORDER, KINDS, PRIORITIES, Idea, Plan, Status

PRIORITY_STYLE = {0: ("  ", ""), 1: ("!!", "bold red"), 2: ("↑ ", "dark_orange"), 3: ("= ", "yellow"), 4: ("↓ ", "dim")}

COLUMN_TITLES = {
    Status.INBOX: "Inbox",
    Status.MAYBE: "Maybe",
    Status.COMMITTED: "Committed",
    Status.PARKED: "Parked",
    Status.DROPPED: "Dropped",
}


def idea_label(idea: Idea) -> Text:
    glyph, style = PRIORITY_STYLE.get(idea.priority, ("? ", ""))
    text = Text.assemble((glyph, style), " ")
    if idea.parent is not None or idea.parent_linear_id:
        text.append("↳ ", "dim")
    text.append(idea.title)
    if idea.carry_count:
        text.append(f" ⟳{idea.carry_count}", "magenta")
    if idea.estimate is not None:
        text.append(f" {idea.estimate:g}p", "dim cyan")
    if idea.linear_id:
        text.append(f" {idea.linear_id}", "green")
    return text


def idea_markdown(idea: Idea, plan: Plan) -> str:
    meta = [
        f"**{idea.status}**",
        idea.kind,
        f"P{idea.priority} {PRIORITIES.get(idea.priority, '')}",
        f"{idea.estimate:g} pts" if idea.estimate is not None else "unestimated",
    ]
    if idea.assignee:
        meta.append(f"@{idea.assignee}")
    if idea.labels:
        meta.append(" ".join(f"`{label}`" for label in idea.labels))
    if idea.project:
        meta.append(f"project: {idea.project}")
    if idea.parent is not None:
        parent = plan.get(idea.parent)
        meta.append(f"parent: mgr-{idea.parent}" + (f" {parent.title}" if parent else " (missing)"))
    elif idea.parent_linear_id:
        meta.append(f"parent: {idea.parent_linear_id}")
    if idea.carry_count:
        meta.append(f"carried over {idea.carry_count}×")
    if idea.linear_id:
        meta.append(f"Linear: **{idea.linear_id}**")
    body = idea.notes.strip() or "_No notes yet — press `e` to edit._"
    return f"### mgr-{idea.id}: {idea.title}\n\n{' · '.join(meta)}\n\n{body}"


def plan_markdown(plan: Plan) -> str:
    goals = "\n".join(f"- {g}" for g in plan.goals) or "_No goals yet — press `s` to set them._"
    return f"### {plan.name}\n\n{plan.start} → {plan.end}\n\n**Goals**\n\n{goals}"


def tilde(path: Path) -> str:
    """Shorten paths under $HOME for display."""
    try:
        return "~/" + str(path.resolve().relative_to(Path.home()))
    except ValueError:
        return str(path)


class IdeaList(OptionList):
    """One board column. Carries its status so actions know where they are."""

    def __init__(self, status: Status) -> None:
        super().__init__(id=f"list-{status}")
        self.status = status


class Column(Vertical):
    def __init__(self, status: Status) -> None:
        super().__init__(id=f"col-{status}", classes="column")
        self.status = status

    def compose(self) -> ComposeResult:
        yield Static(COLUMN_TITLES[self.status], classes="col-header")
        yield IdeaList(self.status)


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("y", "answer(True)", "Yes"), Binding("n,escape", "answer(False)", "No")]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog small"):
            yield Label(self.question)
            yield Label("[b]y[/b] yes   [b]n[/b] no", classes="hint")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class FormScreen(ModalScreen[bool]):
    """Base for editors: ctrl+s saves, esc cancels — but asks first if dirty.

    The guard matters for vim users, who hit esc reflexively to leave
    "insert mode" and would otherwise silently lose everything they typed.
    """

    BINDINGS = [Binding("ctrl+s", "save", "Save", priority=True), Binding("escape", "cancel", "Cancel")]

    def on_mount(self) -> None:
        self._initial = self._values()

    def _values(self) -> list:
        return [w.text if isinstance(w, TextArea) else w.value for w in self.query("Input, Select, TextArea")]

    def action_save(self) -> None:
        raise NotImplementedError

    def action_cancel(self) -> None:
        if self._values() == self._initial:
            self.dismiss(False)
            return

        def done(discard: bool | None) -> None:
            if discard:
                self.dismiss(False)

        self.app.push_screen(ConfirmScreen("Discard unsaved changes?"), done)


class IdeaEditor(FormScreen):
    """Full editor for one idea. Dismisses True if the idea was changed."""

    def __init__(self, idea: Idea, plan: Plan) -> None:
        super().__init__()
        self.idea = idea
        self.plan = plan

    def compose(self) -> ComposeResult:
        idea = self.idea
        # Only offer parents that could plausibly become Linear issues, and
        # never the idea itself (a self-parent would loop in the export).
        parents = [
            (f"mgr-{i.id} {i.title}", i.id) for i in self.plan.ideas if i.id != idea.id and i.status != Status.DROPPED
        ]
        with VerticalScroll(classes="dialog"):
            yield Label(f"Edit mgr-{idea.id}", classes="title")
            yield Input(idea.title, placeholder="Title", id="title")
            with Grid(classes="fields"):
                yield Label("Status")
                yield Select(
                    [(COLUMN_TITLES[s], s) for s in BOARD_ORDER],
                    value=idea.status,
                    allow_blank=False,
                    id="status",
                    compact=True,
                )
                yield Label("Kind")
                yield Select(
                    [(k, k) for k in KINDS],
                    value=idea.kind if idea.kind in KINDS else KINDS[0],
                    allow_blank=False,
                    id="kind",
                    compact=True,
                )
                yield Label("Priority")
                yield Select(
                    [(f"{n} {name}", n) for n, name in PRIORITIES.items()],
                    value=idea.priority,
                    allow_blank=False,
                    id="priority",
                    compact=True,
                )
                yield Label("Estimate (pts)")
                yield Input(
                    "" if idea.estimate is None else f"{idea.estimate:g}",
                    placeholder="blank = unestimated",
                    id="estimate",
                    compact=True,
                )
                yield Label("Assignee")
                yield Input(idea.assignee, placeholder="name as in Linear", id="assignee", compact=True)
                yield Label("Labels")
                yield Input(", ".join(idea.labels), placeholder="comma separated", id="labels", compact=True)
                yield Label("Project")
                yield Input(
                    idea.project,
                    placeholder=f"default: {self.plan.linear_project or 'none'}",
                    id="project",
                    compact=True,
                )
                yield Label("Parent")
                yield Select(
                    parents,
                    value=idea.parent if idea.parent in {p[1] for p in parents} else Select.NULL,
                    prompt="none",
                    id="parent",
                    compact=True,
                )
                yield Label("Parent (Linear)")
                yield Input(
                    idea.parent_linear_id,
                    placeholder="parent issue already in Linear, e.g. ENG-12",
                    id="parent_linear_id",
                    compact=True,
                )
                yield Label("Linear id")
                yield Input(
                    idea.linear_id, placeholder="set by `mgr link` after creation", id="linear_id", compact=True
                )
            yield Label("Notes (Markdown — becomes the ticket description)")
            yield TextArea(idea.notes, language="markdown", soft_wrap=True, id="notes")
            yield Label("[b]ctrl+s[/b] save   [b]esc[/b] cancel", classes="hint")

    def action_save(self) -> None:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            self.notify("Title can't be empty", severity="error")
            return
        raw_estimate = self.query_one("#estimate", Input).value.strip()
        try:
            estimate = float(raw_estimate) if raw_estimate else None
        except ValueError:
            self.notify(f"Estimate must be a number, got {raw_estimate!r}", severity="error")
            return

        idea = self.idea
        idea.title = title
        idea.estimate = estimate
        idea.status = Status(self.query_one("#status", Select).value)
        idea.kind = str(self.query_one("#kind", Select).value)
        idea.priority = int(self.query_one("#priority", Select).value)
        idea.assignee = self.query_one("#assignee", Input).value.strip()
        idea.labels = [s.strip() for s in self.query_one("#labels", Input).value.split(",") if s.strip()]
        idea.project = self.query_one("#project", Input).value.strip()
        parent = self.query_one("#parent", Select).value
        # A blank Select yields a sentinel rather than None.
        idea.parent = parent if isinstance(parent, int) else None
        # Export prefers the local parent when both are set.
        idea.parent_linear_id = self.query_one("#parent_linear_id", Input).value.strip()
        idea.linear_id = self.query_one("#linear_id", Input).value.strip()
        idea.notes = self.query_one("#notes", TextArea).text
        self.dismiss(True)


class PlanEditor(FormScreen):
    def __init__(self, plan: Plan) -> None:
        super().__init__()
        self.plan = plan

    def compose(self) -> ComposeResult:
        p = self.plan
        with VerticalScroll(classes="dialog"):
            yield Label("Plan settings", classes="title")
            with Grid(classes="fields"):
                yield Label("Name")
                yield Input(p.name, id="name", compact=True)
                yield Label("Start")
                yield Input(p.start, placeholder="YYYY-MM-DD", id="start", compact=True)
                yield Label("End")
                yield Input(p.end, placeholder="YYYY-MM-DD", id="end", compact=True)
                yield Label("Capacity (pts)")
                yield Input(
                    f"{p.capacity:g}" if p.capacity else "",
                    placeholder="team points for the window",
                    id="capacity",
                    compact=True,
                )
                yield Label("Linear team")
                yield Input(p.linear_team, placeholder="team name or key, e.g. ENG", id="team", compact=True)
                yield Label("Default project")
                yield Input(p.linear_project, placeholder="optional", id="project", compact=True)
            yield Label("Goals (one per line)")
            yield TextArea("\n".join(p.goals), id="goals")
            yield Label("[b]ctrl+s[/b] save   [b]esc[/b] cancel", classes="hint")

    def action_save(self) -> None:
        def val(widget_id: str) -> str:
            return self.query_one(f"#{widget_id}", Input).value.strip()

        try:
            start, end = date.fromisoformat(val("start")), date.fromisoformat(val("end"))
        except ValueError:
            self.notify("Dates must be YYYY-MM-DD", severity="error")
            return
        if end < start:
            self.notify("End date is before start date", severity="error")
            return
        try:
            capacity = float(val("capacity") or 0)
        except ValueError:
            self.notify("Capacity must be a number", severity="error")
            return
        p = self.plan
        p.name = val("name") or p.name
        p.start, p.end = start.isoformat(), end.isoformat()
        p.capacity = capacity
        p.linear_team = val("team")
        p.linear_project = val("project")
        p.goals = [g.strip() for g in self.query_one("#goals", TextArea).text.splitlines() if g.strip()]
        self.dismiss(True)


class BoardScreen(Screen):
    # Board bindings live on the screen rather than the app so that modal
    # editors automatically shadow them (no stray moves while typing).
    BINDINGS = [
        # Vim-style aliases throughout: hjkl, g/G, 0/$, ctrl+d/u, o/i, u/ctrl+r.
        Binding("n,o", "quick_add", "New"),
        Binding("e,i", "edit", "Edit"),
        Binding("left,h", "focus_column(-1)", "←", show=False),
        Binding("right,l", "focus_column(1)", "→", show=False),
        Binding("0", "jump_column(0)", show=False),
        Binding("dollar_sign", "jump_column(-1)", show=False),
        Binding("j", "cursor('cursor_down')", show=False),
        Binding("k", "cursor('cursor_up')", show=False),
        Binding("g", "cursor('first')", show=False),
        Binding("G", "cursor('last')", show=False),
        Binding("ctrl+d", "cursor('page_down')", show=False),
        Binding("ctrl+u", "cursor('page_up')", show=False),
        Binding("shift+right,L", "shift(1)", "Move →"),
        Binding("shift+left,H", "shift(-1)", "Move ←"),
        Binding("shift+down,J", "reorder(1)", "Rank ↓", show=False),
        Binding("shift+up,K", "reorder(-1)", "Rank ↑", show=False),
        Binding("p", "cycle_priority", "Priority"),
        Binding("d", "drop", "Drop"),
        Binding("D", "delete", "Delete", show=False),
        Binding("z", "toggle_dropped", "Show dropped"),
        Binding("u", "undo", "Undo"),
        Binding("ctrl+r", "redo", show=False),
        Binding("s", "edit_plan", "Plan"),
        Binding("x", "export", "Export"),
        Binding("A", "app.edit_instructions(True)", "Agent rules", show=False),
        Binding("minus", "back", "Plans"),
        Binding("escape", "close_quick_add", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, plan: Plan, path: Path) -> None:
        super().__init__()
        self.plan = plan
        self.path = path
        # Remembered selection per column so redraws don't jump the cursor.
        self.cursor_ids: dict[Status, int | None] = {}
        self.show_dropped = False
        self.add_target = Status.INBOX
        self.last_mtime = self._disk_mtime()
        # Undo history holds whole-plan snapshots: plans are tiny, and this is
        # far simpler than per-action inverse operations.
        self.saved_state = plan.to_dict()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []

    # ----- layout -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        with Horizontal(id="board"):
            for status in BOARD_ORDER:
                yield Column(status)
        with VerticalScroll(id="detail"):
            yield Markdown(id="detail-md")
        yield Input(placeholder="New idea — enter to add, esc to stop", id="quick-add")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#quick-add").display = False
        self.query_one(f"#col-{Status.DROPPED}").display = False
        self.refresh_board()
        self.list_for(Status.INBOX).focus()
        # Poll rather than use a file watcher: `mgr link` runs in another
        # process (usually driven by an LLM agent) and polling is dependency-free.
        self.set_interval(1.5, self.check_external_changes)

    # ----- helpers ------------------------------------------------------

    def visible_statuses(self) -> list[Status]:
        return BOARD_ORDER if self.show_dropped else BOARD_ORDER[:-1]

    def list_for(self, status: Status) -> IdeaList:
        return self.query_one(f"#list-{status}", IdeaList)

    def focused_list(self) -> IdeaList | None:
        return self.focused if isinstance(self.focused, IdeaList) else None

    def current_idea(self) -> Idea | None:
        lst = self.focused_list()
        if lst is None or lst.highlighted_option is None:
            return None
        return self.plan.get(int(lst.highlighted_option.id))

    def refresh_board(self) -> None:
        for status in BOARD_ORDER:
            lst = self.list_for(status)
            ideas = self.plan.in_status(status)
            previous_index = lst.highlighted
            lst.set_options([Option(idea_label(i), id=str(i.id)) for i in ideas])
            ids = [i.id for i in ideas]
            want = self.cursor_ids.get(status)
            if want in ids:
                lst.highlighted = ids.index(want)
            elif ids:
                # The remembered idea left this column; stay at the same position.
                lst.highlighted = min(previous_index or 0, len(ids) - 1)

            points = sum(i.estimate or 0 for i in ideas)
            header = f"{COLUMN_TITLES[status]}  [dim]{len(ideas)}"
            if points:
                header += f" · {points:g}p"
            self.query_one(f"#col-{status} .col-header", Static).update(header + "[/dim]")
        self.refresh_status_bar()
        self.refresh_detail()

    def refresh_status_bar(self) -> None:
        p = self.plan
        committed = p.committed_points()
        if p.capacity:
            over = committed > p.capacity
            load = f"[{'bold red' if over else 'green'}]{committed:g}/{p.capacity:g} pts committed[/]"
        else:
            load = f"{committed:g} pts committed [dim](no capacity set)[/dim]"
        team = f" · team {p.linear_team}" if p.linear_team else ""
        self.query_one("#status-bar", Static).update(
            f"[b]{p.name}[/b]  {p.start} → {p.end}{team}  ·  {load}  [dim]{self.path.name}[/dim]"
        )

    def refresh_detail(self) -> None:
        idea = self.current_idea()
        md = idea_markdown(idea, self.plan) if idea else plan_markdown(self.plan)
        self.query_one("#detail-md", Markdown).update(md)

    def _disk_mtime(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return None

    def persist(self, record: bool = True) -> None:
        """Save after every mutation and redraw. Called by all actions.

        `record` pushes the pre-mutation state onto the undo stack; undo/redo
        themselves pass False.
        """
        if record:
            self.undo_stack.append(self.saved_state)
            del self.undo_stack[:-200]
            self.redo_stack.clear()
        if self._disk_mtime() != self.last_mtime:
            # Someone (typically `mgr link`) wrote the file since we last
            # looked. Absorb their Linear ids so our save doesn't erase them.
            disk = model.load(self.path)
            for disk_idea in disk.ideas:
                ours = self.plan.get(disk_idea.id)
                if ours and disk_idea.linear_id and not ours.linear_id:
                    ours.linear_id = disk_idea.linear_id
        model.save(self.plan, self.path)
        self.last_mtime = self._disk_mtime()
        self.saved_state = self.plan.to_dict()
        self.refresh_board()

    def check_external_changes(self) -> None:
        mtime = self._disk_mtime()
        if mtime is None or mtime == self.last_mtime:
            return
        self.last_mtime = mtime
        try:
            self.plan = model.load(self.path)
        except ValueError as exc:  # half-edited JSON by hand; try again next tick
            self.notify(f"Could not reload {self.path}: {exc}", severity="warning")
            return
        self.saved_state = self.plan.to_dict()
        self.refresh_board()
        self.notify("Reloaded plan (changed on disk)")

    def select(self, idea: Idea) -> None:
        self.cursor_ids[idea.status] = idea.id
        self.refresh_board()
        self.list_for(idea.status).focus()

    # ----- events -------------------------------------------------------

    @on(OptionList.OptionHighlighted)
    def _highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if isinstance(event.option_list, IdeaList):
            self.cursor_ids[event.option_list.status] = int(event.option.id)
            if event.option_list is self.focused:
                self.refresh_detail()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.action_edit()

    def on_descendant_focus(self) -> None:
        self.refresh_detail()

    @on(Input.Submitted, "#quick-add")
    def _quick_add_submit(self, event: Input.Submitted) -> None:
        title = event.value.strip()
        if not title:
            return
        idea = self.plan.add(title, self.add_target)
        self.cursor_ids[idea.status] = idea.id
        event.input.value = ""
        # Keep the input open for rapid-fire brainstorming.
        self.persist()

    # ----- actions ------------------------------------------------------

    def action_quick_add(self) -> None:
        lst = self.focused_list()
        self.add_target = lst.status if lst else Status.INBOX
        box = self.query_one("#quick-add", Input)
        box.placeholder = f"New idea → {COLUMN_TITLES[self.add_target]} — enter to add, esc to stop"
        box.display = True
        box.focus()

    def action_close_quick_add(self) -> None:
        box = self.query_one("#quick-add", Input)
        if box.display:
            box.value = ""
            box.display = False
            self.list_for(self.add_target).focus()

    def action_focus_column(self, delta: int) -> None:
        lst = self.focused_list()
        visible = self.visible_statuses()
        idx = visible.index(lst.status) if lst and lst.status in visible else 0
        self.list_for(visible[(idx + delta) % len(visible)]).focus()

    def action_jump_column(self, index: int) -> None:
        self.list_for(self.visible_statuses()[index]).focus()

    def action_cursor(self, motion: str) -> None:
        # `motion` names an OptionList action: cursor_up/down, first/last, page_up/down.
        if lst := self.focused_list():
            getattr(lst, f"action_{motion}")()

    def _restore(self, src: list[dict], dst: list[dict], verb: str) -> None:
        if not src:
            self.notify(f"Nothing to {verb}")
            return
        restored = Plan.from_dict(src.pop())
        dst.append(self.saved_state)
        # Linear ids record facts about the outside world (issues that now
        # exist), so undo must not forget them or re-export would duplicate.
        for idea in restored.ideas:
            current = self.plan.get(idea.id)
            if current and current.linear_id and not idea.linear_id:
                idea.linear_id = current.linear_id
        self.plan = restored
        self.persist(record=False)

    def action_undo(self) -> None:
        self._restore(self.undo_stack, self.redo_stack, "undo")

    def action_redo(self) -> None:
        self._restore(self.redo_stack, self.undo_stack, "redo")

    def action_shift(self, delta: int) -> None:
        idea = self.current_idea()
        if idea is None:
            return
        visible = self.visible_statuses()
        idx = visible.index(idea.status) if idea.status in visible else len(visible) - 1
        target = idx + delta
        if not 0 <= target < len(visible):
            return
        idea.status = visible[target]
        self.persist()
        self.select(idea)

    def action_reorder(self, delta: int) -> None:
        if idea := self.current_idea():
            self.plan.move_within_status(idea.id, delta)
            self.persist()
            self.select(idea)

    def action_cycle_priority(self) -> None:
        # Cycle in "importance" order (none → low → … → urgent → none), which
        # is the reverse of Linear's numbering except for 0.
        order = [0, 4, 3, 2, 1]
        if idea := self.current_idea():
            idea.priority = order[(order.index(idea.priority) + 1) % len(order)] if idea.priority in order else 0
            self.persist()

    def action_drop(self) -> None:
        idea = self.current_idea()
        if idea is None:
            return
        # Dropping a dropped idea restores it to the inbox, so `d` is a toggle.
        idea.status = Status.INBOX if idea.status == Status.DROPPED else Status.DROPPED
        self.persist()
        self.notify(f"{'Dropped' if idea.status == Status.DROPPED else 'Restored'}: {idea.title}")

    def action_delete(self) -> None:
        idea = self.current_idea()
        if idea is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.plan.remove(idea.id)
                self.persist()

        self.app.push_screen(ConfirmScreen(f"Permanently delete mgr-{idea.id} “{idea.title}”?"), done)

    def action_toggle_dropped(self) -> None:
        self.show_dropped = not self.show_dropped
        self.query_one(f"#col-{Status.DROPPED}").display = self.show_dropped
        lst = self.focused_list()
        if not self.show_dropped and lst and lst.status == Status.DROPPED:
            self.list_for(Status.PARKED).focus()

    def action_edit(self) -> None:
        idea = self.current_idea()
        if idea is None:
            return

        def done(changed: bool | None) -> None:
            if changed:
                if idea.status == Status.DROPPED and not self.show_dropped:
                    self.action_toggle_dropped()
                self.persist()
                self.select(idea)

        self.app.push_screen(IdeaEditor(idea, self.plan), done)

    def action_edit_plan(self) -> None:
        def done(changed: bool | None) -> None:
            if changed:
                self.persist()

        self.app.push_screen(PlanEditor(self.plan), done)

    def action_export(self) -> None:
        out = export_path(self.path)
        text = export.render(self.plan, self.path, store.load_instructions(self.path.parent))
        out.write_text(text)
        # Also put it on the clipboard (OSC 52) so it can be pasted straight
        # into a Claude session; terminals without OSC 52 just ignore this.
        self.app.copy_to_clipboard(text)
        n = sum(1 for i in self.plan.in_status(Status.COMMITTED) if not i.linear_id)
        self.notify(f"Wrote {out} ({n} tickets) and copied to clipboard")

    def action_back(self) -> None:
        self.app.pop_screen()


def export_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".handoff.md")


class InstructionsEditor(FormScreen):
    """Edits the plans folder's agent-instructions.md (team conventions)."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(f"Agent instructions — {tilde(self.path)}", classes="title", markup=False)
            yield Label(
                "Team conventions added to every handoff brief from this folder. "
                "Text inside <!-- --> comments is ignored.",
                classes="hint",
                markup=False,
            )
            yield TextArea(self.path.read_text(), language="markdown", soft_wrap=True, id="instructions")
            yield Label("[b]ctrl+s[/b] save   [b]esc[/b] cancel", classes="hint")

    def action_save(self) -> None:
        self.path.write_text(self.query_one("#instructions", TextArea).text)
        self.dismiss(True)


class PlanListScreen(Screen):
    """The plans folder: pick, create, carry over, archive or delete plans."""

    BINDINGS = [
        Binding("l,o", "open", "Open"),
        Binding("n", "new", "New"),
        Binding("c", "carry", "Carry over"),
        Binding("a", "archive", "Archive"),
        Binding("D", "delete", "Delete"),
        Binding("A", "app.edit_instructions(False)", "Agent rules"),
        Binding("j", "move('cursor_down')", show=False),
        Binding("k", "move('cursor_up')", show=False),
        Binding("g", "move('first')", show=False),
        Binding("G", "move('last')", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder
        self.files: list[store.PlanFile] = []

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        yield OptionList(id="plans")
        with VerticalScroll(id="detail"):
            yield Markdown(id="plan-detail")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_list(select=store.current_plan(self.folder))
        self.query_one("#plans").focus()

    def on_screen_resume(self) -> None:
        # Coming back from a board: counts and names may have changed.
        self.refresh_list(select=self.selected().path if self.selected() else None)

    def selected(self) -> store.PlanFile | None:
        idx = self.query_one("#plans", OptionList).highlighted
        return self.files[idx] if idx is not None and idx < len(self.files) else None

    def refresh_list(self, select: Path | None = None) -> None:
        self.files = store.list_plans(self.folder)
        current = store.current_plan(self.folder)
        options = []
        for f in self.files:
            # Style the marker only: a style passed to Text() would tint the whole row.
            text = Text.assemble(("* " if f.path == current else "  ", "bold green"))
            if f.plan is None:
                text.append(f"{f.path.name}  unreadable", "red")
            else:
                text.append(f"{f.plan.start} → {f.plan.end}  ", "cyan")
                text.append(f.plan.name, "bold")
                text.append(f"  {store.summary(f.plan)}", "dim")
                if f.path.stem != f.plan.start:
                    text.append(f"  [{f.path.name}]", "dim")
            options.append(Option(text))
        lst = self.query_one("#plans", OptionList)
        lst.set_options(options)
        paths = [f.path for f in self.files]
        if select in paths:
            lst.highlighted = paths.index(select)
        elif paths:
            lst.highlighted = 0
        n_rules = "set" if store.load_instructions(self.folder) else "none yet"
        self.query_one("#status-bar", Static).update(
            f"[b]Plans[/b]  {len(self.files)} plan(s)  ·  agent rules: {n_rules}  ·  [dim]{tilde(self.folder)}[/dim]"
        )
        self.refresh_detail()

    def refresh_detail(self) -> None:
        f = self.selected()
        if f is None:
            md = "_No plans yet — press `n` to create one._"
        elif f.plan is None:
            md = f"### {f.path.name}\n\nCould not read this file:\n\n```\n{f.error}\n```"
        else:
            md = plan_markdown(f.plan) + f"\n\n{store.summary(f.plan)}"
        self.query_one("#plan-detail", Markdown).update(md)

    @on(OptionList.OptionHighlighted)
    def _highlighted(self) -> None:
        self.refresh_detail()

    @on(OptionList.OptionSelected)
    def _selected(self) -> None:
        self.action_open()

    def action_move(self, motion: str) -> None:
        getattr(self.query_one("#plans", OptionList), f"action_{motion}")()

    def action_open(self) -> None:
        f = self.selected()
        if f is None:
            return
        if f.plan is None:
            self.notify(f"{f.path.name} is unreadable; fix or delete it", severity="error")
            return
        self.app.push_screen(BoardScreen(f.plan, f.path))

    def action_new(self) -> None:
        path = store.create_plan(self.folder)
        self.refresh_list(select=path)
        self.notify(f"Created {path.name}")

    def action_carry(self) -> None:
        f = self.selected()
        if f is None or f.plan is None:
            return
        _, nxt, path = store.carry(f.path)
        self.refresh_list(select=path)
        self.notify(f"Carried {len(nxt.ideas)} unfinished idea(s) into {path.name}")

    def action_archive(self) -> None:
        f = self.selected()
        if f is None:
            return
        try:
            target = store.archive(f.path)
        except FileExistsError as exc:
            self.notify(f"Already in archive: {exc}", severity="error")
            return
        self.refresh_list()
        self.notify(f"Archived to {target.parent}")

    def action_delete(self) -> None:
        f = self.selected()
        if f is None:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                store.delete(f.path)
                self.refresh_list()

        self.app.push_screen(ConfirmScreen(f"Permanently delete {f.path.name} (and its handoff)?"), done)


class MgrApp(App):
    TITLE = "mgr"
    CSS = """
    #status-bar { height: 1; padding: 0 1; background: $boost; }
    #board { height: 1fr; }
    .column { width: 1fr; border: round $panel-lighten-2; }
    .column:focus-within { border: round $accent; }
    .col-header { text-style: bold; padding: 0 1; }
    IdeaList { height: 1fr; border: none; background: transparent; }
    IdeaList:focus { border: none; }
    /* Only the focused column gets a strong highlight; others keep a faint marker. */
    IdeaList:blur > .option-list--option-highlighted { background: $boost; text-style: none; }
    #detail { height: 12; border-top: solid $panel-lighten-2; padding: 0 1; }
    #quick-add { margin: 0 1; }

    ModalScreen { align: center middle; }
    .dialog { width: 90; max-width: 95%; height: auto; max-height: 90%; border: thick $accent; background: $surface; padding: 1 2; }
    .dialog.small { width: 60; }
    .dialog.wide { width: 110; height: 90%; }
    #instructions { height: 1fr; }
    #plans { height: 1fr; border: none; padding: 0 1; }
    .dialog .title { text-style: bold; margin-bottom: 1; }
    .dialog .hint { color: $text-muted; margin-top: 1; }
    .fields { grid-size: 2; grid-columns: 16 1fr; grid-rows: 1; grid-gutter: 1 0; height: auto; margin: 1 0; }
    #notes { height: 12; }
    #goals { height: 6; }
    """

    def __init__(self, folder: Path, open_path: Path | None = None) -> None:
        super().__init__()
        self.folder = folder
        self.open_path = open_path

    def on_mount(self) -> None:
        # The picker is always the bottom screen so `-` from any board has
        # somewhere to go back to, even when launched straight into a plan.
        self.push_screen(PlanListScreen(self.folder))
        if self.open_path is not None:
            self.push_screen(BoardScreen(model.load(self.open_path), self.open_path))

    def action_edit_instructions(self, from_board: bool) -> None:
        # From a board, the rules that apply are those next to that plan.
        folder = self.screen.path.parent if from_board else self.folder
        self.push_screen(InstructionsEditor(store.ensure_instructions(folder)), self._instructions_saved)

    def _instructions_saved(self, saved: bool | None) -> None:
        if saved:
            self.notify("Saved agent instructions")
            if isinstance(self.screen, PlanListScreen):
                self.screen.refresh_list(select=self.screen.selected().path if self.screen.selected() else None)
