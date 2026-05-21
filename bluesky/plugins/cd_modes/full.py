"""FullDetect - report every pair, identical to upstream StateBased.

Selecting this mode is equivalent to ``CDMETHOD STATEBASED`` but keeps the
user inside BluePlan's CD family, so forms/scripts can flip back to the
baseline without bailing out of the plugin.
"""
from .base import BaseFilterDetect


class FullDetect(BaseFilterDetect):
    """No-op filter: every pair is included."""

    def should_include_pair(self, a: str, b: str) -> bool:
        return True
