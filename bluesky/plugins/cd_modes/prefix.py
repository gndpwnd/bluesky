"""PrefixFilterDetect - keep pairs where at least one side matches a prefix.

Re-uses ``ego_prefix`` as the match target. Set to ``""`` to disable
(no pairs kept). Use a leading ``!`` to negate, e.g. ``!T`` means
"neither side starts with T" - i.e. "no ADSB-vs-ADSB pairs".
"""
from .base import BaseFilterDetect


class PrefixFilterDetect(BaseFilterDetect):
    def should_include_pair(self, a: str, b: str) -> bool:
        if not self.ego_prefix:
            return False
        if self.ego_prefix.startswith("!"):
            p = self.ego_prefix[1:]
            if not p:
                return False
            return not a.startswith(p) and not b.startswith(p)
        return a.startswith(self.ego_prefix) or b.startswith(self.ego_prefix)
