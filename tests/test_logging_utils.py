from scripts.utils.logging_utils import get_logger


def test_get_logger_configures_handlers():
    logger = get_logger("test_logging_utils_logger")

    assert logger.name == "test_logging_utils_logger"
    assert len(logger.handlers) == 2


def test_get_logger_does_not_duplicate_handlers():
    logger_a = get_logger("test_logging_utils_idempotent")
    logger_b = get_logger("test_logging_utils_idempotent")

    assert logger_a is logger_b
    assert len(logger_a.handlers) == 2
