"""Errors shared by the tool layer and the operating-system layer."""

from __future__ import annotations


class ToolError(Exception):
    """A tool failed or refused. The message is safe to show the user and the model."""
