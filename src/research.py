"""CLI entry point for the Researcher agent."""
import sys

from .agents import main as pipeline_main


def main():
    # Keep the concise public CLI while sharing the full two-agent runner.
    if "--mode" not in sys.argv:
        sys.argv[1:1] = ["--mode", "research"]
    pipeline_main()


if __name__ == "__main__":
    main()
