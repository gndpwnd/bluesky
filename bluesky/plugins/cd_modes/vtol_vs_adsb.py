"""VtolVsAdsbDetect - keep only VTOL-vs-ADSB pairs.

This is the mode the professor asked for: "count VTOL to ADS-B conflicts ONLY"
(inspired by the ADCL-Group ``VtolDetect`` plugin — see
``docs/adcl-bluesky-reference/06-exhaustive-audit.md``, section
"Per-file categorization" and ``02-vtoldetect-port-plan.md``).

A pair ``(a, b)`` is kept iff **all** of:

1. Exactly one side's callsign starts with ``adsb_prefix`` (default ``"T"``,
   BluePlan's ADSB relay convention) — i.e. the pair is ADSB-vs-non-ADSB.
   This drops ADSB-vs-ADSB and user-vs-user pairs in one shot.
2. The non-ADSB side has an aircraft type present in the configured
   ``vtol_types`` set (default: a small hardcoded VTOL/rotorcraft list).
   An unknown actype (empty string) fails the check — we err on the side of
   *excluding* unclassified aircraft so the log is not polluted.
3. ``a != b`` (self-pair defensive check; upstream ``StateBased`` already
   skips these, but we re-assert because predicate tests may hand us one).

``vtol_types`` is configured with the existing ``CDCONFIG`` stack command via
a new ``vtol_types`` param::

    CDCONFIG vtol_types EH101,B407,B412,VTOL,UAM,EVTOL
    CDCONFIG adsb_prefix T

An empty ``vtol_types`` falls back to the built-in default list so a minimal
``CDMETHOD VTOLVSADSBDETECT`` invocation still "does the right thing" without
additional config.

Unit tests monkey-patch ``_typeof`` on an instance (same pattern as
``TypeFilterDetect``) so the predicate is exercised without BlueSky installed.
"""
from __future__ import annotations

from .base import BaseFilterDetect


# Built-in default VTOL/rotorcraft ICAO type list. Chosen to match the
# professor's ADCL scenarios (EH101, B407, B412) plus generic BluePlan
# "VTOL"/"UAM"/"EVTOL" synthetic types we already use in scenario templates.
# Kept small and hand-curated — any researcher with a different fleet can
# override via ``CDCONFIG vtol_types``.
DEFAULT_VTOL_TYPES: frozenset[str] = frozenset({
    "EH101",  # AgustaWestland AW101 (ADCL AAM scenarios)
    "B407",   # Bell 407 (ADCL AAM scenarios)
    "B412",   # Bell 412 (ADCL AAM scenarios)
    "B429",   # Bell 429
    "EC35",   # Eurocopter EC135
    "EC45",   # Eurocopter EC145
    "H60",    # Sikorsky H-60
    "VTOL",   # BluePlan generic VTOL placeholder
    "UAM",    # BluePlan generic UAM placeholder
    "EVTOL",  # BluePlan generic eVTOL placeholder
})


class VtolVsAdsbDetect(BaseFilterDetect):
    """Keep conflict pairs only when a VTOL aircraft meets an ADSB aircraft."""

    # Runtime-configurable VTOL type allow-set. Populated by ``CDCONFIG
    # vtol_types <csv>``; empty means "use DEFAULT_VTOL_TYPES".
    vtol_types: frozenset = frozenset()

    # ---- Aircraft-type lookup (shared shape with TypeFilterDetect) ----------
    def _typeof(self, callsign: str) -> str:
        """Return the uppercased aircraft type for ``callsign`` or ``""``.

        Unit tests override this per-instance to avoid importing BlueSky.
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

    # ---- Predicate ----------------------------------------------------------
    def _effective_vtol_types(self) -> frozenset:
        return self.vtol_types if self.vtol_types else DEFAULT_VTOL_TYPES

    def should_include_pair(self, a: str, b: str) -> bool:
        if a == b:
            return False

        prefix = self.adsb_prefix or "T"
        a_is_adsb = a.startswith(prefix)
        b_is_adsb = b.startswith(prefix)

        # Require exactly one ADSB side — drops adsb/adsb and user/user.
        if a_is_adsb == b_is_adsb:
            return False

        # Identify the non-ADSB ("user") side and check its type.
        user_cs = b if a_is_adsb else a
        user_type = self._typeof(user_cs).upper()
        if not user_type:
            return False  # unknown type: default-exclude

        return user_type in self._effective_vtol_types()
