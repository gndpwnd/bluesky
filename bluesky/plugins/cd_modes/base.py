"""BaseFilterDetect - shared base for BluePlan multi-mode conflict detection.

Subclasses override ``should_include_pair(a, b)``. Geometry math is delegated
to upstream ``StateBased.detect()``; this class post-filters the returned pair
lists and re-indexes the geometry arrays.

BlueSky imports are lazy so the pure-Python predicates can be unit tested
without a BlueSky install. ``StateBased`` is only touched inside ``detect()``.
See ``docs/findings/egodetect-multi-mode-design.md`` for the full design.
"""
from __future__ import annotations

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - numpy always present in BlueSky env
    np = None  # type: ignore

try:
    from bluesky.traffic.asas.statebased import StateBased  # type: ignore
except Exception:  # pragma: no cover - enables unit tests without BlueSky
    class StateBased:  # type: ignore[no-redef]
        """Stub used when BlueSky is not installed (unit test path).

        The real ``StateBased.detect`` is only invoked through
        ``super().detect(...)`` inside ``BaseFilterDetect.detect``; unit tests
        bypass ``detect`` entirely and call ``should_include_pair`` directly.
        """

        def detect(self, *args, **kwargs):  # pragma: no cover
            raise RuntimeError(
                "BaseFilterDetect.detect requires BlueSky's StateBased to be "
                "importable. This stub only exists to allow unit tests of the "
                "pair predicate without BlueSky installed."
            )


class BaseFilterDetect(StateBased):
    """Post-filter StateBased results through a pair predicate.

    Subclasses override :meth:`should_include_pair`. Runtime configuration is
    delivered via class attributes that the ``CDCONFIG`` stack command (see
    ``__init__.py``) mutates on the active instance.
    """

    # ---- Configuration (set by CDCONFIG / init_plugin) ----------------------
    ego_prefix: str = ""
    adsb_prefix: str = "T"
    callsigns: frozenset = frozenset()
    pair_list: frozenset = frozenset()
    actype: str = ""

    # ---- Subclass contract --------------------------------------------------
    def should_include_pair(self, a: str, b: str) -> bool:
        """Return True if pair (a, b) should be reported as a conflict."""
        raise NotImplementedError

    def is_ego(self, callsign: str) -> bool:
        """Default ego definition.

        If ``ego_prefix`` is set, any callsign starting with it is ego.
        Otherwise fall back to: ego = NOT starting with ``adsb_prefix``.
        """
        if self.ego_prefix:
            return callsign.startswith(self.ego_prefix)
        return not callsign.startswith(self.adsb_prefix)

    # ---- StateBased.detect() wrapper ----------------------------------------
    def detect(self, ownship, intruder, rpz, hpz, dtlookahead):  # pragma: no cover
        """Run upstream ``StateBased.detect`` then filter the pair lists.

        Not unit tested (requires BlueSky). Exercised by integration tests
        in ``test_cd_modes_plugin.py``.
        """
        if np is None:
            raise RuntimeError("numpy required for BaseFilterDetect.detect")

        (confpairs, lospairs, inconf, tcpamax,
         qdr, dist, dcpa, tcpa, tLOS) = super().detect(
            ownship, intruder, rpz, hpz, dtlookahead)

        # Short-circuit: nothing to filter.
        if not confpairs and not lospairs:
            return confpairs, lospairs, inconf, tcpamax, qdr, dist, dcpa, tcpa, tLOS

        # Build per-pair keep mask in the same order StateBased emits.
        keep_mask = np.array(
            [self.should_include_pair(a, b) for (a, b) in confpairs],
            dtype=bool,
        )

        filtered_confpairs = [p for p, k in zip(confpairs, keep_mask) if k]
        filtered_lospairs = [
            (a, b) for (a, b) in lospairs if self.should_include_pair(a, b)
        ]

        # Re-index geometry arrays to match the kept conflict pairs.
        if hasattr(qdr, "size") and qdr.size:
            qdr = qdr[keep_mask]
            dist = dist[keep_mask]
            dcpa = dcpa[keep_mask]
            tcpa = tcpa[keep_mask]
            tLOS = tLOS[keep_mask]

        # Rebuild inconf vector: only aircraft still in a kept pair.
        new_inconf = np.zeros(ownship.ntraf, dtype=bool)
        id_to_idx = {cs: i for i, cs in enumerate(ownship.id)}
        for a, b in filtered_confpairs:
            if a in id_to_idx:
                new_inconf[id_to_idx[a]] = True
            if b in id_to_idx:
                new_inconf[id_to_idx[b]] = True

        # tcpamax: zero out aircraft no longer in conflict.
        new_tcpamax = np.where(new_inconf, tcpamax, 0.0)

        return (filtered_confpairs, filtered_lospairs, new_inconf, new_tcpamax,
                qdr, dist, dcpa, tcpa, tLOS)
