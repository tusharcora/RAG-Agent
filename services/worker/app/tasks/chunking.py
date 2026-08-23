import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def _approx_tokens(text: str) -> int:
    """Word-count heuristic, not an exact tokenizer. Good enough to keep
    chunks reasonably sized — Voyage's per-input context limit is generous
    (~32K tokens), so exact counts aren't needed for correctness."""
    return int(len(text.split()) * 1.3)


def chunk_text(text: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[dict]:
    """Paragraph-aware greedy packing with a small overlap between consecutive
    chunks. Returns [{"content": str, "heading_path": list[str]}, ...] —
    heading_path tracks markdown '#'-style headings (present in Notion's
    flattened content; always empty for plain text like Jira descriptions
    and comments, which contain no markdown headings).
    """
    paragraphs = _split_paragraphs(text)
    chunks: list[dict] = []
    current: list[str] = []
    current_tokens = 0
    heading_path: list[str] = []

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append({"content": "\n\n".join(current), "heading_path": list(heading_path)})
        current = []
        current_tokens = 0

    for paragraph in paragraphs:
        heading_match = _HEADING_RE.match(paragraph)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_path = heading_path[: level - 1] + [title]

        paragraph_tokens = _approx_tokens(paragraph)
        if current and current_tokens + paragraph_tokens > max_tokens:
            flush()
            # carry the last paragraph forward as overlap context, if it's small enough
            if chunks:
                overlap_para = chunks[-1]["content"].split("\n\n")[-1]
                if _approx_tokens(overlap_para) <= overlap_tokens:
                    current = [overlap_para]
                    current_tokens = _approx_tokens(overlap_para)

        current.append(paragraph)
        current_tokens += paragraph_tokens

    flush()
    return [c for c in chunks if c["content"]]
