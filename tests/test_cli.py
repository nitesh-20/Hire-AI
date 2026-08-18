"""Unit tests for hirepilot CLI parsing and execution."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hirepilot.cli import main

ROOT = Path(__file__).resolve().parent.parent


def test_cli_run_mock(tmp_path):
    """Test standard mock run execution via CLI arguments."""
    seen_file = tmp_path / "seen.json"
    digest_file = tmp_path / "out" / "digest.html"
    cfg_file = tmp_path / "config.yaml"

    base_cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    base_cfg["seen_file"] = str(seen_file)
    base_cfg["digest_file"] = str(digest_file)
    base_cfg["score_threshold"] = 0.0
    cfg_file.write_text(yaml.dump(base_cfg), encoding="utf-8")

    # Test running with --config before subcommand
    code = main(["--config", str(cfg_file), "run", "--mock", "--scorer", "keyword"])
    assert code == 0
    assert seen_file.exists()

    # Test running with --config after subcommand
    code_after = main(["run", "--config", str(cfg_file), "--mock", "--scorer", "keyword", "--limit", "5"])
    assert code_after == 0


def test_cli_run_no_draft(tmp_path):
    """Test --no-draft flag in mock run."""
    seen_file = tmp_path / "seen.json"
    cfg_file = tmp_path / "config.yaml"

    base_cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    base_cfg["seen_file"] = str(seen_file)
    base_cfg["score_threshold"] = 0.0
    cfg_file.write_text(yaml.dump(base_cfg), encoding="utf-8")

    code = main(["run", "--config", str(cfg_file), "--mock", "--scorer", "keyword", "--no-draft"])
    assert code == 0


def test_cli_stats_command(tmp_path):
    """Test stats command output."""
    seen_file = tmp_path / "seen.json"
    seen_file.write_text("{}", encoding="utf-8")
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        f"""seen_file: "{seen_file}"
tracker_csv: "{tmp_path / 'tracker.csv'}"
""",
        encoding="utf-8",
    )

    code = main(["--config", str(cfg_file), "stats"])
    assert code == 0


def test_cli_version_flag(capsys):
    """Test --version and -v CLI flags."""
    from hirepilot import __version__

    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out or __version__ in captured.err

    with pytest.raises(SystemExit) as exc_info:
        main(["-v"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert __version__ in captured.out or __version__ in captured.err

