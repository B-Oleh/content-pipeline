"""Thin research source abstraction.

This is the deliberate "provider abstraction" exception documented in
CLAUDE.md: research providers may change (new free feeds, rate limits,
future paid APIs), so pipeline logic never talks to a specific provider
directly. Adding a new source means implementing this one small interface
and registering it in scripts/research/cli.py's SOURCE_BUILDERS -- not
building a plugin framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from scripts.research.models import ResearchCandidate


class ResearchSource(ABC):
    """A provider of candidate content ideas."""

    name: str

    @abstractmethod
    def fetch(self) -> list[ResearchCandidate]:
        """Return newly discovered candidates.

        Implementations should raise on failure (network error, malformed
        feed, etc.) rather than returning an empty list silently -- callers
        are responsible for catching, logging, and continuing with other
        sources (see CLAUDE.md "Input sources").
        """
        raise NotImplementedError
