"""PairFilterDetect - keep only explicit unordered (a, b) pairs from a config list.

Config format (stack command)::

    CDCONFIG pair_list VTOL1:KLM101,VTOL1:N12345

Internally stored as a ``frozenset`` of ``frozenset`` so direction doesn't
matter - ``(A, B)`` and ``(B, A)`` are the same pair.
"""
from .base import BaseFilterDetect


class PairFilterDetect(BaseFilterDetect):
    def should_include_pair(self, a: str, b: str) -> bool:
        if not self.pair_list:
            return False
        return frozenset((a, b)) in self.pair_list
