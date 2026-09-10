from scripts import config


def test_shared_directories_exist():
    assert config.ASSETS_DIR.exists()
    assert config.OUTPUT_DIR.exists()
    assert config.DATA_DIR.exists()
    assert config.LOGS_DIR.exists()
    assert config.STATE_DIR.exists()


def test_log_level_has_a_default():
    assert isinstance(config.LOG_LEVEL, str)
    assert config.LOG_LEVEL != ""


def test_state_dir_is_relative_to_base_dir_not_hard_coded():
    assert config.STATE_DIR == config.BASE_DIR / "state"


def test_state_dir_is_separate_from_data_dir():
    # state/ (persistent, tracked in Git) and data/ (transient, gitignored)
    # must never be nested inside one another -- see docs/RESEARCH_AGENT.md
    # "Git-backed persistent state" and "Persistent state vs transient
    # output".
    assert config.DATA_DIR not in config.STATE_DIR.parents
    assert config.STATE_DIR not in config.DATA_DIR.parents
    assert config.STATE_DIR != config.DATA_DIR
