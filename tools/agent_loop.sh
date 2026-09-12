#!/usr/bin/env bash
set -euo pipefail

MAX_ROUNDS=5
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

cd "$ROOT_DIR"

echo "=================================================="
echo "FrameForge autonomous agent loop"
echo "Repository: $ROOT_DIR"
echo "Max rounds: $MAX_ROUNDS"
echo "=================================================="

if [[ ! -f ".ai/GOAL.md" ]]; then
  echo "ERROR: .ai/GOAL.md not found"
  exit 1
fi

if [[ ! -f ".ai/POLICY.md" ]]; then
  echo "ERROR: .ai/POLICY.md not found"
  exit 1
fi

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "ERROR: Python virtual environment not found at .venv"
  exit 1
fi

touch .ai/REVIEW.md
touch .ai/IMPLEMENTATION.md

# Single writer for .ai/STATUS.json so implementer/reviewer prompts never
# race the orchestration script over its content. Usage:
#   update_status key1 value1 [key2 value2 ...]
# "true"/"false"/"null" string values are coerced to JSON types.
update_status() {
  "$VENV_PYTHON" - "$@" <<'PY'
import json
import sys

path = ".ai/STATUS.json"
try:
    with open(path) as f:
        data = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    data = {}

args = sys.argv[1:]
for i in range(0, len(args), 2):
    key, value = args[i], args[i + 1]
    if value == "true":
        value = True
    elif value == "false":
        value = False
    elif value == "null":
        value = None
    data[key] = value

with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
}

update_status \
  goal_status "active" \
  phase "implementing" \
  implementer "codex" \
  reviewer "claude" \
  review "pending" \
  tests "unknown" \
  needs_human_approval "false" \
  blocker "null"

for ROUND in $(seq 1 "$MAX_ROUNDS"); do
  echo
  echo "=================================================="
  echo "ROUND $ROUND - CODEX IMPLEMENTATION"
  echo "=================================================="

  update_status phase "implementing"

  codex exec --sandbox workspace-write "
You are the autonomous IMPLEMENTER for FrameForge.

Read:
- .ai/GOAL.md
- .ai/POLICY.md
- .ai/STATUS.json
- .ai/REVIEW.md if it contains prior review feedback
- CLAUDE.md if present
- README.md if relevant

Your job:
1. Inspect the current implementation before changing anything.
2. If .ai/REVIEW.md contains CHANGES_REQUIRED, fix every concrete issue there.
3. Implement only what is necessary to satisfy .ai/GOAL.md.
4. Run relevant tests with .venv/bin/python.
5. Do not stop after explaining a fixable problem.
6. Do not push.
7. Do not modify secrets or credentials.
8. Do not publish anything.
9. Do not rewrite git history.
10. Do not make unrelated architectural changes.

When finished:
- write a concise implementation summary to .ai/IMPLEMENTATION.md
- leave code changes in the working tree

Do not edit .ai/STATUS.json -- the orchestration script owns it.
"

  echo
  echo "=================================================="
  echo "ROUND $ROUND - CLAUDE REVIEW"
  echo "=================================================="

  update_status phase "review" review "pending"

  REVIEW_OUTPUT="$(
    claude -p "
You are the autonomous REVIEWER for FrameForge.

Read:
- .ai/GOAL.md
- .ai/POLICY.md
- .ai/STATUS.json
- .ai/IMPLEMENTATION.md
- git diff
- relevant source files
- relevant tests

Review only the current goal.

Check:
- correctness
- goal compliance
- regressions
- dead code
- stale tests
- misleading test coverage
- hidden edge cases
- production realism
- security
- unrelated changes

Do not modify files.
Do not redesign unrelated systems.

Your FIRST LINE must be exactly one of:

VERDICT: PASS

or

VERDICT: CHANGES_REQUIRED

If changes are required:
- give a numbered list
- each item must describe a concrete defect
- include an exact fix

If acceptable:
- briefly explain why it passes
"
  )" || {
    update_status \
      phase "blocked" \
      review "invalid" \
      blocker "Reviewer process (claude -p) exited with a non-zero status in round $ROUND"
    echo "ERROR: Reviewer process failed to run."
    exit 3
  }

  # Check the captured variable directly, not a file we are about to
  # write ourselves -- `printf '%s\n' ""` still writes a 1-byte newline,
  # so testing the file with `-s` after the fact can never detect a truly
  # empty/whitespace-only review.
  if [[ -z "${REVIEW_OUTPUT//[[:space:]]/}" ]]; then
    printf '%s\n' "$REVIEW_OUTPUT" > .ai/REVIEW.md
    update_status \
      phase "blocked" \
      review "invalid" \
      blocker "Reviewer returned an empty review in round $ROUND"
    echo "ERROR: Claude returned an empty review."
    exit 3
  fi

  printf '%s\n' "$REVIEW_OUTPUT" > .ai/REVIEW.md

  echo
  echo "=================================================="
  echo "REVIEW RESULT"
  echo "=================================================="

  cat .ai/REVIEW.md

  FIRST_LINE="$(head -n1 .ai/REVIEW.md)"

  if [[ "$FIRST_LINE" == "VERDICT: PASS" ]]; then
    echo
    echo "Claude review passed."
    update_status review "pass" blocker "null"
    break
  fi

  if [[ "$FIRST_LINE" != "VERDICT: CHANGES_REQUIRED" ]]; then
    update_status \
      phase "blocked" \
      review "invalid" \
      blocker "Reviewer's first line was not a valid verdict in round $ROUND: ${FIRST_LINE:0:200}"
    echo
    echo "ERROR: Claude review did not contain a valid verdict."
    exit 4
  fi

  update_status review "changes_required"

  if [[ "$ROUND" -eq "$MAX_ROUNDS" ]]; then
    update_status \
      phase "blocked" \
      blocker "Reached max review rounds ($MAX_ROUNDS) without VERDICT: PASS"
    echo
    echo "ERROR: Maximum review rounds reached without PASS."
    exit 2
  fi

  echo
  echo "Claude requested changes. Starting another Codex round..."
done

echo
echo "=================================================="
echo "FINAL TEST SUITE"
echo "=================================================="

if "$VENV_PYTHON" -m pytest -q; then
  update_status tests "passed"
else
  update_status \
    phase "blocked" \
    tests "failed" \
    blocker "Final .venv/bin/python -m pytest -q run failed after review PASS"
  echo
  echo "ERROR: Final test suite failed after review PASS."
  exit 5
fi

echo
echo "=================================================="
echo "FINAL GIT STATUS"
echo "=================================================="

git status --short

update_status \
  goal_status "complete" \
  phase "done" \
  needs_human_approval "false" \
  blocker "null"

echo
echo "=================================================="
echo "AUTONOMOUS LOOP COMPLETE"
echo "=================================================="
echo "Claude review: PASS"
echo "Tests: PASS"
echo "No push was performed."
echo "Human approval can happen now."
