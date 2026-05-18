# LocalAgents — Claude Code Working Notes

> Project-level context for Claude Code. Reads on every session start.
> Keep this file short and concrete. Architectural depth lives in `Agents.md` (36KB) and `README.md`.

## What this project is

Local Agent Dev Studio — AI-native software delivery runtime.
Codex / Claude Code are used as **patch workers**; Studio is the **delivery loop around them**.

Core invariant:

> **"The model is not the system. The delivery loop is the system."**

Every change must keep this invariant: deterministic gates (Promotion Gate, Apply Gate, permission checks) own the decisions; the model owns the proposing.

## Stack

- **Python 3.12** (`.venv/` at project root; `pyproject.toml` defines `agent-studio = "orchestrator.cli:main"`)
- **Next.js 15** (`apps/studio-console`, runs on port 3015)
- Tests: `pytest` (unit + integration + e2e)
- No mypy yet; rely on tests + reading

## How to verify your changes

```bash
# Always run first
pytest tests/unit -x

# When touching a specific module
pytest tests/unit/test_<module_name>.py -x

# Heavier; only for cross-module changes
pytest tests/integration -x
pytest tests/e2e -x          # slow, real subprocesses

# Quick smoke that the package still imports
python -c "import orchestrator.cli; import orchestrator.core.permission_engine"
```

## Code conventions (enforced by reading, not tooling)

- `from __future__ import annotations` at the top of every Python file
- Type hints in **PEP 604** form: `str | None`, not `Optional[str]`
- `pathlib.Path`, not `os.path`
- `dataclasses` over dicts for any non-trivial structure (see `core/review_queue.py` as the reference style)
- Timestamps via `orchestrator.core.ids.now_iso()`, never `datetime.now()` inline
- One module = one responsibility; if a file grows past ~500 lines, split it (`cli.py` is the known exception — don't add to it casually)

## What NOT to touch without explicit confirmation

These are protected by `.claude/hooks/deny-sensitive.py`:
- `.env`, `.env.local`, `.env.*.local` — contain `V0_API_KEY`, `TAVILY_API_KEY`
- `.agent-studio/` — autonomous session runtime data (generated projects, git worktrees, evidence)
- `.studio-console/runtime/` — UI server runtime logs and pids

Also do not silently:
- Edit `pyproject.toml`'s `[project.scripts]` entry point
- Force-push any branch matching `agentic/autonomous/*` or `agentic/change/*`
- Bump dependencies in `package.json` / pyproject without flagging it explicitly
- Add a new top-level directory

## Where things live (only the non-obvious bits)

- **Core engine**: `orchestrator/core/` — runtime, gates, queues, contracts
- **Tool adapters**: `orchestrator/tools/` — shell / git / file / v0 / figma / browser / firecrawl / search / test
- **Agent role definitions**: `agents/*.yaml` — declares model, tools, permissions per role
- **Workflow definitions**: `workflows/*.yaml` — declares the stage pipeline
- **Permission model**: `orchestrator/core/permission_engine.py` (currently a 14-line stub — known refactor target; see `Agents.md:504` for design intent borrowed from Claude Code's allow/ask/deny)

## How to refactor safely in this repo

1. Find the test file: `tests/unit/test_<module>.py`
2. Read existing tests — they encode the invariants
3. Search for usages: `grep -rn "<symbol>" --include="*.py"` (file_tools.py is a common downstream of core changes)
4. Keep public signatures backward-compatible unless you also update every caller
5. Permission / contract / promotion-gate changes always on a new branch (`refactor/<scope>` or `feature/<scope>`)

## When in doubt

- `Agents.md` is the original design doc. Treat conflicts between code and Agents.md as bugs in one or the other — surface them, don't silently pick.
- `docs/EVALUATION.md` and `docs/rc4c-demo-suite-report.md` document what's currently green; don't break a green demo without a plan to fix it in the same change.
