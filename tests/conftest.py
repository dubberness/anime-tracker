import os
import sys

# The app runs with /app on sys.path inside the container, so mirror that here
# rather than turning every module into a relative import.
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import pytest  # noqa: E402

import logging_setup  # noqa: E402

logging_setup.configure("WARNING")


@pytest.fixture
def runtime(tmp_path):
    import config as config_mod

    rt = config_mod.Runtime()
    rt.config_dir = str(tmp_path)
    rt.config_file = str(tmp_path / "config.json")
    rt.cache_file = str(tmp_path / "cache.json")
    rt.results_file = str(tmp_path / "results.json")
    rt.database_file = str(tmp_path / "history.db")
    return rt


@pytest.fixture
def clean_env(monkeypatch):
    """Env vars leak between tests otherwise, since they override settings."""
    import config as config_mod

    for name in config_mod.ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch
