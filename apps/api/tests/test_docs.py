"""The documentation cannot drift into claims the repository does not back.

* every relative link and every image in the Markdown files points at something that exists (so a
  screenshot cannot be shown before its file is committed);
* every Mermaid block is a fenced block of a diagram type Mermaid knows, with balanced fences;
* the statements the README makes about itself (no license yet, no screenshots yet) match the
  repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MARKDOWN = sorted(
    {
        REPO_ROOT / "README.md",
        REPO_ROOT / "CLAUDE.md",
        *(REPO_ROOT / "docs").rglob("*.md"),
        *(REPO_ROOT / "infra").rglob("*.md"),
    }
)
LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
FENCE = re.compile(r"^```", re.MULTILINE)
MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
DIAGRAM_TYPES = (
    "flowchart",
    "graph",
    "sequenceDiagram",
    "stateDiagram-v2",
    "erDiagram",
    "classDiagram",
    "gantt",
    "journey",
)


def relative_targets(path: Path) -> list[tuple[str, str]]:
    text = path.read_text()
    # links inside fenced code blocks are examples, not links
    without_code = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    found = []
    for bang, target in LINK.findall(without_code):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue  # an absolute URL, mailto: and so on, or an in-page anchor
        found.append((bang, target.split("#", 1)[0]))
    return found


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_and_images_resolve(path: Path) -> None:
    missing = []
    for _bang, target in relative_targets(path):
        if not target:
            continue
        resolved = (path.parent / unquote(target)).resolve()
        if not resolved.exists():
            missing.append(target)
    assert missing == [], f"{path.relative_to(REPO_ROOT)} links to missing files: {missing}"


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_code_fences_are_balanced_and_mermaid_diagrams_have_a_known_type(path: Path) -> None:
    text = path.read_text()
    assert len(FENCE.findall(text)) % 2 == 0, "an unbalanced ``` fence"
    for block in MERMAID_BLOCK.findall(text):
        first = next(line for line in block.splitlines() if line.strip())
        assert first.strip().startswith(DIAGRAM_TYPES), f"unknown Mermaid diagram type: {first!r}"
        assert block.count("[") == block.count("]"), "unbalanced [ ] in a Mermaid block"
        assert block.count("(") == block.count(")"), "unbalanced ( ) in a Mermaid block"


def test_the_readme_and_architecture_document_contain_diagrams() -> None:
    for name in ("README.md", "docs/architecture.md"):
        assert "```mermaid" in (REPO_ROOT / name).read_text(), name


def test_the_readme_only_shows_screenshots_that_exist() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    images = [t for bang, t in relative_targets(REPO_ROOT / "README.md") if bang == "!"]
    for image in images:
        assert (REPO_ROOT / image).is_file(), image
    shots = list((REPO_ROOT / "docs" / "screenshots").glob("*.png"))
    if not shots:
        # no image files: the README must say so, and must not embed any image
        assert images == []
        assert "No screenshots have been captured yet" in readme
    else:
        assert images, "screenshots exist but the README does not show them"


def test_the_readme_statement_about_the_license_matches_the_repository() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    has_license = any(REPO_ROOT.glob("LICENSE*")) or any(REPO_ROOT.glob("COPYING*"))
    if has_license:
        assert "No license has been chosen yet" not in readme
    else:
        assert "No license has been chosen yet" in readme


def test_the_readme_does_not_describe_the_old_phase_zero_state() -> None:
    readme = (REPO_ROOT / "README.md").read_text()
    for stale in ("Phase 0 (foundation)", "There are no business features yet"):
        assert stale not in readme
