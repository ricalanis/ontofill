"""Content-addressed bronze and contract-compatible gold storage."""

from ontofill.lake.storage import FileLake, S3Lake, lake_for_case

__all__ = ["FileLake", "S3Lake", "lake_for_case"]
