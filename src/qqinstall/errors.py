"""The one exception qqinstall reports as is (exit 2); manifest re-exports it."""
from __future__ import annotations


class InstallerError(Exception):
    """A failure the caller should report as is."""
