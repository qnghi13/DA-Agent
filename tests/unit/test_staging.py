"""Old staging folders are purged when the worker starts."""

import os
from pathlib import Path

from da_agent.worker.staging import purge_staging

NOW = 1_800_000_000.0


def test_only_staging_folders_older_than_a_day_are_purged(tmp_path: Path) -> None:
    old, fresh = tmp_path / "da-sbx-old", tmp_path / "da-sbx-fresh"
    old.mkdir()
    fresh.mkdir()
    (old / "rows.csv").write_text("secret")
    os.utime(old, (NOW - 86401, NOW - 86401))
    os.utime(fresh, (NOW - 60, NOW - 60))

    assert purge_staging(tmp_path, now=NOW) == 1

    assert not old.exists() and fresh.exists()


def test_a_missing_root_purges_nothing(tmp_path: Path) -> None:
    assert purge_staging(tmp_path / "absent", now=NOW) == 0


def test_loose_files_are_left_alone(tmp_path: Path) -> None:
    loose = tmp_path / "note.txt"
    loose.write_text("x")
    os.utime(loose, (NOW - 999999, NOW - 999999))

    assert purge_staging(tmp_path, now=NOW) == 0 and loose.exists()
