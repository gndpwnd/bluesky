"""EgoOnlyDetect - keep any conflict where at least one side is ego.

"Ego" is defined by :meth:`BaseFilterDetect.is_ego`: either matches
``ego_prefix`` when set, or (fallback) is NOT an ADSB relay aircraft
(default prefix ``T``). Ego-vs-ego pairs are kept; use ``EgoVsOtherDetect``
to drop them.
"""
from .base import BaseFilterDetect


class EgoOnlyDetect(BaseFilterDetect):
    def should_include_pair(self, a: str, b: str) -> bool:
        return self.is_ego(a) or self.is_ego(b)
