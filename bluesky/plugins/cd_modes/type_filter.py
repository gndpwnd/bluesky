"""TypeFilterDetect - keep pairs where at least one side has a given aircraft type.

Looks up each callsign in ``bs.traf.type`` via ``bs.traf.id2idx()`` at detect
time. Useful when background ADSB traffic has varied types and the researcher
wants "only pairs involving EVTOLs" without tagging callsigns by prefix.

BlueSky imports are lazy so the module can be imported in unit tests without
a BlueSky install; tests can monkey-patch ``_typeof`` on an instance.
"""
from .base import BaseFilterDetect


class TypeFilterDetect(BaseFilterDetect):
    def _typeof(self, callsign: str) -> str:
        """Return the uppercased aircraft type for ``callsign`` or ``""``.

        Unit tests can override this per-instance without importing BlueSky.
        """
        try:
            import bluesky as bs  # type: ignore
        except Exception:
            return ""
        try:
            idx = bs.traf.id2idx(callsign)
        except Exception:
            return ""
        if idx is None or idx < 0:
            return ""
        try:
            return str(bs.traf.type[idx]).upper()
        except Exception:
            return ""

    def should_include_pair(self, a: str, b: str) -> bool:
        if not self.actype:
            return False
        target = self.actype.upper()
        return self._typeof(a) == target or self._typeof(b) == target
