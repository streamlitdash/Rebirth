"""Deployment-bundle and Plotly Cloud command regression tests."""

from __future__ import annotations

from pathlib import Path

import s03_publish as publishing


def test_stage_bundle_uses_conventional_runtime_names(tmp_path: Path) -> None:
    staged = publishing.stage_bundle(tmp_path / "runtime")

    assert (staged / "app.py").read_bytes() == (
        publishing.PROJECT / "s01_app.py"
    ).read_bytes()
    assert (staged / "gunicorn.conf.py").read_bytes() == (
        publishing.PROJECT / "s04_server.py"
    ).read_bytes()
    assert (staged / "requirements.txt").is_file()
    requirements = (staged / "requirements.txt").read_text(encoding="utf-8")
    assert "tzdata==" in requirements
    assert not (staged / "s03_publish.py").exists()
    assert not (staged / "tests").exists()
    assert not (staged / "README.md").exists()


def test_publish_uses_plotly_native_entrypoint_discovery(
    tmp_path: Path, monkeypatch
) -> None:
    captured: dict[str, object] = {}

    def capture(command, *, cwd, check):
        captured.update(command=command, cwd=cwd, check=check)

    monkeypatch.setattr(publishing.subprocess, "run", capture)
    publishing.publish(keep_bundle=tmp_path)

    command = captured["command"]
    assert isinstance(command, list)
    assert "--entrypoint-module" not in command
    assert command[command.index("--name") + 1] == "rebirth"
    assert captured["cwd"] == publishing.PROJECT
    assert captured["check"] is True


def test_plotly_config_does_not_inherit_an_existing_app() -> None:
    config = publishing.CONFIG.read_text(encoding="utf-8")

    assert 'name = "rebirth"' in config
    assert "app_id" not in config
    assert "app_url" not in config
