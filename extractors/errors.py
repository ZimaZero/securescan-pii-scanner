"""Shared extraction failure contract."""


class ExtractionError(Exception):
    """An extractor could not read its input at all."""

    @classmethod
    def from_exception(cls, exc: Exception) -> "ExtractionError":
        message = " ".join(str(exc).split()) or "no details"
        return cls(f"{type(exc).__name__}: {message}")
