import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


import pytest


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """No test may touch the user's real preferences, taste or corpus.

    This was learned the expensive way: a schema test called
    set_sound_preference with role="lead", path="x/y" against the real
    config, and every track the user built afterwards had a silent lead --
    the junk path failed to load and nothing fell back.
    """
    from ableton_ai import sounds, taste

    monkeypatch.setattr(sounds, "CONFIG_PATH", tmp_path / "preferences.json")
    monkeypatch.setattr(sounds, "LEGACY_PATH", tmp_path / "sounds.json")
    monkeypatch.setattr(taste, "TASTE_PATH", tmp_path / "taste.json")
