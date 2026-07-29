---
date: 2026-07-02
topic: batch-tools-console
---

# Batch Tools Console

## Problem Frame

The `percell4-batch-*` console commands (declared in `pyproject.toml` under
`[project.scripts]`) are the only way to run a **single** batch task in
isolation — e.g. running just segmentation + tracking via
`percell4-batch-cellpose-laptrack`, rather than a full multi-step Workflow.
Today, running one means leaving the GUI: either quit the app, or open a new
terminal, `cd ~/percell4`, and activate the venv. That context switch is pure
friction for a task the user does routinely.

The launcher already surfaces multi-step batch *Workflows* as dialogs, but the
raw single-task catalog has no in-app home. This feature adds a **command
console** inside the launcher: the user types a batch-tool invocation exactly as
they would in a terminal (command name, flags, file paths), runs it in the venv,
and watches its output stream — without ever opening a terminal.

The user deliberately wants to *type commands*, not fill out forms, and wants a
terminal-like feel (ANSI colors, tab-completion, history) — but scoped only to
running the batch-tool catalog, not a full interactive shell.

---

## Key Flows

- F1. Run a single batch tool
  - **Trigger:** User wants one batch task (e.g. segmentation + tracking only), not a full Workflow.
  - **Steps:** Open the console → optionally pick a tool from the catalog to seed its name → type flags and data paths (tab-completion or drag files in to insert paths) → press Enter → output streams live → run finishes with a visible exit status.
  - **Outcome:** The batch task ran against on-disk datasets without leaving the app.
  - **Covered by:** R1, R2, R3, R7, R8, R9, R10, R12, R13, R14, R15

- F2. Look up a tool's options
  - **Trigger:** User doesn't remember a tool's flags.
  - **Steps:** Pick the tool from the catalog → click "Show --help" → read the help text in the output area → type the real command.
  - **Covered by:** R14, R16

- F3. Cancel or re-run
  - **Trigger:** A command was wrong, a run needs aborting, or the user wants to repeat a prior run.
  - **Steps:** Cancel terminates the running process; Up-arrow recalls the previous command; edit it; Enter re-runs.
  - **Covered by:** R5, R6, R11

---

## Requirements

**Command entry & execution**
- R1. A command input where the user types a full batch-tool invocation exactly as in a terminal — command name, flags, and file paths — and runs it with Enter (or a Run button).
- R2. Commands execute inside the project's virtual environment without the user manually activating it, so the `percell4-batch-*` commands resolve and behave as they do in a venv-activated terminal.
- R3. Commands run from a defined working directory (default: repository root) so relative paths behave predictably; absolute paths are also supported.
- R4. The GUI stays fully responsive while a command runs — a long Cellpose batch must not freeze the app.
- R5. One command runs at a time; Run is unavailable while a command is in progress (queueing is out of scope).
- R6. A running command can be cancelled from the console, terminating the process (equivalent to Ctrl-C).

**Output & feedback**
- R7. stdout and stderr stream into a scrollable output area live, as produced — not only on completion.
- R8. ANSI color escape codes render as color, not as raw escape text.
- R9. Carriage-return progress updates (tqdm / Cellpose-style `\r` progress bars) overwrite the current line in place rather than appending thousands of lines.
- R10. On completion, the console clearly shows the command's exit status (success vs. non-zero).
- R11. Command history: previous commands can be recalled and edited with Up/Down arrows.

**Terminal niceties**
- R12. Tab-completion for tool names (the `percell4-batch-*` catalog) and for filesystem paths while typing.
- R13. Files or folders dragged onto the input insert their path at the cursor.

**Catalog & discovery**
- R14. A catalog control lists the available `percell4-batch-*` tools, auto-derived from the project's declared console scripts, so newly added tools appear without manual registration.
- R15. Selecting a tool from the catalog inserts its command name into the input, ready for the user to add flags and paths.
- R16. A "Show --help" action runs the selected tool's `--help` and displays it in the output area, so flags can be looked up without leaving the app.

**Placement & lifecycle**
- R17. The console is reachable from the launcher as a persistent panel, in or adjacent to the existing Workflows sidebar section.
- R18. Output accumulates across runs within a session as a scrollable log; a clear-output action is available.

---

## Acceptance Examples

- AE1. **Covers R2, R3.** Given the app launched normally (no terminal has the venv activated), when the user types `percell4-batch-cellpose-laptrack ./exp1 --seg-channel 0 --gpu` and presses Enter, then the tool runs exactly as it would in a venv-activated terminal at the repository root.
- AE2. **Covers R9.** Given a Cellpose batch that prints a `\r` progress bar, when it runs in the console, then the progress line updates in place and the log is not flooded with duplicate lines.
- AE3. **Covers R4, R6.** Given a long-running batch command, when it is running, then the user can still interact with the rest of the app and can click Cancel to terminate it.

---

## Success Criteria

- The user can run any `percell4-batch-*` tool for a single batch task from inside the app — no quitting, no new terminal, no `cd`, no venv activation — and can read its output (including colors and progress bars) as it streams.
- A planner can implement without inventing UX: the input model (typed command), execution semantics (venv, cwd, single run, cancel), output behavior (live stream, ANSI, `\r`), niceties (history, completion, drag-in paths), and catalog discovery are all specified here.

---

## Scope Boundaries

- Not a full interactive terminal: no interactive prompts (`y/N`), no PTY, no curses / full-screen TUIs.
- Not a general shell: no pipes, redirects, globbing, env-var expansion, `cd`, or arbitrary shell builtins — the console is oriented to running the `percell4-*` catalog. (Whether the backend uses a shell is a planning decision; these features are not promised regardless.)
- `scripts/*.py` (per_particle_analysis, gen_puncta_masks, compare_masks, etc.) are out of scope for v1; the catalog is the `[project.scripts]` console entry points only.
- No auto-generated argument forms and no per-tool GUI wrappers — explicitly rejected in favor of typed commands.
- No job queue or parallel runs in v1.
- No automatic reconciliation of a tool writing an `.h5` that the GUI has open (see Dependencies) — a warning and/or manual reload is the ceiling for v1.

---

## Key Decisions

- **Typed command line over generated forms** — the user wants to type the exact terminal invocation with flags and paths; forms (auto-generated or curated) were considered and rejected.
- **"Console + niceties" fidelity** (ANSI rendering + tab-completion + history) chosen over both a bare run-and-watch box and a true embedded PTY terminal — it delivers the terminal feel for batch runs without the maintenance cost of an embedded terminal-emulator dependency.
- **Catalog auto-derived from declared console scripts**, not a hand-maintained registry — avoids drift, consistent with the project's documentation rules (docs describe what IS; no duplicated source of truth).
- **Scope limited to the uniform `percell4-batch-*` argparse CLIs first** — they share a clean, predictable command shape; the messier `scripts/*.py` family is deferred.

---

## Dependencies / Assumptions

- Assumes the app is run from the package installed into the project venv (per root `CLAUDE.md`), so the `percell4-batch-*` entry points are on PATH in that environment.
- Assumes batch tools operate on `.h5` files / project folders on disk. If the GUI has a dataset open that a tool writes, the in-app view could go stale; assumption for v1 is that a warning and/or a manual reload after a run is acceptable, and automatic reconciliation is out of scope.
- The catalog's source of truth is `[project.scripts]` in `pyproject.toml`.

---

## Outstanding Questions

### Deferred to Planning

- [Affects R2] [Technical] Backend execution model: run through a login shell with the venv activated, vs. resolve the venv interpreter / entry points directly and exec the parsed argv (e.g. `shlex` + `QProcess`). Recommendation: direct argv exec in the venv environment (no shell), since shell features are out of scope.
- [Affects R8, R9] [Technical] ANSI-color and `\r` rendering approach — which converter and which widget (e.g. a small ANSI-to-rich-text pass into a `QPlainTextEdit`/`QTextEdit`).
- [Affects R12] [Technical] Tab-completion mechanism (e.g. `QCompleter` + a filesystem model for paths; a static list for the catalog) and how thorough path completion needs to be.
- [Affects R14] [Technical] How to enumerate the catalog at runtime — parse `pyproject.toml`, query installed entry points via `importlib.metadata`, or generate a small manifest.
- [Affects R17] [User decision — low stakes] Placement: a "Batch tools" group inside the existing Workflows panel vs. a dedicated sidebar entry. Recommendation: a dedicated panel, since the streaming output console wants vertical space.

---

## Next Steps

-> `/ce-plan` for structured implementation planning
