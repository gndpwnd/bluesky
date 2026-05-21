"""EgoVsOtherDetect - keep only ego-vs-other pairs, drop ego-vs-ego.

Useful when multiple user aircraft are in the sim and the researcher only
cares how they interact with background traffic, not each other.
"""
from .base import BaseFilterDetect


class EgoVsOtherDetect(BaseFilterDetect):
    def should_include_pair(self, a: str, b: str) -> bool:
        # XOR: exactly one side is ego.
        return self.is_ego(a) != self.is_ego(b)
