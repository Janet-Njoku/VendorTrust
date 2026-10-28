"""Strict, safe loader for credential files that live outside the repository.

Nothing here runs on import. A file is read only when a caller asks for it, and every refusal
is explained without ever repeating a value from the file: messages name the file, a line
number or a variable name, never the contents of a line.

Format: UTF-8, one `NAME=value` per line. `#` starts a comment (on its own line, or after
whitespace following an unquoted value). Values may be wrapped in matching single or double
quotes. There is NO variable expansion, no `export`, no multi-line values and no escapes.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 64 * 1024
MAX_LINES = 200
NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
INLINE_COMMENT = re.compile(r"\s#")
REPO_ROOT = Path(__file__).resolve().parents[3]


class EnvFileError(Exception):
    """A credential file is missing, unsafe or malformed. Messages never contain values."""


class EnvFileMissingError(EnvFileError):
    """The file (or its folder) does not exist."""


@dataclass(frozen=True, repr=False)
class LoadedEnvFile:
    path: Path
    values: dict[str, str]
    warnings: tuple[str, ...] = ()

    def __repr__(self) -> str:  # never show values
        return f"LoadedEnvFile(path={str(self.path)!r}, names={sorted(self.values)})"


def parse_env_text(text: str, source: str = "<text>") -> dict[str, str]:
    """Parse `NAME=value` text strictly. Raises EnvFileError without echoing any content."""
    values: dict[str, str] = {}
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise EnvFileError(f"{source}: more than {MAX_LINES} lines")
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r").strip()
        if not line or line.startswith("#"):
            continue
        name, separator, rest = line.partition("=")
        name = name.strip()
        if not separator or not NAME_PATTERN.fullmatch(name):
            raise EnvFileError(f"{source}: line {number} is malformed (expected NAME=value)")
        if name in values:
            raise EnvFileError(f"{source}: line {number} repeats the variable {name}")
        value = _parse_value(rest.strip(), f"{source}: line {number}")
        if any(ord(ch) < 32 and ch != "\t" for ch in value):
            raise EnvFileError(f"{source}: line {number} contains a control character")
        values[name] = value
    return values


def _parse_value(rest: str, where: str) -> str:
    if rest[:1] in {'"', "'"}:
        quote = rest[0]
        end = rest.find(quote, 1)
        if end == -1:
            raise EnvFileError(f"{where} has an unterminated quote")
        tail = rest[end + 1 :].strip()
        if tail and not tail.startswith("#"):
            raise EnvFileError(f"{where} has text after the closing quote")
        return rest[1:end]
    if rest.startswith("#"):
        return ""
    match = INLINE_COMMENT.search(rest)
    return (rest[: match.start()] if match else rest).strip()


def _inside(child: Path, root: Path) -> bool:
    c, r = str(child).casefold(), str(root).casefold().rstrip(os.sep)
    return c == r or c.startswith(r + os.sep)


def _reason(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno or 0, "OSError").lower()


def read_secure_text(path: Path, *, repo_root: Path | None = None) -> tuple[str, tuple[str, ...]]:
    """Read a credential file after verifying where it is and who can touch it.

    Returns (text, warnings). Raises EnvFileMissingError if it does not exist and EnvFileError
    for anything unsafe. The file is opened without following symlinks and inspected through
    the open descriptor, so it cannot be swapped between the check and the read.
    """
    if not path.is_absolute():
        raise EnvFileError(f"{path}: the credential file path must be absolute")
    root = (repo_root or REPO_ROOT).resolve()
    if _inside(Path(os.path.realpath(path)), root):
        raise EnvFileError(f"{path}: credentials must live outside the git repository")

    uid = os.getuid()
    parent = path.parent
    try:
        parent_stat = os.lstat(parent)
    except FileNotFoundError as exc:
        raise EnvFileMissingError(f"{path}: the folder does not exist") from exc
    except OSError as exc:
        raise EnvFileError(f"{parent}: cannot inspect the folder ({_reason(exc)})") from exc
    if stat.S_ISLNK(parent_stat.st_mode):
        raise EnvFileError(f"{parent}: must be a real folder, not a symlink")
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise EnvFileError(f"{parent}: is not a folder")
    if parent_stat.st_uid != uid:
        raise EnvFileError(f"{parent}: the folder must be owned by the current user")
    folder_mode = stat.S_IMODE(parent_stat.st_mode)
    if folder_mode & 0o022:
        raise EnvFileError(
            f"{parent}: the folder is writable by other users (mode {folder_mode:04o}); "
            f"run: chmod 700 {parent}"
        )
    warnings: list[str] = []
    if folder_mode & 0o077:
        warnings.append(f"{parent}: folder mode is {folder_mode:04o}; 0700 is recommended")

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError as exc:
        raise EnvFileMissingError(f"{path}: the file does not exist") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise EnvFileError(f"{path}: must be a regular file, not a symlink") from exc
        raise EnvFileError(f"{path}: cannot open the file ({_reason(exc)})") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise EnvFileError(f"{path}: must be a regular file")
        if info.st_uid != uid:
            raise EnvFileError(f"{path}: the file must be owned by the current user")
        file_mode = stat.S_IMODE(info.st_mode)
        if file_mode & 0o077:
            raise EnvFileError(
                f"{path}: group or other users can access this file (mode {file_mode:04o}); "
                f"run: chmod 600 {path}"
            )
        if info.st_size > MAX_BYTES:
            raise EnvFileError(f"{path}: larger than {MAX_BYTES // 1024} KiB")
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise EnvFileError(f"{path}: larger than {MAX_BYTES // 1024} KiB")
    try:
        return data.decode("utf-8-sig"), tuple(warnings)
    except UnicodeDecodeError as exc:
        raise EnvFileError(f"{path}: not valid UTF-8 text") from exc


def load_env_file(
    path: Path, *, allowed: frozenset[str], repo_root: Path | None = None
) -> LoadedEnvFile:
    """Securely read and strictly parse a credential file; reject names outside `allowed`."""
    text, warnings = read_secure_text(path, repo_root=repo_root)
    values = parse_env_text(text, source=str(path))
    unexpected = sorted(set(values) - allowed)
    if unexpected:
        raise EnvFileError(
            f"{path}: unexpected variable name(s): {', '.join(unexpected)} "
            f"(this file may only define: {', '.join(sorted(allowed))})"
        )
    return LoadedEnvFile(path=path, values=values, warnings=warnings)
