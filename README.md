# mgr

A single-player TUI for spitballing what your team should do over the next
planning window (typically two weeks), then handing the result to an LLM that
creates the Linear tickets via the Linear MCP.

## Install

macOS or Linux, one command (installs [uv](https://docs.astral.sh/uv/) if
missing, then `mgr`, then the Claude Code skill):

```
curl -fsSL https://raw.githubusercontent.com/kreldjarn/mgr/main/install.sh | sh
```

If the repo is private, `curl` can't fetch the script anonymously. Use the
GitHub CLI and SSH instead (works for anyone with repo access):

```
gh api repos/kreldjarn/mgr/contents/install.sh -H "Accept: application/vnd.github.raw" | MGR_REPO=ssh://git@github.com/kreldjarn/mgr sh
```

Re-run the same command to upgrade. From a clone, run `./install.sh` instead
(`MGR_EDITABLE=1 ./install.sh` for an editable install while hacking on mgr).
If you keep plans somewhere other than `~/.local/share/mgr`, set `MGR_DIR`
before installing, so the skill points at the right folder.

To uninstall: `uv tool uninstall mgr && rm -r ~/.claude/skills/mgr-*`.

## Usage

```
mgr                         # home: plans tab (tab switches to notes)
mgr --plan 2026-10-19       # open a plan directly (name or path)
mgr ls                      # list plans; * marks the current one
mgr show                    # a plan with every idea and its mgr-N id
mgr new [--weeks 45-46]     # new plan (default: the window after the latest)
mgr add TITLE [--notes -]   # add an idea (inbox by default; see --help)
mgr search WORDS [--all]    # ideas and notes containing all WORDS
mgr notes                   # list notes
mgr note TITLE [--body -]   # create a note (- reads stdin)
mgr append NOTE --body -    # append to a note
mgr promote NOTE [--into P] # turn a note into a plan, or add it to one
mgr export [-o FILE]        # LLM handoff brief to stdout / FILE
mgr link 12 ENG-345         # record that mgr-12 became ENG-345
mgr carry [NEW] [--open]    # start the next window from unfinished ideas
mgr instructions            # path of the agent instructions file
mgr skill [--install]       # the Claude Code skills
```

Plans live in `~/.local/share/mgr` (override with `--dir` or `$MGR_DIR`, e.g. a
folder in a git repo). Commands that need a plan default to the **current**
one: the plan whose window contains today, else the next upcoming one, else the
latest.

## The plans folder

```
2026-10-19.json            one plan per window, named by start date
2026-10-19.handoff.md      last export of that plan
agent-instructions.md      team conventions for the ticket-creating agent
notes/                     free-form Markdown notes (notes/archive/ too)
archive/                   archived plans (with their handoffs)
```

Windows are shown with ISO (Swedish) week numbers, e.g.
`2026-10-19 → 2026-11-01 · v.43–44`.

`mgr` opens on the plan picker. `enter`/`l` opens a plan, `n` creates the next
window's plan (copying team, project and capacity from the latest), `c` carries
the selected plan over, `a` archives it, `D` deletes it (asks first), and `A`
edits the agent instructions. On a board, `-` returns to the picker.
Unreadable files are listed rather than hidden, so a bad hand edit is obvious.

Everything is plain files, and every screen keeps up with changes on disk. The
lists redraw when files appear or change, and an open board merges outside
edits (e.g. Claude adding ideas, `mgr link`) into yours instead of
overwriting them. If both sides changed the same field, your edit wins.

## Notes

`tab` on the home screen switches to **Notes**: free-form Markdown in
`notes/`, for meeting takeaways, decisions and half-formed ideas. `n` creates
one, `e` edits it in the built-in editor, `E` opens it in `$VISUAL`/`$EDITOR`,
and `a`/`D` archive or delete it.

Notes are grouped by ISO week (`v.42 · 12–18 Oct 2026 · this week`), dated by
the `YYYY-MM-DD` prefix of their file name. `}`/`{` (or `]`/`[`) jump to the
next or previous week, and `w` toggles to a flat "recently edited" list.

`p` **promotes** a note into a new plan or an existing one:

```markdown
# Retrieval offsite           → plan name (new plans)
## Goals
- Ship retrieval eval v1      → plan goal
## Ideas
- Build golden query set      → Inbox idea
  - 200 queries, two labellers → …with this as its notes
- [ ] Profile reranker        → Inbox idea
- [x] Done already            → skipped
```

The whole note is also added to the plan's **Background**, which the handoff
brief passes to the agent as context. A promoted note gets a visible
`> Promoted to 2026-11-02.json` line.

## Agent instructions

Two layers tell the ticket-creating agent what to do:

- **The handoff brief** (`x` / `mgr export`) carries the built-in procedure:
  1. confirm the ticket list with you first;
  2. create Committed ideas only, parents first;
  3. map the fields;
  4. record each Linear id with `mgr link`.
- **`agent-instructions.md`** (`A` in the TUI) holds your team's conventions,
  e.g. "add every ticket to the active cycle" or "research tickets get the
  Research label". They are added to every brief from that folder, and they
  override the built-in field mapping where the two conflict. Text inside
  `<!-- -->` is ignored, so the starter examples have no effect until you move
  them out of the comment.

## Search

`/` from the plans list, the notes list or a board opens search across every
plan and note. Results update as you type; all words must match, in any
order. `↑↓` or `ctrl+j`/`ctrl+k` move through the results without leaving the
input. `enter` jumps to the result: for an idea, its plan's board with the
cursor on it; for a note, the note in the notes list. `ctrl+t` includes
archived plans and notes. `mgr search` does the same from the shell.

## Using mgr from Claude Code

`install.sh` (or `mgr skill --install`) installs two skills to
`~/.claude/skills/`, so any Claude Code session can work with your plans:

- **mgr-planning**: "note down what we decided about X", "add these three
  things to the plan", "make a plan for weeks 45–46 from this", "what's in my
  plan about reranking?". It uses the CLI (`note`, `append`, `add`, `new`,
  `show`, `search`, `promote`) and never edits files directly. Ideas go to
  Inbox unless you say otherwise, and it's told to write notes that make sense
  later, without the conversation.
- **mgr-linear-handoff**: "create the Linear tickets from my plan". It finds
  the current plan, fetches the brief, and follows it.

The skills only describe how to call the CLI, so editing conventions never
requires reinstalling them. Reinstall only if the plans folder or the `mgr`
install location changes, because the skills embed both paths.

## Workflow

1. **Spitball.** Press `n` and type ideas one after another (enter adds, esc
   stops). They land in the focused column — usually *Inbox*.
2. **Shape.** Move ideas through *Inbox → Maybe → Committed* (or *Parked*,
   or `d` to drop). Rank within a column with `K`/`J`. Press `e` to add the
   things a ticket needs: notes (become the description), estimate, priority,
   assignee, labels, project, parent.
3. **Check capacity.** Press `s` on the board to set the window, goals, capacity and Linear
   team. The status bar turns red when committed points exceed capacity.
4. **Hand off.** Press `x` (or run `mgr export`). This writes
   `<plan>.handoff.md` and copies it to the clipboard. Paste it into Claude
   Code with the Linear MCP connected, or install the skill (see below) and
   just ask for the tickets. The brief
   tells the agent to create only *Committed* ideas, parents first, and to
   run `mgr link` after each issue. Re-exporting therefore skips anything
   already created, and the open board picks up the new Linear ids live.

5. **Roll over.** When the window ends, press `c` in the picker (or run
   `mgr carry`) to start the next plan as `<next start date>.json`. Anything never ticketed comes along, with the same ids:
   *Inbox*, *Maybe* and *Parked* stay put, and *Committed*-but-unticketed
   ideas drop back to *Maybe* so they're re-decided against the new capacity.
   Ticketed ideas stay behind (Linear tracks them now), as do dropped ones.
   Settings and goals are copied, and the old plan is left untouched. Each
   carried idea shows `⟳N` with its rollover count. The command lists anything
   carried 3+ times, since those are probably worth a decision.

## Keys

Vim-style throughout; arrow keys work too.

| key | action |
| --- | --- |
| `n` / `o` | quick-add to the focused column |
| `e` / `i` / enter | edit idea |
| `h l` / `←→` | change column |
| `0` / `$` | first / last column |
| `j k` / `↑↓` | move cursor |
| `g` / `G` | top / bottom of column |
| `ctrl+d` / `ctrl+u` | page down / up |
| `H L` / `shift+←→` | move idea to the adjacent column |
| `K J` / `shift+↑↓` | rank idea up/down |
| `p` | cycle priority (none → low → medium → high → urgent) |
| `d` | drop / restore |
| `D` | delete permanently (asks first) |
| `u` / `ctrl+r` | undo / redo |
| `z` | show/hide the *Dropped* column |
| `s` | plan settings (dates, capacity, goals, Linear team) |
| `x` | export handoff brief |
| `/` | search plans and notes |
| `A` | edit agent instructions |
| `-` | back to the plan picker |
| `q` | quit |

In editors: `ctrl+s` saves. `esc` cancels, but asks before discarding unsaved
changes, so a reflexive vim `esc` won't lose your typing. Editor text fields
are not modal (no vim mode inside them).

## Notes

- Inbox ideas never appear in the handoff; *Maybe* and *Parked* appear as
  context only, so the agent knows what was considered and deliberately left
  out.
- Undo never removes Linear ids recorded by `mgr link`: those issues
  already exist, and forgetting them would make a re-export create duplicates.
- The plan file is plain JSON and is saved after every change. Editing it by
  hand while the board is open is fine: the board reloads on change.
