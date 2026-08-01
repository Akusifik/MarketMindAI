"""MarketMind AI command-line entry point."""

import argparse
import asyncio

from logs.logger import logger
from runtime.application import run_application


def main(argv=None):
    parser = argparse.ArgumentParser(description="MarketMind AI")
    parser.add_argument("--mode", choices=("one-shot", "live"), default=None)
    args = parser.parse_args(argv)
    logger.info("Starting MarketMind AI")
    try:
        overrides = {"mode": args.mode} if args.mode else {}
        asyncio.run(run_application(**overrides))
    except KeyboardInterrupt:
        logger.info("MarketMind AI stopped")


if __name__ == "__main__":
    main()
