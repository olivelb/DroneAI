"""Fail when a tracked Markdown document references a missing local target."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})")
MERMAID_FENCE_PATTERN = re.compile(r"^\s*(`{3,}|~{3,})mermaid\s*$", re.IGNORECASE)
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


@dataclass(frozen=True)
class MermaidIssue:
    document: Path
    line: int
    message: str


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



def _mermaid_blocks(markdown: str):
    marker: str | None = None
    start_line = 0
    lines: list[tuple[int, str]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        if marker is None:
            match = MERMAID_FENCE_PATTERN.match(line)
            if match:
                marker = match.group(1)
                start_line = line_number
                lines = []
            continue
        stripped = line.strip()
        if stripped.startswith(marker[0] * len(marker)):
            yield start_line, tuple(lines), True
            marker = None
            continue
        lines.append((line_number, line))
    if marker is not None:
        yield start_line, tuple(lines), False


def find_mermaid_issues(documents: tuple[Path, ...]) -> tuple[MermaidIssue, ...]:
    issues: list[MermaidIssue] = []
    sequence_openers = {"alt", "opt", "loop", "par", "critical", "break", "rect"}
    for document in documents:
        markdown = document.read_text(encoding="utf-8")
        for start_line, lines, closed in _mermaid_blocks(markdown):
            if not closed:
                issues.append(MermaidIssue(document, start_line, "unclosed Mermaid fence"))
                continue
            nonempty = [(line_number, line.strip()) for line_number, line in lines if line.strip()]
            if not nonempty or nonempty[0][1] != "sequenceDiagram":
                continue
            stack: list[tuple[str, int]] = []
            for line_number, line in nonempty[1:]:
                keyword = line.split(maxsplit=1)[0]
                if keyword in sequence_openers:
                    stack.append((keyword, line_number))
                elif keyword == "end":
                    if stack:
                        stack.pop()
                    else:
                        issues.append(
                            MermaidIssue(document, line_number, "unexpected sequenceDiagram end")
                        )
                if "->" in line and ":" in line and ";" in line.split(":", 1)[1]:
                    issues.append(
                        MermaidIssue(
                            document,
                            line_number,
                            "sequence message contains a raw semicolon rejected by GitHub Mermaid",
                        )
                    )
            for keyword, line_number in stack:
                issues.append(
                    MermaidIssue(
                        document,
                        line_number,
                        f"unclosed sequenceDiagram {keyword} block",
                    )
                )
    return tuple(issues)

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
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    # Unstaged deletions still appear in the index. Check the working tree;
    # links from retained documents to deleted targets remain errors.
    return tuple(
        root / relative_path
        for relative_path in result.stdout.split("\0")
        if relative_path and (root / relative_path).is_file()
    )


def main() -> int:
    documents = tracked_markdown_documents()
    broken_links = find_broken_links(documents)
    mermaid_issues = find_mermaid_issues(documents)
    for broken_link in broken_links:
        document = broken_link.document.relative_to(ROOT)
        print(f"{document}: missing local target {broken_link.target!r}")
    for issue in mermaid_issues:
        document = issue.document.relative_to(ROOT)
        print(f"{document}:{issue.line}: {issue.message}")
    if broken_links or mermaid_issues:
        print(
            f"Found {len(broken_links)} broken local Markdown link(s) and "
            f"{len(mermaid_issues)} Mermaid issue(s)."
        )
        return 1
    print("All tracked local Markdown links and Mermaid blocks pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
