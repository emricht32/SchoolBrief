import logging
import sys

def setup_logger(name: str):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # 👈 must be DEBUG to see .debug()
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.propagate = False
    return logger

logger = setup_logger("SchoolBrief")

def example_function():
    logger.debug("This is a debug log")
    logger.info("This is an info log")
    logger.warning("This is a warning")

if __name__ == "__main__":
    example_function()
