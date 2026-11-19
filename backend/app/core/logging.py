import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for noisy in ("cmdstanpy", "prophet", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
