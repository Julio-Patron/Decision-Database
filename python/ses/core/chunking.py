"""
Chunking module for SES document ingestion.

Splits large text into overlapping chunks suitable for embedding models
with limited context windows (e.g. 256-512 tokens). Uses a recursive
strategy that tries to split on natural boundaries first.
"""

from typing import List


# Ordered from most to least desirable split points
_SEPARATORS = ["\n\n", "\n", ". ", " "]


def chunk_text(
    text: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> List[str]:
    """
    Split *text* into overlapping chunks of roughly *chunk_size* characters.

    The algorithm tries to split on paragraph breaks first (``\\n\\n``), then
    line breaks, sentence endings, and finally spaces.  Each chunk shares
    *chunk_overlap* trailing characters with the next chunk so that context
    is not lost at boundaries.

    Parameters
    ----------
    text : str
        The full document text.
    chunk_size : int
        Target maximum length (in characters) for each chunk.
    chunk_overlap : int
        Number of characters to repeat between consecutive chunks.

    Returns
    -------
    list[str]
        Non-empty chunks.  If the input text is shorter than *chunk_size*,
        a single-element list is returned.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    if len(text) <= chunk_size:
        return [text]

    return _recursive_split(text, chunk_size, chunk_overlap, _SEPARATORS)


def _recursive_split(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    separators: List[str],
) -> List[str]:
    """Recursively split *text* using the first usable separator."""

    # Base case: no separators left, hard-split by character count
    if not separators:
        return _merge_splits(list(text), chunk_size, chunk_overlap, "")

    separator = separators[0]
    remaining_separators = separators[1:]

    # Split the text on the current separator
    splits = text.split(separator)

    good_splits: List[str] = []
    current_group: List[str] = []

    for piece in splits:
        candidate = separator.join(current_group + [piece])
        if len(candidate) <= chunk_size:
            current_group.append(piece)
        else:
            # Flush what we have accumulated so far
            if current_group:
                good_splits.append(separator.join(current_group))
            # If this single piece is too large, recurse with finer separators
            if len(piece) > chunk_size:
                good_splits.extend(
                    _recursive_split(piece, chunk_size, chunk_overlap, remaining_separators)
                )
                current_group = []
            else:
                current_group = [piece]

    # Don't forget the last group
    if current_group:
        good_splits.append(separator.join(current_group))

    return _merge_splits(good_splits, chunk_size, chunk_overlap, separator)


def _merge_splits(
    pieces: List[str],
    chunk_size: int,
    chunk_overlap: int,
    join_str: str,
) -> List[str]:
    """Merge small pieces back together respecting *chunk_size* and adding overlap."""

    if not pieces:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for piece in pieces:
        piece_len = len(piece)
        join_cost = len(join_str) if current else 0

        if current_len + join_cost + piece_len > chunk_size and current:
            chunk_text_str = join_str.join(current).strip()
            if chunk_text_str:
                chunks.append(chunk_text_str)

            # Keep tail pieces that fit within the overlap budget
            overlap_len = 0
            overlap_pieces: List[str] = []
            for prev in reversed(current):
                added = len(prev) + (len(join_str) if overlap_pieces else 0)
                if overlap_len + added > chunk_overlap:
                    break
                overlap_pieces.insert(0, prev)
                overlap_len += added

            current = overlap_pieces
            while current and (sum(len(p) for p in current) + len(join_str) * max(0, len(current) - 1) + (len(join_str) if current else 0) + piece_len > chunk_size):
                current.pop(0)
            current_len = sum(len(p) for p in current) + len(join_str) * max(0, len(current) - 1)
            join_cost = len(join_str) if current else 0

        current.append(piece)
        current_len += (join_cost + piece_len)

    # Flush remainder
    remainder = join_str.join(current).strip()
    if remainder:
        chunks.append(remainder)

    return chunks
