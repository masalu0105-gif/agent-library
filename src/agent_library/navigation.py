"""Small navigation Markdown files, independently bound to full text and source."""
import json
import math
import posixpath

BRIEF_METHOD = "extractive-navigation-v1"


def markdown_label(text: str) -> str:
    return " ".join(text.split()).replace("[", "\\[").replace("]", "\\]").replace("<", "&lt;").replace(">", "&gt;")


def build_catalog(entries: list[tuple[str, str]], address: str, title: str) -> dict[str, str]:
    """Bound category and document catalogs to 32 entries per Markdown file."""
    cards = {}
    def build(selected, target):
        text = "# " + markdown_label(title) + "\n\n"
        if len(selected) <= 32:
            for label, destination in selected:
                link = posixpath.relpath(destination, posixpath.dirname(target) or ".")
                text += f"- [{markdown_label(label)}]({link})\n"
        else:
            width = math.ceil(len(selected) / 32)
            for index, offset in enumerate(range(0, len(selected), width), 1):
                group = selected[offset:offset + width]
                child = target.removesuffix(".md") + f"-{index}.md"
                link = posixpath.relpath(child, posixpath.dirname(target) or ".")
                text += f"- [Entries {offset + 1}-{offset + len(group)}: {markdown_label(group[0][0])}]({link})\n"
                build(group, child)
        cards[target] = text
    build(entries, address)
    return cards


def build_briefs(pages: list[dict], identity: dict, fulltext_hash: str) -> dict[str, str]:
    """At most eight children per card. No model summaries or invented claims."""
    cards = {}
    def build(selected, address="brief.md"):
        front = {**identity, "fulltext_sha256": fulltext_hash, "brief_method": BRIEF_METHOD}
        text = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()) + "\n---\n\n"
        text += "# Document brief\n\nVerbatim excerpts for navigation; not a complete answer or a verified fact summary.\n\n"
        if not selected:
            text += "No extracted text. Inspect extraction status before using this document.\n"
        elif len(selected) <= 8:
            for page in selected:
                excerpt = markdown_label(page["text"].strip()[:160]) or "[No extracted text]"
                target = posixpath.relpath("full.md", posixpath.dirname(address) or ".")
                text += f"- [Page {page['number']}]({target}#page-{page['number']}): {excerpt}\n"
        else:
            width = math.ceil(len(selected) / 8)
            stem = "" if address == "brief.md" else posixpath.basename(address).removesuffix(".md") + "-"
            for index, offset in enumerate(range(0, len(selected), width), 1):
                group = selected[offset:offset + width]
                child = f"sections/{stem}{index}.md"
                link = posixpath.relpath(child, posixpath.dirname(address) or ".")
                hint = markdown_label(group[0]["text"].strip()[:100])
                text += f"- [Pages {group[0]['number']}-{group[-1]['number']}]({link}): {hint}\n"
                build(group, child)
        cards[address] = text
    build(pages)
    return cards
