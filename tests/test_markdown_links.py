from tools import check_markdown_links


def test_markdown_link_checker_accepts_local_remote_anchor_and_encoded_targets(tmp_path):
    target = tmp_path / "target file.md"
    target.write_text("target", encoding="utf-8")
    document = tmp_path / "README.md"
    document.write_text(
        "\n".join(
            (
                "[local](<target file.md>)",
                "[encoded](target%20file.md#section)",
                "[remote](https://github.com/olivelb/DroneAI)",
                "[anchor](#section)",
                "[reference]: target%20file.md",
            )
        ),
        encoding="utf-8",
    )

    assert check_markdown_links.find_broken_links((document,), tmp_path) == ()


def test_markdown_link_checker_reports_missing_targets_outside_fenced_code(tmp_path):
    document = tmp_path / "README.md"
    document.write_text(
        "[missing](missing.md)\n[outside](../outside.md)\n"
        "```markdown\n[example](ignored.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path.parent / "outside.md").write_text("outside", encoding="utf-8")

    broken_links = check_markdown_links.find_broken_links((document,), tmp_path)

    assert len(broken_links) == 2
    assert broken_links[0].document == document
    assert broken_links[0].target == "missing.md"
    assert broken_links[1].target == "../outside.md"


def test_markdown_inventory_handles_unstaged_deletion_without_hiding_broken_links(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    retained = tmp_path / "README.md"
    retired = tmp_path / "retired.md"
    retained.write_text("[old](retired.md)\n", encoding="utf-8")
    retired.write_text("obsolete", encoding="utf-8")
    subprocess.run(["git", "add", "README.md", "retired.md"], cwd=tmp_path, check=True)
    retired.unlink()

    documents = check_markdown_links.tracked_markdown_documents(tmp_path)
    assert documents == (retained,)
    assert check_markdown_links.find_broken_links(documents, tmp_path) == (
        check_markdown_links.BrokenLink(document=retained, target="retired.md"),
    )



def test_mermaid_checker_rejects_raw_sequence_semicolon_and_unclosed_block(tmp_path):
    document = tmp_path / "diagram.md"
    document.write_text(
        """```mermaid
sequenceDiagram
    participant API
    opt selected
        API->>API: persist; release
```
""",
        encoding="utf-8",
    )

    issues = check_markdown_links.find_mermaid_issues((document,))

    assert [issue.line for issue in issues] == [5, 4]
    assert "semicolon" in issues[0].message
    assert "unclosed" in issues[1].message


def test_mermaid_checker_accepts_balanced_sequence_and_non_sequence_semicolon(tmp_path):
    document = tmp_path / "diagram.md"
    document.write_text(
        """```mermaid
sequenceDiagram
    opt selected
        API->>DB: persist, release
    end
```

```mermaid
flowchart LR
    A["semicolon; allowed"] --> B
```
""",
        encoding="utf-8",
    )

    assert check_markdown_links.find_mermaid_issues((document,)) == ()


def test_mermaid_checker_rejects_unclosed_fence(tmp_path):
    document = tmp_path / "diagram.md"
    document.write_text(
        """```mermaid
flowchart LR
A --> B
""",
        encoding="utf-8",
    )

    issues = check_markdown_links.find_mermaid_issues((document,))

    assert len(issues) == 1
    assert issues[0].line == 1
    assert issues[0].message == "unclosed Mermaid fence"
