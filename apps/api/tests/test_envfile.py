"""Strict parser and secure file checks of the credential loader (fake files only)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import envfile
from app.envfile import (
    EnvFileError,
    EnvFileMissingError,
    LoadedEnvFile,
    load_env_file,
    parse_env_text,
    read_secure_text,
)
from tests._creds import CANARY, assert_no_canary, full_text, secure_dir, write_file

ALLOWED = frozenset({"VT_A", "VT_B"})


# --- parser -------------------------------------------------------------------------------


def test_parses_names_comments_blank_lines_and_crlf() -> None:
    text = "# comment\r\n\r\nVT_A=one\r\n  VT_B = two  \r\n"
    assert parse_env_text(text) == {"VT_A": "one", "VT_B": "two"}


def test_values_may_contain_equals_signs_and_urls() -> None:
    url = "postgresql://u:p@h:5432/db?sslmode=require&x=y"
    assert parse_env_text(f"VT_A={url}\n") == {"VT_A": url}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("VT_A=value   # trailing comment", "value"),
        ("VT_A=value#not-a-comment", "value#not-a-comment"),
        ('VT_A="quoted # not a comment"', "quoted # not a comment"),
        ("VT_A='single quoted'", "single quoted"),
        ('VT_A="quoted"  # then a comment', "quoted"),
        ("VT_A=", ""),
        ("VT_A=#only a comment", ""),
    ],
)
def test_value_forms(line: str, expected: str) -> None:
    assert parse_env_text(line) == {"VT_A": expected}


@pytest.mark.parametrize("literal", ["${HOME}", "$(whoami)", "`id`", "$OTHER", "%HOME%"])
def test_no_interpolation_or_expansion(literal: str) -> None:
    assert parse_env_text(f"VT_A={literal}\nVT_B=x\n") == {"VT_A": literal, "VT_B": "x"}


@pytest.mark.parametrize(
    "text",
    [
        "no equals sign",
        "lower=case",
        "1BAD=name",
        "export VT_A=1",
        "VT_A=1\nVT_A=2",
        'VT_A="unterminated',
        'VT_A="quoted" trailing',
        "VT_A=bad\x00byte",
        "VT_A=bad\x07bell",
        "=novalue",
        "VT A=spaces",
    ],
)
def test_malformed_input_is_rejected(text: str) -> None:
    with pytest.raises(EnvFileError):
        parse_env_text(text)


def test_too_many_lines_rejected() -> None:
    with pytest.raises(EnvFileError, match="lines"):
        parse_env_text("# c\n" * 250)


@pytest.mark.parametrize(
    "text",
    [
        f"this line is junk and mentions {CANARY}",
        f"lower_case_name={CANARY}",
        f"VT_A={CANARY}\nVT_A={CANARY}",
        f'VT_A="{CANARY} unterminated',
        f'VT_A="{CANARY}" trailing {CANARY}',
        f"VT_A={CANARY}\x00",
    ],
)
def test_parse_errors_never_echo_content(text: str) -> None:
    with pytest.raises(EnvFileError) as info:
        parse_env_text(text, source="api.env")
    assert_no_canary(full_text(info.value))


# --- secure reading --------------------------------------------------------------------------


def good_file(tmp_path: Path, text: str = f"VT_A={CANARY}\n") -> Path:
    return write_file(secure_dir(tmp_path), "api.env", text)


def test_reads_a_properly_protected_file(tmp_path: Path) -> None:
    text, warnings = read_secure_text(good_file(tmp_path), repo_root=tmp_path / "elsewhere")
    assert CANARY in text
    assert warnings == ()


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o602])
def test_file_accessible_to_group_or_others_is_refused(tmp_path: Path, mode: int) -> None:
    path = good_file(tmp_path)
    path.chmod(mode)
    with pytest.raises(EnvFileError, match="chmod 600") as info:
        read_secure_text(path, repo_root=tmp_path / "elsewhere")
    assert_no_canary(full_text(info.value))


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_owner_only_modes_are_accepted(tmp_path: Path, mode: int) -> None:
    path = good_file(tmp_path)
    path.chmod(mode)
    assert read_secure_text(path, repo_root=tmp_path / "elsewhere")[0]


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o772, 0o757])
def test_folder_writable_by_others_is_refused(tmp_path: Path, mode: int) -> None:
    path = good_file(tmp_path)
    path.parent.chmod(mode)
    with pytest.raises(EnvFileError, match="writable by other users"):
        read_secure_text(path, repo_root=tmp_path / "elsewhere")


@pytest.mark.parametrize("mode", [0o755, 0o750, 0o705])
def test_readable_folder_is_accepted_with_a_warning_recommending_700(
    tmp_path: Path, mode: int
) -> None:
    path = good_file(tmp_path)
    path.parent.chmod(mode)
    _, warnings = read_secure_text(path, repo_root=tmp_path / "elsewhere")
    assert len(warnings) == 1
    assert "0700" in warnings[0]


def test_folder_mode_700_gives_no_warning(tmp_path: Path) -> None:
    assert read_secure_text(good_file(tmp_path), repo_root=tmp_path / "x")[1] == ()


def test_symlinked_file_is_refused(tmp_path: Path) -> None:
    target = good_file(tmp_path / "real")
    link_dir = secure_dir(tmp_path / "links")
    (link_dir / "api.env").symlink_to(target)
    with pytest.raises(EnvFileError, match="symlink"):
        read_secure_text(link_dir / "api.env", repo_root=tmp_path / "elsewhere")


def test_symlinked_folder_is_refused(tmp_path: Path) -> None:
    real = good_file(tmp_path / "real").parent
    (tmp_path / "link").symlink_to(real)
    with pytest.raises(EnvFileError, match="not a symlink"):
        read_secure_text(tmp_path / "link" / "api.env", repo_root=tmp_path / "elsewhere")


def test_a_regular_file_in_place_of_the_folder_is_refused(tmp_path: Path) -> None:
    not_a_folder = tmp_path / "afile"
    not_a_folder.write_text("x")
    not_a_folder.chmod(0o600)
    with pytest.raises(EnvFileError, match="is not a folder"):
        read_secure_text(not_a_folder / "api.env", repo_root=tmp_path / "elsewhere")


def test_directory_in_place_of_the_file_is_refused(tmp_path: Path) -> None:
    folder = secure_dir(tmp_path)
    (folder / "api.env").mkdir(mode=0o700)
    with pytest.raises(EnvFileError, match="regular file"):
        read_secure_text(folder / "api.env", repo_root=tmp_path / "elsewhere")


def _owned_by_another_user(result: os.stat_result) -> os.stat_result:
    fields = list(result)
    fields[4] = result.st_uid + 1  # st_uid
    return os.stat_result(fields)


def test_folder_owned_by_someone_else_is_refused_even_if_the_file_is_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = good_file(tmp_path)
    real_lstat = os.lstat

    def lstat(target: str | Path, *args: object, **kwargs: object) -> os.stat_result:
        result = real_lstat(target, *args, **kwargs)  # type: ignore[arg-type]
        return _owned_by_another_user(result) if Path(target) == path.parent else result

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(EnvFileError, match="folder must be owned by the current user"):
        read_secure_text(path, repo_root=tmp_path / "elsewhere")


def test_file_owned_by_someone_else_is_refused_even_if_the_folder_is_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = good_file(tmp_path)
    real_fstat = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: _owned_by_another_user(real_fstat(fd)))
    with pytest.raises(EnvFileError, match="file must be owned by the current user") as info:
        read_secure_text(path, repo_root=tmp_path / "elsewhere")
    assert_no_canary(full_text(info.value))


def test_when_this_process_runs_as_a_different_user_everything_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = good_file(tmp_path)
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    with pytest.raises(EnvFileError, match="owned by the current user"):
        read_secure_text(path, repo_root=tmp_path / "elsewhere")


def test_file_inside_the_repository_is_refused_even_if_perfectly_protected(tmp_path: Path) -> None:
    path = good_file(tmp_path)  # tmp_path plays the role of the git repository
    with pytest.raises(EnvFileError, match="outside the git repository") as info:
        read_secure_text(path, repo_root=tmp_path)
    assert_no_canary(full_text(info.value))


def test_default_repo_root_is_this_repository() -> None:
    assert (envfile.REPO_ROOT / "apps" / "api" / "app" / "envfile.py").is_file()


def test_relative_paths_are_refused() -> None:
    with pytest.raises(EnvFileError, match="absolute"):
        read_secure_text(Path("api.env"))


def test_missing_file_and_missing_folder_are_reported_as_missing(tmp_path: Path) -> None:
    folder = secure_dir(tmp_path)
    with pytest.raises(EnvFileMissingError):
        read_secure_text(folder / "api.env", repo_root=tmp_path / "elsewhere")
    with pytest.raises(EnvFileMissingError):
        read_secure_text(tmp_path / "nope" / "api.env", repo_root=tmp_path / "elsewhere")


def test_oversized_file_is_refused(tmp_path: Path) -> None:
    path = write_file(secure_dir(tmp_path), "api.env", "#" * (envfile.MAX_BYTES + 10))
    with pytest.raises(EnvFileError, match="larger than"):
        read_secure_text(path, repo_root=tmp_path / "elsewhere")


def test_invalid_utf8_is_refused_and_a_bom_is_tolerated(tmp_path: Path) -> None:
    folder = secure_dir(tmp_path)
    bad = folder / "bad.env"
    bad.write_bytes(b"VT_A=\xff\xfe\n")
    bad.chmod(0o600)
    with pytest.raises(EnvFileError, match="UTF-8"):
        read_secure_text(bad, repo_root=tmp_path / "elsewhere")
    bom = folder / "bom.env"
    bom.write_bytes(b"\xef\xbb\xbfVT_A=1\n")
    bom.chmod(0o600)
    assert parse_env_text(read_secure_text(bom, repo_root=tmp_path / "x")[0]) == {"VT_A": "1"}


# --- allowlist and output hygiene -----------------------------------------------------------


def test_load_returns_values_for_allowed_names(tmp_path: Path) -> None:
    path = good_file(tmp_path, f"VT_A={CANARY}\nVT_B=2\n")
    loaded = load_env_file(path, allowed=ALLOWED, repo_root=tmp_path / "elsewhere")
    assert loaded.values == {"VT_A": CANARY, "VT_B": "2"}


def test_unknown_variable_names_are_rejected_and_named(tmp_path: Path) -> None:
    path = good_file(tmp_path, f"VT_A=1\nVT_SURPRISE={CANARY}\nPGPASSWORD={CANARY}\n")
    with pytest.raises(EnvFileError, match="unexpected variable name") as info:
        load_env_file(path, allowed=ALLOWED, repo_root=tmp_path / "elsewhere")
    message = str(info.value)
    assert "VT_SURPRISE" in message
    assert "PGPASSWORD" in message
    assert_no_canary(full_text(info.value))


def test_loaded_file_repr_never_shows_values(tmp_path: Path) -> None:
    loaded = load_env_file(good_file(tmp_path), allowed=ALLOWED, repo_root=tmp_path / "elsewhere")
    assert isinstance(loaded, LoadedEnvFile)
    assert_no_canary(repr(loaded), str(loaded))
    assert "VT_A" in repr(loaded)
