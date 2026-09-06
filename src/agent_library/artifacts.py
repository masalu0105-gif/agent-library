"""Version-bound visual assets. JSON metadata stays separate from binary objects."""
import hashlib
import html
import re


ASSET_PATH = re.compile(r"assets/([a-f0-9]{64})\.(png|jpg|gif|webp|tiff|bmp|json|html|xml|pdf|bin)")
MAX_ASSET_BYTES = 128 * 1024 * 1024


def add_asset(result, data, extension, media_type, role):
    sha = hashlib.sha256(data).hexdigest()
    name = f"assets/{sha}.{extension}"
    if not ASSET_PATH.fullmatch(name):
        raise ValueError("Invalid asset type")
    blobs = result.setdefault("_asset_bytes", {})
    if sum(len(x) for x in blobs.values()) + (0 if name in blobs else len(data)) > MAX_ASSET_BYTES:
        raise ValueError("Extraction assets exceed limit")
    blobs[name] = data
    result.setdefault("assets", {})[name] = {
        "sha256": sha, "bytes": len(data), "media_type": media_type, "role": role,
    }
    return name


def asset_manifest(extraction):
    """Validate addresses before any read, export or publication of referenced bytes."""
    assets = extraction.get("assets", {})
    if not isinstance(assets, dict):
        raise ValueError("Invalid asset manifest")
    size = 0
    for name, item in assets.items():
        match = ASSET_PATH.fullmatch(name)
        if not match or not isinstance(item, dict) or item.get("sha256") != match[1]:
            raise ValueError("Invalid asset address")
        if type(item.get("bytes")) is not int or item["bytes"] < 0:
            raise ValueError("Invalid asset size")
        if not isinstance(item.get("media_type"), str) or not isinstance(item.get("role"), str):
            raise ValueError("Invalid asset metadata")
        size += item["bytes"]
    if size > MAX_ASSET_BYTES:
        raise ValueError("Extraction assets exceed limit")
    for page in extraction.get("pages", []):
        refs = ([page["preview"]] if page.get("preview") else []) + page.get("images", [])
        refs += [table["asset"] for table in page.get("tables", [])]
        if any(ref not in assets for ref in refs):
            raise ValueError("Unresolved page asset")
    for key in ["raw_asset", "rendered_asset"]:
        if extraction.get(key) and extraction[key] not in assets:
            raise ValueError("Unresolved parser output")
    if any(item["asset"] not in assets for item in extraction.get("embedded_media", [])):
        raise ValueError("Unresolved embedded media")
    return assets


def table_html(cells, row_count, col_count):
    """Keep spans; escape source strings so generated tables contain no active HTML."""
    covered, starts = set(), {}
    if not (type(row_count) is int and type(col_count) is int and
            0 <= row_count <= 10000 and 0 <= col_count <= 1000 and row_count * col_count <= 100000):
        raise ValueError("Invalid table dimensions")
    for cell in cells:
        r, c, rs, cs = (cell[key] for key in ("row", "col", "rowspan", "colspan"))
        if (any(type(v) is not int for v in [r, c, rs, cs]) or
                min(r, c) < 0 or min(rs, cs) < 1 or r + rs > row_count or c + cs > col_count):
            raise ValueError("Invalid table span")
        area = {(a, b) for a in range(r, r + rs) for b in range(c, c + cs)}
        if covered & area:
            raise ValueError("Overlapping table cells")
        covered |= area
        starts[r, c] = cell
    rows = []
    for r in range(row_count):
        values = []
        for c in range(col_count):
            cell = starts.get((r, c))
            if cell:
                tag = "th" if cell.get("header") else "td"
                values.append(f'<{tag} rowspan="{cell["rowspan"]}" colspan="{cell["colspan"]}">'
                              + html.escape(str(cell["text"])).replace("\n", "<br>") + f"</{tag}>")
            elif (r, c) not in covered:
                values.append("<td></td>")
        rows.append("<tr>" + "".join(values) + "</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def render_page(page):
    text = page.get("markdown", page["text"])
    if page.get("preview"):
        text += f"\n\n[Original page preview]({page['preview']})\n"
    return text
