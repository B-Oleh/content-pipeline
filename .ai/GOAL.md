# Current Goal

Build and validate an autonomous overnight content batch for FrameForge.

Goal:
By the end of this run, the system must be able to autonomously produce 3 distinct high-quality video candidates in one batch so I can review them the next morning and choose which one to publish.

Claude is the content-quality supervisor. Codex is the implementation agent.

Requirements:

1. Generate 3 distinct video candidates, not minor variations of the same topic.
2. Before scripting each candidate, define:
   - target audience
   - viewer pain/problem
   - topic angle
   - 2-3 hook candidates
   - selected hook and why it should retain attention
3. Prefer topics with strong practical value, curiosity, buying mistakes, performance problems, myths, optimization, or game/hardware decisions.
4. Avoid generic or repetitive topics.
5. Use existing research/topic scoring as the base and improve it only where evidence shows it is weak.
6. Each rendered candidate must pass:
   - >=80% meaningful real-media runtime coverage
   - <=1 consecutive info-card
   - normal QA
   - on-topic validation
   - no fabricated factual claims
7. If a candidate fails before render because of topic/visual quality, autonomously try another candidate.
8. If an API quota or external provider blocks further candidates, keep all successfully completed candidates and report the blocker clearly.
9. Send every successful candidate to Telegram separately.
10. For each Telegram candidate include a concise summary:
    - Candidate number
    - Topic
    - Target audience
    - Hook
    - Duration
    - Real-media coverage
    - QA result
11. Do NOT publicly publish anything.
12. Do NOT automatically choose a winner for me.
13. Existing Approve behavior may upload a selected video privately to YouTube, but public publication always requires human action.
14. Store batch metadata so future agents can compare what topics/hooks were attempted and avoid unnecessary repetition.
15. Do not build speculative infrastructure unrelated to this overnight batch.

Implementation process:
- Inspect the current production pipeline and reuse existing components.
- Claude defines the quality criteria and reviews the plan.
- Codex implements necessary changes.
- Claude reviews Codex changes and sends defects back until correct.
- Run targeted tests during implementation.
- Run the full pytest suite before shipping.
- You are approved to create a normal git commit and push validated changes to origin/main.
- No force push, history rewrite, secret/OAuth changes, billing changes, or public publishing.
- After push, run the real GitHub Actions production batch using existing GitHub Secrets.
- Continue autonomously until either:
  A) 3 successful candidate videos are delivered to Telegram, or
  B) a genuine external blocker prevents completion.

At the end report:
STATUS: PASS or STATUS: BLOCKED
Candidates produced:
Topics:
Hooks:
Target audiences:
Real-media coverage:
Tests:
Changes:
External blockers:
Human action required:

## Rules

- Work only on this goal.
- Do not perform unrelated architecture audits.
- Do not invent additional work.
- Continue autonomously until PASS or a true external blocker.
- Codex is the primary implementer when code changes are needed.
- Claude is supervisor/reviewer.
- Run real tests before PASS.
- Do not push, publish, change secrets, OAuth credentials, billing, remote data, force-push, or rewrite git history without human approval.
