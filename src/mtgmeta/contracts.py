"""Source contracts.

Every parser validates the shape of what a source returns before anything is
written. When a site changes its page or JSON, the parser raises
``ContractError`` and the run stops, instead of quietly landing bad rows in
bronze.
"""

from __future__ import annotations


class ContractError(RuntimeError):
    """A source returned something that doesn't match the shape we depend on."""

    def __init__(self, source: str, what: str, detail: str = "") -> None:
        self.source = source
        self.what = what
        self.detail = detail
        message = f"[{source}] contract broken: {what}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


def require(condition: bool, source: str, what: str, detail: str = "") -> None:
    """Raise ContractError unless ``condition`` holds."""
    if not condition:
        raise ContractError(source, what, detail)
