from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_private_project_media_is_git_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", ".chordatlas/private/locators/source.json"],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0
