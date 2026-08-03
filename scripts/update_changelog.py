#!/usr/bin/env python3
"""Update CHANGELOG.md with GitHub release notes."""

import re
import sys
from datetime import UTC, datetime
from pathlib import Path


def parse_github_release_notes(notes: str) -> dict[str, list[str]]:
    """Parse GitHub auto-generated release notes into categories."""
    categories: dict[str, list[str]] = {
        "Added": [],
        "Changed": [],
        "Fixed": [],
        "Removed": [],
        "Dependencies": [],
    }

    for line in notes.split("\n"):
        line = line.strip()
        if not line.startswith("*"):
            continue

        match = re.match(r"\*\s+(.+?)\s+by\s+@.+?\s+in\s+https://.+/pull/(\d+)$", line)
        if not match:
            continue

        title = match.group(1)
        pr_ref = f"(#{match.group(2)})"
        title_lower = title.lower()

        if title_lower.startswith("bump "):
            categories["Dependencies"].append(f"- {title} {pr_ref}")
        elif any(
            word in title_lower for word in ["add", "new", "implement", "introduce", "create"]
        ):
            categories["Added"].append(f"- {title} {pr_ref}")
        elif any(word in title_lower for word in ["fix", "resolve", "correct", "repair", "patch"]):
            categories["Fixed"].append(f"- {title} {pr_ref}")
        elif any(word in title_lower for word in ["remove", "delete", "drop"]):
            categories["Removed"].append(f"- {title} {pr_ref}")
        else:
            categories["Changed"].append(f"- {title} {pr_ref}")

    return categories


def format_changelog_entry(version: str, date: str, categories: dict[str, list[str]]) -> str:
    """Format a changelog entry in Keep a Changelog format."""
    lines = [f"## [{version}] - {date}", ""]

    for category in ["Added", "Changed", "Fixed", "Removed", "Dependencies"]:
        if categories[category]:
            lines.append(f"### {category}")
            lines.append("")
            lines.extend(categories[category])
            lines.append("")

    return "\n".join(lines)


def update_changelog(
    changelog_path: Path, version: str, release_notes: str, date: str | None = None
) -> None:
    """Update CHANGELOG.md with a new release entry."""
    if date is None:
        date = datetime.now(tz=UTC).strftime("%Y-%m-%d")

    categories = parse_github_release_notes(release_notes)
    new_entry = format_changelog_entry(version, date, categories)

    content = changelog_path.read_text()

    match = re.search(r"## \[Unreleased\]", content)
    if not match:
        raise ValueError("Could not find [Unreleased] section in CHANGELOG.md")

    insert_pos = match.end()
    while insert_pos < len(content) and content[insert_pos] in "\n\r":
        insert_pos += 1

    updated_content = content[:insert_pos] + "\n" + new_entry + "\n" + content[insert_pos:]

    links_pattern = r"(\[Unreleased\]: https://github\.com/[^/]+/[^/]+/compare/)v[\d.]+\.\.\.HEAD"
    match = re.search(links_pattern, updated_content)

    if match:
        updated_content = re.sub(links_pattern, rf"\g<1>v{version}...HEAD", updated_content)

        prev_version_pattern = r"## \[(\d+\.\d+\.\d+)\]"
        versions = re.findall(prev_version_pattern, updated_content)

        if len(versions) >= 2:
            prev_version = versions[1]
            new_version_link = (
                f"[{version}]: https://github.com/nordstad/zigporter/compare/"
                f"v{prev_version}...v{version}"
            )
            unreleased_link_end = updated_content.find("\n", match.end())
            updated_content = (
                updated_content[:unreleased_link_end]
                + f"\n{new_version_link}"
                + updated_content[unreleased_link_end:]
            )

    changelog_path.write_text(updated_content)


def main() -> None:
    """CLI entry point."""
    if len(sys.argv) not in [3, 4]:
        print("Usage: update_changelog.py VERSION RELEASE_NOTES_FILE [DATE]")
        print("Example: update_changelog.py 0.2.0 release_notes.txt 2026-03-01")
        sys.exit(1)

    version = sys.argv[1]
    notes_file = Path(sys.argv[2])
    date = sys.argv[3] if len(sys.argv) == 4 else None

    if not notes_file.exists():
        print(f"Error: Release notes file not found: {notes_file}", file=sys.stderr)
        sys.exit(1)

    release_notes = notes_file.read_text()
    changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"

    try:
        update_changelog(changelog_path, version, release_notes, date)
        print(f"✓ Updated CHANGELOG.md with version {version}")
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
