# Configuration for a logger for the UtilityBillCalculatorClass

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

# Single root logger for the entire package.  All child loggers created with
# logging.getLogger(__name__) inside any buildstock_rate_impact_calculator module inherit
# from this logger automatically.
ROOT_LOGGER_NAME = "buildstock_rate_impact_calculator"


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger that is a child of the package root logger."""
    return logging.getLogger(name or ROOT_LOGGER_NAME)


def setup_logging(
    log_file: Optional[Path] = None,
    log_level: int = logging.INFO,
    module_name: str = ROOT_LOGGER_NAME,  # kept for backward compat, ignored
) -> logging.Logger:
    """
    Configure the buildstock_rate_impact_calculator root logger (once).

    All child loggers obtained via ``logging.getLogger(__name__)`` inside the
    package inherit handlers and level from this root logger automatically,
    so this function only needs to be called once (e.g. from app.py).

    Args:
        log_file: Optional path to log file. If None, logs to console only.
        log_level: Logging level (default: INFO)
        module_name: Deprecated – ignored. Kept only for backward compatibility.

    Returns:
        Configured root logger instance
    """
    # Always configure the single package-level root logger
    logger = logging.getLogger(ROOT_LOGGER_NAME)

    # prevent adding duplicate handlers
    if logger.handlers:
        return logger

    logger.setLevel(log_level)

    # Create formatters
    detailed_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    )
    console_formatter = logging.Formatter("%(levelname)s - %(message)s")

    # only add console handler if log_level is WARNING or higher
    # Console handler (INFO and below)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(lambda record: record.levelno <= logging.INFO)
    logger.addHandler(console_handler)

    # Error console handler (WARNING and above)
    error_console_handler = logging.StreamHandler(sys.stderr)
    error_console_handler.setLevel(logging.WARNING)
    error_console_handler.setFormatter(detailed_formatter)
    logger.addHandler(error_console_handler)

    # File handler (if log file specified)
    if log_file:
        try:
            # file_handler = logging.handlers.RotatingFileHandler(
            #     log_file,
            #     maxBytes=10*1024*1024,  # 10MB
            #     backupCount=5
            # )
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(log_level)
            file_handler.setFormatter(detailed_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to set up file logging at {log_file}: {e}")

    # Log initial setup
    logger.info(f"Logger initialized for {module_name}")
    logger.debug(f"Log level set to {logging.getLevelName(log_level)}")
    if log_file:
        logger.debug(f"Logging to file: {log_file}")

    # prevent propagation to root logger
    # logger.propagate = False

    return logger


# Example log message categories for consistent messaging
class LogMessages:
    """Standard log message templates for consistent logging."""

    # Data validation
    VALIDATION_ERROR = "Validation error: {}"
    VALIDATION_WARNING = "Validation warning: {}"

    # Data processing
    PROCESSING_START = "Starting {} processing"
    PROCESSING_COMPLETE = "Completed {} processing"
    PROCESSING_ERROR = "Error during {} processing: {}"

    # Rate structure
    RATE_FETCH_START = "Fetching rate data for {}"
    RATE_FETCH_COMPLETE = "Successfully fetched rate data: {}"
    RATE_SETUP_ERROR = "Error setting up rate structure: {}"

    # Load profile
    LOAD_PROFILE_START = "Reading load profile from {}"
    LOAD_PROFILE_COMPLETE = "Successfully read load profile: {} points"
    LOAD_PROFILE_ERROR = "Error reading load profile: {}"

    # Calculations
    CALCULATION_START = "Starting bill calculation"
    CALCULATION_COMPLETE = "Completed bill calculation"
    CALCULATION_ERROR = "Error during bill calculation: {}"

    # Configuration
    CONFIG_ERROR = "Configuration error: {}"
    CONFIG_WARNING = "Configuration warning: {}"

    @staticmethod
    def data_summary(name: str, data) -> str:
        """Create a standard data summary message."""
        return (
            f"Data summary for {name}: "
            f"shape={getattr(data, 'shape', 'N/A')}, "
            f"type={type(data).__name__}"
        )
