"""Earnings press release (8-K Exhibit 99.1) HTML -> numbered paragraphs.

Each block of text becomes one paragraph with a stable id (P1, P2, ...). Each HTML
table becomes one paragraph whose rows are lines and whose cells are joined by " | ".
Quote verification is an exact substring match against these paragraph texts, after
the light normalization in `norm_text`, which is applied the same way to both sides.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol"}
SKIP_TAGS = {"script", "style", "head", "title"}

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " ", " ": " ", " ": " "})


def norm_text(s: str) -> str:
    """Normalize typography + whitespace only (never words or numbers)."""
    s = html.unescape(s).translate(_QUOTES).replace("​", "")
    return re.sub(r"\s+", " ", s).strip()


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str, str]] = []  # (kind, text)
        self.buf: list[str] = []
        self.skip = 0
        self.table_depth = 0
        self.rows: list[list[str]] = []
        self.cell: list[str] | None = None

    def _flush(self):
        text = norm_text("".join(self.buf))
        self.buf = []
        if text:
            self.blocks.append(("text", text))

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "table":
            if self.table_depth == 0:
                self._flush()
                self.rows = []
            self.table_depth += 1
        elif self.table_depth and tag == "tr":
            self.rows.append([])
        elif self.table_depth and tag in ("td", "th"):
            self.cell = []
        elif not self.table_depth and tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag == "table" and self.table_depth:
            self.table_depth -= 1
            if self.table_depth == 0:
                lines = [" | ".join(c for c in r if c) for r in self.rows]
                lines = [ln for ln in lines if ln.strip(" |")]
                if lines:
                    self.blocks.append(("table", "\n".join(lines)))
                self.rows = []
        elif self.table_depth and tag in ("td", "th") and self.cell is not None:
            if self.rows:
                self.rows[-1].append(norm_text("".join(self.cell)))
            self.cell = None
        elif not self.table_depth and tag in BLOCK_TAGS:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.table_depth:
            if self.cell is not None:
                self.cell.append(data)
        else:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def paragraphs(html_text: str) -> list[dict]:
    """[{id: 'P1', kind: 'text'|'table', text: ...}, ...]"""
    p = _Parser()
    p.feed(html_text)
    p.close()
    blocks: list[tuple[str, str]] = []
    for kind, text in p.blocks:
        # Re-join sentences that the HTML split across blocks ("...guidance to a range of" + "$2.5 to $2.6 billion").
        if (blocks and kind == "text" and blocks[-1][0] == "text" and not re.search(r"[.!?:;\"')\]]$", blocks[-1][1])
                and re.match(r"[a-z$\d(]", text)):
            blocks[-1] = ("text", blocks[-1][1] + " " + text)
        else:
            blocks.append((kind, text))
    out = []
    for kind, text in blocks:
        if kind == "text" and len(text) < 3:
            continue
        out.append({"id": f"P{len(out) + 1}", "kind": kind, "text": text})
    return out
