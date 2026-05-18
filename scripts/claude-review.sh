#!/usr/bin/env bash
#
# Headless Claude Code review of the current diff.
#
# Usage:
#   ./scripts/claude-review.sh              # review HEAD vs origin/main
#   ./scripts/claude-review.sh main         # review HEAD vs local main
#   BASE_REF=other ./scripts/claude-review.sh
#
# Designed to be CI-friendly: emits JSON on stdout, always exits 0
# (the review itself is advisory; downstream gates decide whether to block).
#
# Flag reference (verify in the install's Claude Code with /help):
#   -p / --print            : query-then-exit, non-interactive
#   --tools                 : restrict which tools Claude can use
#                            (NOT --allowedTools — that's the auto-approve list)
#   --permission-mode       : default | acceptEdits | plan | bypassPermissions
#   --output-format         : text | json | stream-json

set -euo pipefail

BASE_REF="${1:-${BASE_REF:-origin/main}}"

# Collect changed files vs the base ref
if git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    CHANGED=$(git diff --name-only "$BASE_REF"...HEAD)
else
    # Fallback: just look at uncommitted changes
    CHANGED=$(git diff --name-only HEAD; git diff --name-only --cached)
fi

if [ -z "$CHANGED" ]; then
    echo '{"review": "no_diff", "base_ref": "'"$BASE_REF"'", "files": []}'
    exit 0
fi

PROMPT="You are reviewing changes to the LocalAgents project.

Changed files (vs $BASE_REF):
$CHANGED

For each file, check:
- CLAUDE.md conventions (PEP 604, pathlib, dataclasses, future-annotations)
- Test coverage in tests/unit/
- Backward-compatibility risk for callers
- Reference Agents.md design intent if relevant

Return a single JSON object with this exact shape:
{
  \"summary\": \"<one paragraph, plain English>\",
  \"risk_level\": \"low\" | \"medium\" | \"high\",
  \"must_fix\": [\"<item>\", ...],
  \"should_fix\": [\"<item>\", ...]
}

Do not include any prose outside the JSON."

claude -p "$PROMPT" \
    --tools "Read,Grep,Bash" \
    --permission-mode default \
    --output-format json
