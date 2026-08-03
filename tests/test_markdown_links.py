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
