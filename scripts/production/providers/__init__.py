"""Thin, replaceable provider boundaries (LLM, visual assets, voice, Telegram).

See CLAUDE.md "Provider abstraction": these are the deliberate exception to
"avoid unnecessary generic frameworks" -- provider availability, pricing, and
free tiers may change, so pipeline stage logic never calls a vendor SDK
directly. Each provider module holds one small ABC plus its concrete
implementation(s); no plugin framework.
"""
