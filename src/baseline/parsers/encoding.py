"""Turn raw bytes into text without guessing silently.

P6 usually exports Windows-1252 (cp1252). Some tools export UTF-8, with or without a
byte-order mark (BOM). Anything else is rejected instead of being decoded into garbage.
"""

from baseline.parsers.base import ParseError


def decode_text(content: bytes) -> str:
    if not content:
        raise ParseError("EMPTY_FILE", "file has no content")

    # UTF-16 files start with a BOM. Decode them properly.
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return content.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ParseError("ENCODING_ERROR", f"bad UTF-16 content: {exc}") from exc

    # A NUL byte in a "text" file means it's binary or UTF-16 without a BOM.
    if b"\x00" in content:
        raise ParseError("ENCODING_ERROR", "file contains NUL bytes; not a text export")

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise ParseError("ENCODING_ERROR", "file is neither valid UTF-8 nor Windows-1252")