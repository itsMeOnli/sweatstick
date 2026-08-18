"""Tests for settings loading."""

from __future__ import annotations

import pytest

from fallguys_pose.config import Settings, load_settings


def test_defaults_load_without_a_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.tuning.jump_threshold == 0.18
    assert settings.camera.backend == "dshow"


def test_a_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_settings(tmp_path / "nope.toml")


def test_toml_overrides_only_what_it_names(tmp_path):
    path = tmp_path / "fallguys.toml"
    path.write_text(
        "[tuning]\njump_threshold = 0.25\n\n[camera]\nsource = 1\n",
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.tuning.jump_threshold == 0.25
    assert settings.camera.source == 1
    # Untouched values keep their defaults.
    assert settings.tuning.cadence_max == 3.5
    assert settings.camera.backend == "dshow"


def test_a_url_camera_source_survives_loading(tmp_path):
    """Android's IP Webcam streams MJPEG over HTTP, with no driver at all."""
    path = tmp_path / "fallguys.toml"
    path.write_text(
        '[camera]\nsource = "http://192.168.1.5:8080/video"\n', encoding="utf-8"
    )
    assert load_settings(path).camera.source == "http://192.168.1.5:8080/video"


def test_a_typo_raises_instead_of_being_ignored(tmp_path):
    """A misspelt key must not silently do nothing."""
    path = tmp_path / "fallguys.toml"
    path.write_text("[tuning]\njump_treshold = 0.25\n", encoding="utf-8")
    with pytest.raises(ValueError, match="jump_treshold"):
        load_settings(path)


def test_settings_round_trip_to_a_dict():
    assert Settings().to_dict()["tuning"]["cadence_min"] == 1.2
