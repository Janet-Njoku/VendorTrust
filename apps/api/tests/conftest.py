"""Test isolation: no test may ever see the real home folder or inherited credentials.

Every test runs with HOME pointing at an empty temporary folder and with every inherited
VT_* variable removed (except VT_TEST_*, which only configure the local test database).
Credential tests therefore can only ever touch fake files in temporary folders, and the real
~/.config/vendortrust/ is unreachable from the suite by construction.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for name in list(os.environ):
        if name.startswith("VT_") and not name.startswith("VT_TEST_"):
            monkeypatch.delenv(name)
    return home
