# Business Strategy

Living document for the current business model and content niche of **content-pipeline**.
Update this file directly when the model, revenue stage, or niche changes. Engineering rules,
architecture, and pipeline stage definitions live in [`CLAUDE.md`](../CLAUDE.md) — this file
covers business context only, not implementation.

Status: strategy defined; pipeline not yet implemented (see "Current development stage" in
CLAUDE.md).

## Goal

Build a **$0-cost autonomous gaming content business**: an automated pipeline that researches,
produces, and (after human approval) publishes short-form gaming content, growing revenue through
distinct, increasingly ambitious stages without upfront spend.

## Revenue milestones

| Stage | Revenue range | Primary monetization |
|---|---|---|
| 1 | $0 → $100 | Affiliate revenue |
| 2 | $100 → $500 | Affiliate revenue + audience growth + experimentation with long-form content |
| 3 | $500 → $1,000+ | Affiliate revenue + YouTube monetization + sponsorships + reinvestment into better tools |

**Do not assume YouTube ad revenue is available during Stage 1.** Affiliate links are the only
monetization channel the pipeline and content strategy should rely on until the channel qualifies
for YouTube monetization and that is confirmed.

Reinvestment (Stage 3) means profits may fund better tools/services, but every such purchase still
goes through the "API integrations" and "Human approval required" rules in CLAUDE.md — reaching
Stage 3 does not remove the approval requirement.

## Content niche

**Primary niche:** gaming hardware, gaming setup optimization, buying advice, and useful gaming
technology content.

Possible formats:

- product comparisons
- budget gaming gear
- best products under a specific price
- gaming PC optimization
- Windows/game settings
- upgrade advice
- hardware explanations
- gaming technology facts
- mistakes and myths
- deals, when reliably verifiable

Content must stay original, useful, and varied — avoid making every video an identical "Top 3"
template. This is also reflected in CLAUDE.md's priorities (originality ranks above automation)
and video principles (no low-effort spam, no near-duplicate mass production).

Because this niche makes factual/technical claims (specs, prices, benchmarks, compatibility) that
directly inform purchase decisions, the pipeline includes a dedicated Fact Checking stage — see
CLAUDE.md's "Fact checking" and "Research and opportunity scoring rules" sections. Affiliate
potential and commercial intent are explicit scoring factors for topic selection because affiliate
revenue is the only monetization channel in Stage 1.

## Content pillar: "What to play this month"

Status: defined; not yet implemented.

A second recurring content pillar, alongside the primary hardware/buying-advice niche described
above. A **monthly** series recommending games for different PC performance tiers, meant to reach
viewers who are not actively shopping for hardware — broadening the audience beyond the primary
niche's buyers.

**Core categories:**

1. Low-end PC
2. Mid-range PC
3. High-end PC

**Possible formats:**

- What to play on a low-end PC this month
- What to play on a mid-range PC this month
- What to play on a powerful PC this month
- Best new games this month for low-end PCs
- Best free games this month for low-end PCs
- Best-looking games for powerful PCs
- Weekend gaming recommendations by hardware tier
- Games for specific popular GPUs or hardware classes
- Games that run surprisingly well on weak hardware

**Strategic purpose:** grow reach beyond the hardware-buyer audience, while still feeding the same
Stage 1 affiliate-revenue model (see "Revenue milestones") by connecting game recommendations to
hardware follow-up content where relevant. Example funnel:

> Game recommendations for GTX 1650 users → upgrade comparison → budget GPU recommendation →
> affiliate opportunity

**Content variety:** this pillar must feel curated, not templated. Do not recommend the same games
every month without a strong reason — balance new releases, older hidden gems, free games, popular
games, indie titles, and AAA titles across the recurring series.

The engineering rules that make this pillar viable (avoiding fabricated performance claims,
checking hardware-tier suitability, persisting recommendation history) are documented in
[`CLAUDE.md`](../CLAUDE.md)'s "Game recommendation integrity" section — not duplicated here.

## Human approval via Telegram

Telegram is the intended primary human-approval interface once the pipeline exists: every
candidate video is reviewed (rendered video, topic, proposed title, selection rationale, scores)
before it can be published, with Approve / Regenerate / Reject actions. See CLAUDE.md's "Telegram
approval gate" for the technical requirements (event-driven callbacks, no publishing before
Approve). This reflects the business need for a human checkpoint before anything reaches the
public channel, especially while the content and scoring model are unproven.

## Cloud execution and budget

GitHub Actions is the initial planned execution environment, chosen because it fits the $0 budget
constraint of Stage 1. See CLAUDE.md's "Cloud execution and budget" and "Provider abstraction" for
the engineering rules that keep this affordable and swappable (free-tier LLM provider by default,
every provider decoupled behind an interface, paid providers require explicit approval with a cost
estimate before use).

## Research scoring rationale

Topic scoring exists to make topic selection accountable to this business model rather than
arbitrary. Scores are persisted so that, once videos are published and analytics come back, the
system can find out which score characteristics (e.g. high affiliate potential, high novelty)
actually correlated with revenue and retention — see CLAUDE.md's "Analytics and learning feedback
loop". The scoring factors themselves are defined in CLAUDE.md, not duplicated here, so there is
one source of truth for them.
