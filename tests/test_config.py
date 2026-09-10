from scripts import config


def test_shared_directories_exist():
    assert config.ASSETS_DIR.exists()
    assert config.OUTPUT_DIR.exists()
    assert config.DATA_DIR.exists()
    assert config.LOGS_DIR.exists()


def test_log_level_has_a_default():
    assert isinstance(config.LOG_LEVEL, str)
    assert config.LOG_LEVEL != ""
