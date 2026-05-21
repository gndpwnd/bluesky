"""CallsignFilterDetect - keep pairs involving a configured callsign set.

Equivalent to ADCL's hardcoded ``VtolDetect(callsign="VTOL1")`` but supports
a list of callsigns. Populated via::

    CDCONFIG callsigns VTOL1,KLM101,N12345
"""
from .base import BaseFilterDetect


class CallsignFilterDetect(BaseFilterDetect):
    def should_include_pair(self, a: str, b: str) -> bool:
        if not self.callsigns:
            return False
        return a in self.callsigns or b in self.callsigns
