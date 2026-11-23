"""CLI entry point for the independent Evaluator agent."""
import sys

from .agents import main as pipeline_main


def main():
    if "--mode" not in sys.argv:
        sys.argv[1:1] = ["--mode", "evaluate"]
    pipeline_main()


if __name__ == "__main__":
    main()
