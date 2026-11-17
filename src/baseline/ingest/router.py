"""Look at a file's first bytes and pick the right parser.

We sniff the content instead of trusting the file extension, because people rename files.
"""

from __future__ import annotations

from baseline.canonical.models import ParsedFile
from baseline.parsers.base import ParseError, Parser
from baseline.parsers.msp_xml import MspXmlParser
from baseline.parsers.progress_xlsx import ProgressXlsxParser
from baseline.parsers.xer import XerParser


def detect_parser(content: bytes, filename: str = "") -> Parser:
    head = content[:2048]
    if head.startswith(b"PK"):  # .xlsx files are zip archives
        return ProgressXlsxParser()

    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")  # ignore a UTF-8 BOM and whitespace
    if stripped.startswith(b"ERMHDR"):
        return XerParser()
    if b"<Project" in head and (stripped.startswith(b"<?xml") or stripped.startswith(b"<")):
        return MspXmlParser()

    # Fall back to the extension only if the content gave no clue.
    lower = filename.lower()
    if lower.endswith(".xer"):
        return XerParser()
    if lower.endswith(".xml"):
        return MspXmlParser()
    if lower.endswith(".xlsx"):
        return ProgressXlsxParser()

    raise ParseError("UNKNOWN_FORMAT", f"can't tell what kind of file {filename!r} is")


def parse_file(content: bytes, filename: str) -> ParsedFile:
    return detect_parser(content, filename).parse(content, filename)