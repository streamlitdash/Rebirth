"""Deployment-bundle and Plotly Cloud command regression tests."""

from __future__ import annotations

import tomllib
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
    for page_module in (
        "__init__.py",
        "risk.py",
        "static_data.py",
        "not_found_404.py",
    ):
        assert (staged / "pages" / page_module).read_bytes() == (
            publishing.PROJECT / "pages" / page_module
        ).read_bytes()
    assert not any((staged / "pages").rglob("__pycache__"))
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


def test_plotly_config_targets_the_rebirth_app() -> None:
    config = tomllib.loads(publishing.CONFIG.read_text(encoding="utf-8"))

    assert config == {
        "name": "rebirth",
        "app_id": "7a4087c3-84a7-4f1a-a5fb-d5ac2cccb661",
        "app_url": "71053046-5033-4d1f-8024-3494abf67602",
    }
