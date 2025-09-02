import logging
import sys
from pathlib import Path

def setup_logger(name: str, log_file: str = "app.log", level=logging.DEBUG):
    """
    Set up a custom logger that writes to both console and a file.
    """
    # Create a custom logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Prevent adding duplicate handlers if setup_logger is called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()

    # Formatter with function, file, line info
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d in %(funcName)s() - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    # File handler
    log_path = Path(log_file)
    file_handler = logging.FileHandler(log_path, mode="a")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# Example usage
logger = setup_logger("SchoolBrief", "SchoolBrief.log")

def example_function():
    logger.debug("This is a debug log")
    logger.info("This is an info log")
    logger.warning("This is a warning")

if __name__ == "__main__":
    example_function()
