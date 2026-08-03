"""Fail when a tracked Markdown document references a missing local target."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_LINK_PATTERN = re.compile(
    r"!?\[[^]]*\]\(\s*(?P<target><[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)",
)
REFERENCE_LINK_PATTERN = re.compile(
    r"^\s*\[[^]]+\]:\s*(?P<target><[^>]+>|\S+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class BrokenLink:
    document: Path
    target: str


def _without_fenced_code(markdown: str) -> str:
    retained: list[str] = []
    active_fence: str | None = None
    for line in markdown.splitlines():
        match = FENCE_PATTERN.match(line)
        if match:
            marker = match.group(1)
            if active_fence is None:
                active_fence = marker[0]
            elif marker[0] == active_fence:
                active_fence = None
            continue
        if active_fence is None:
            retained.append(line)
    return "\n".join(retained)


def _targets(markdown: str) -> tuple[str, ...]:
    content = _without_fenced_code(markdown)
    matches = (
        *INLINE_LINK_PATTERN.finditer(content),
        *REFERENCE_LINK_PATTERN.finditer(content),
    )
    return tuple(match.group("target").strip("<>") for match in matches)


def _local_path(document: Path, target: str) -> Path | None:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or (not parsed.path and parsed.fragment):
        return None
    relative_path = unquote(parsed.path)
    if not relative_path:
        return None
    return (document.parent / relative_path).resolve()


def find_broken_links(
    documents: tuple[Path, ...],
    repository_root: Path = ROOT,
) -> tuple[BrokenLink, ...]:
    broken: list[BrokenLink] = []
    resolved_root = repository_root.resolve()
    for document in documents:
        markdown = document.read_text(encoding="utf-8")
        for target in _targets(markdown):
            local_path = _local_path(document, target)
            if local_path is not None and (
                not local_path.is_relative_to(resolved_root) or not local_path.exists()
            ):
                broken.append(BrokenLink(document=document, target=target))
    return tuple(broken)


def tracked_markdown_documents(root: Path = ROOT) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(root / relative_path for relative_path in result.stdout.split("\0") if relative_path)


def main() -> int:
    broken_links = find_broken_links(tracked_markdown_documents())
    for broken_link in broken_links:
        document = broken_link.document.relative_to(ROOT)
        print(f"{document}: missing local target {broken_link.target!r}")
    if broken_links:
        print(f"Found {len(broken_links)} broken local Markdown link(s).")
        return 1
    print("All tracked local Markdown links resolve.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
