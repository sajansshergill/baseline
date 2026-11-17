"""Shared parser interface and the error every parser raises when a file is unreadable."""

from __future__ import annotations

from abc import ABC, abstractmethod

from baseline.canonical.models import ParsedFile


class ParseError(Exception):
    """Raised when a file can't be read at all. The file gets quarantined with this reason."""

    def __init__(self, reason_code: str, detail: str):
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


class Parser(ABC):
    #: short name stored in upload.source_format
    format_name: str

    @abstractmethod
    def parse(self, content: bytes, filename: str) -> ParsedFile:
        """Turn raw file bytes into a canonical Schedule or ProgressReport."""