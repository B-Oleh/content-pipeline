# Current Goal

Optimize the overnight batch pipeline to reliably produce 3 candidates under the current Gemini free-tier limit of 20 generate_content requests per minute, without enabling billing.

Use the failed run 34720594297 and batch metadata as evidence.

Requirements:
- Keep the existing >=80% real-media quality gate.
- Do not weaken QA or allow rejected/irrelevant media.
- Reduce Gemini request consumption per candidate.
- Respect Retry-After / retry timing for 429s.
- Avoid starting the next Gemini-heavy stage while the rate-limit window is still saturated.
- Add explicit pacing/rate-limit coordination across Script, content brief, and Vision calls.
- Reuse/cache Vision decisions where safe.
- Add a cheaper pre-filter before Vision so only the strongest visual candidates consume Gemini calls.
- Do not treat a temporary 429 as a permanent batch blocker until a bounded recovery strategy has been exhausted.
- Preserve all successfully completed candidates if a later candidate fails.
- Add tests for the rate-limit coordination and recovery behavior.
- Run the full test suite.
- You are approved to commit and push validated changes to origin/main.
- After push, run a real 3-candidate batch and continue until either 3 candidates are delivered to Telegram or a genuine external blocker remains.
- Do not enable billing, change secrets, or publish publicly.

At the end report:
STATUS: PASS or STATUS: BLOCKED
Gemini requests per candidate:
Rate-limit strategy:
Candidates produced:
Real-media coverage:
Tests:
Changes:
External blocker:

## Rules

- Work only on this goal.
- Do not perform unrelated architecture audits.
- Do not invent additional work.
- Continue autonomously until PASS or a true external blocker.
- Codex is the primary implementer when code changes are needed.
- Claude is supervisor/reviewer.
- Run real tests before PASS.
- Do not push, publish, change secrets, OAuth credentials, billing, remote data, force-push, or rewrite git history without human approval.
