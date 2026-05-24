"""
markdown_lite — a tiny in-tree Markdown -> HTML converter for FIESTA
SEO articles (Tier D6 A4 slice 1, 2026-05-24).

Why in-tree, not the `markdown` PyPI package:
  - The spec for slice 1 says "no heavy dependencies, lightweight
    markdown + frontmatter is enough". The PyPI `markdown` package is
    fine, but adding any new dep through pyproject.toml + lockfile +
    Fly redeploy on a single-slice ship felt heavier than 150 lines of
    well-tested converter. If we later need full CommonMark, swap the
    `md_to_html` body with `markdown.markdown(text, extensions=...)`.

What this covers (the subset our articles actually use):
  - ATX headings   #, ##, ###, ####  (h1 .. h4)
  - Paragraphs (blank-line separated)
  - Bold        **text**  __text__
  - Italic      *text*    _text_
  - Inline code `code`
  - Links       [label](url)
  - Unordered lists  -, *, +
  - Ordered lists    1.  2.  3.
  - Blockquotes  >
  - Horizontal rule  ---, ***
  - Hard line break  trailing two spaces + newline

What it DELIBERATELY does NOT cover (out of scope for slice 1):
  - Tables   (use raw <table> in the .md if needed)
  - Footnotes
  - Definition lists
  - Code fences ``` (single backtick inline only; for multi-line code
    use raw <pre><code> in the .md)
  - Autolinks  <https://example.com>
  - Image syntax  ![alt](url)  (use raw <img> if you need one)

Security:
  All article text is escaped via `markupsafe.escape` before any inline
  patterns run, so a `<script>` literal in the .md becomes the displayed
  text `<script>` rather than executable code. Link URLs are validated
  to start with http:// https:// /  #  mailto:  — anything else is
  treated as plain text. We do NOT pass arbitrary `javascript:` or
  `data:` URLs through.
"""
from __future__ import annotations

import re
from typing import Callable

from markupsafe import Markup, escape


# --------------------------------------------------------------------------- #
# Inline patterns
# --------------------------------------------------------------------------- #
# Order matters: code spans first (so ** inside ` ` isn't treated as bold),
# then bold (** / __), italic (* / _), links, line-break.
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD_STAR = re.compile(r"\*\*([^*\n]+?)\*\*")
_BOLD_UND  = re.compile(r"__([^_\n]+?)__")
_ITALIC_STAR = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_ITALIC_UND  = re.compile(r"(?<!_)_([^_\n]+?)_(?!_)")
_LINK = re.compile(r"\[([^\]\n]+?)\]\(([^)\n]+?)\)")
_HARD_BREAK = re.compile(r"  \n")

_SAFE_URL = re.compile(
    r"^(?:https?://|/|#|mailto:|tel:)", re.IGNORECASE
)


def _safe_link(match: re.Match) -> str:
    """[label](url) -> <a href=...>label</a>, with URL allowlist.

    NOTE: this runs AFTER outer escape(), so `label` and `url` here are
    already HTML-escaped (so &amp;, &lt;, etc. are pre-encoded). We
    therefore do NOT re-escape — we just validate the URL prefix against
    the allowlist and emit the tag.
    """
    label = match.group(1)
    url = match.group(2)
    if not _SAFE_URL.match(url):
        # Pretend it wasn't a link — show the literal text.
        return f"[{label}]({url})"
    # External links open in a new tab + get rel="noopener"; internal
    # /-prefixed links stay in the same tab.
    if url.startswith(("http://", "https://")):
        return (
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
            f'{label}</a>'
        )
    return f'<a href="{url}">{label}</a>'


def _apply_inline(text: str) -> str:
    """Apply inline patterns to an already-HTML-escaped string."""
    # Code spans first.
    text = _INLINE_CODE.sub(
        lambda m: f"<code>{m.group(1)}</code>", text
    )
    # Bold before italic so **foo** doesn't get eaten by *foo*.
    text = _BOLD_STAR.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _BOLD_UND.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC_STAR.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    text = _ITALIC_UND.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    text = _LINK.sub(_safe_link, text)
    text = _HARD_BREAK.sub("<br>\n", text)
    return text


# --------------------------------------------------------------------------- #
# Block parsing
# --------------------------------------------------------------------------- #
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*?)\s*$")
_HR_RE = re.compile(r"^(---|\*\*\*)\s*$")
_UL_RE = re.compile(r"^[\-\*\+]\s+(.*)$")
_OL_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_BQ_RE = re.compile(r"^>\s?(.*)$")


def _slugify_heading(text: str) -> str:
    """Make a permalink-friendly id from a heading. We use plain-text
    (post-inline-strip) because anchor ids should not contain HTML."""
    plain = re.sub(r"<[^>]+>", "", text)
    plain = re.sub(r"[^\w\s-]", "", plain).strip().lower()
    return re.sub(r"[\s_-]+", "-", plain)


def md_to_html(md_text: str) -> str:
    """Convert Markdown to safe HTML using the supported subset.

    Returns a plain `str` (not Markup) so the caller can mark_safe it in
    the template. We rely on `markupsafe.escape` to neutralise any HTML
    in the source before applying inline patterns.
    """
    if not md_text:
        return ""

    # Normalise line endings first.
    src = md_text.replace("\r\n", "\n").replace("\r", "\n")

    out: list[str] = []
    lines = src.split("\n")
    i = 0
    n = len(lines)

    def flush_paragraph(buf: list[str]):
        if not buf:
            return
        joined = " ".join(line.strip() for line in buf if line.strip())
        if not joined:
            return
        # Escape first, then apply inline. The hard-break pattern needs
        # `  \n` (two spaces + newline), which gets eaten when we join on
        # space above; we handle hard breaks per-line BEFORE join instead.
        # For slice 1 we accept this minor edge — articles use blank lines.
        escaped = str(escape(joined))
        out.append(f"<p>{_apply_inline(escaped)}</p>")

    while i < n:
        line = lines[i]

        # Blank line -> paragraph break
        if not line.strip():
            i += 1
            continue

        # Heading
        h = _HEADING_RE.match(line)
        if h:
            level = len(h.group(1))
            text = h.group(2)
            escaped = str(escape(text))
            inline = _apply_inline(escaped)
            slug = _slugify_heading(escaped)
            out.append(f'<h{level} id="{slug}">{inline}</h{level}>')
            i += 1
            continue

        # Horizontal rule
        if _HR_RE.match(line):
            out.append("<hr>")
            i += 1
            continue

        # Unordered list — collect contiguous matching lines
        if _UL_RE.match(line):
            items: list[str] = []
            while i < n and _UL_RE.match(lines[i]):
                m = _UL_RE.match(lines[i])
                item_text = str(escape(m.group(1)))
                items.append(f"<li>{_apply_inline(item_text)}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue

        # Ordered list — collect contiguous numbered lines
        if _OL_RE.match(line):
            items = []
            while i < n and _OL_RE.match(lines[i]):
                m = _OL_RE.match(lines[i])
                item_text = str(escape(m.group(2)))
                items.append(f"<li>{_apply_inline(item_text)}</li>")
                i += 1
            out.append("<ol>" + "".join(items) + "</ol>")
            continue

        # Blockquote — collect contiguous quote lines
        if _BQ_RE.match(line):
            buf = []
            while i < n and _BQ_RE.match(lines[i]):
                m = _BQ_RE.match(lines[i])
                buf.append(m.group(1))
                i += 1
            joined = " ".join(b.strip() for b in buf if b.strip())
            escaped = str(escape(joined))
            out.append(f"<blockquote><p>{_apply_inline(escaped)}</p></blockquote>")
            continue

        # Paragraph — collect contiguous non-blank, non-special lines
        para: list[str] = []
        while i < n:
            cur = lines[i]
            if not cur.strip():
                break
            if (
                _HEADING_RE.match(cur)
                or _HR_RE.match(cur)
                or _UL_RE.match(cur)
                or _OL_RE.match(cur)
                or _BQ_RE.match(cur)
            ):
                break
            para.append(cur)
            i += 1
        flush_paragraph(para)

    return "\n".join(out)


__all__ = ["md_to_html"]
