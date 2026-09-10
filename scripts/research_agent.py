"""CLI entry point for Research Agent V0.1.

Usage:
    python -m scripts.research_agent
    python -m scripts.research_agent --config path/to/sources.json --output-dir path/to/output

See scripts/research/cli.py for the implementation and docs/RESEARCH_AGENT.md for the design.
"""

from scripts.research.cli import main

if __name__ == "__main__":
    main()
