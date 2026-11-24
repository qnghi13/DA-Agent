"""Staging folders hold parsed uploads while a job runs. A job removes its own; this removes what a
crash left behind, so no copy of a dataset's rows outlives its deletion."""

import shutil
from pathlib import Path

STAGING_MAX_AGE_SECONDS = 86400


def purge_staging(
    root: Path, *, now: float, max_age_seconds: float = STAGING_MAX_AGE_SECONDS
) -> int:
    """Remove direct child folders of `root` not modified for `max_age_seconds`; return how many."""
    if not root.is_dir():
        return 0
    removed = 0
    for child in root.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            old = now - child.stat().st_mtime > max_age_seconds
        except OSError:
            continue
        if old:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    return removed
