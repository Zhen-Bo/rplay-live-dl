import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(name: str) -> logging.Logger:
    """Configure and create a logger instance with both console and file output

    Args:
        name (str): logger name

    Returns:
        logging.Logger: configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # Avoid adding duplicate handlers
    if not logger.handlers:
        # Create logs directory if it doesn't exist
        logs_dir = Path(__file__).parent.parent / "logs"
        logs_dir.mkdir(exist_ok=True)

        # Configure console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Configure file handler with rotation
        log_file = logs_dir / f"{name}.log"
        file_handler = RotatingFileHandler(
            filename=str(log_file),  # RotatingFileHandler requires string path
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)

        # Create formatter and add it to the handlers
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(formatter)
        file_handler.setFormatter(formatter)

        # Add handlers to logger
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

    return logger
