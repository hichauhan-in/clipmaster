"""Tests for layered configuration loading and merging."""

from clipmaster.config import _deep_merge, load_settings


def test_defaults_load():
    settings = load_settings()
    assert settings.chunking.max_chunk_seconds == 1200
    assert settings.transcription.provider == "faster_whisper"
    assert settings.llm.host.startswith("http")
    assert settings.analysis.filler_words  # non-empty from default.yaml


def test_deep_merge_overrides_nested_keys_only():
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    override = {"nested": {"y": 20, "z": 30}}
    merged = _deep_merge(base, override)
    assert merged == {"a": 1, "nested": {"x": 1, "y": 20, "z": 30}}
    # Inputs are not mutated.
    assert base["nested"] == {"x": 1, "y": 2}


def test_config_path_override(tmp_path):
    override = tmp_path / "over.yaml"
    override.write_text("transcription:\n  model: medium\n", encoding="utf-8")
    settings = load_settings(override)
    assert settings.transcription.model == "medium"
    # Untouched keys keep their defaults.
    assert settings.chunking.max_chunk_seconds == 1200


def test_workspace_path_is_created(tmp_path):
    override = tmp_path / "over.yaml"
    workspace = tmp_path / "ws"
    override.write_text(f"workspace_dir: {workspace.as_posix()}\n", encoding="utf-8")
    settings = load_settings(override)
    assert settings.workspace_path.exists()
