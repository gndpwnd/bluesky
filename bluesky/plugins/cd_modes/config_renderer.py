"""Render a ``ConflictDetectionConfig`` to BlueSky stack commands.

Kept in the plugin package (NOT in ``scenarios_shared.py``) so that
``scenarios_shared.py`` stays declarative and the stack-command vocabulary
lives with the plugin that implements it.

Accepts either a Pydantic ``ConflictDetectionConfig`` instance or a plain
``dict`` matching its shape, so callers can invoke this from ``app/routes``
without importing the plugin package (which would pull in BlueSky on import
in some environments).

Example::

    from bluesky_extensions.plugins.cd_modes.config_renderer import (
        render_cd_config,
    )
    cmds = render_cd_config({"mode": "ego_only", "ego_prefix": "USER"})
    # -> ["CDMETHOD EGOONLYDETECT", "CDCONFIG ego_prefix USER"]
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping


# Mapping from the Pydantic ``mode`` literal to the BlueSky class name
# registered via subclass discovery in this package.
_MODE_TO_CLASS: dict[str, str] = {
    "full": "FULLDETECT",
    "ego_only": "EGOONLYDETECT",
    "ego_vs_other": "EGOVSOTHERDETECT",
    "callsign": "CALLSIGNFILTERDETECT",
    "prefix": "PREFIXFILTERDETECT",
    "type": "TYPEFILTERDETECT",
    "pair": "PAIRFILTERDETECT",
    "vtol_vs_adsb": "VTOLVSADSBDETECT",
}


def _as_dict(cfg: Any) -> Mapping[str, Any]:
    """Coerce a Pydantic model or dict-like into a plain mapping."""
    if cfg is None:
        return {}
    if isinstance(cfg, Mapping):
        return cfg
    # Pydantic v2
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump()
    # Pydantic v1
    if hasattr(cfg, "dict"):
        return cfg.dict()
    raise TypeError(
        f"render_cd_config: unsupported cfg type {type(cfg).__name__}; "
        "expected dict or ConflictDetectionConfig"
    )


def _clean_pair(pair: Any) -> tuple[str, str] | None:
    """Normalize a pair entry to a ``(str, str)`` tuple or ``None`` if invalid."""
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        a, b = str(pair[0]).strip(), str(pair[1]).strip()
        if a and b:
            return a, b
    return None


def render_cd_config(cfg: Any) -> list[str]:
    """Return a list of BlueSky stack commands for the given CD config.

    The first command is always ``CDMETHOD <CLASS>``; any subsequent
    ``CDCONFIG ...`` commands are only emitted for fields that are non-default
    and relevant to the selected mode. Unknown modes fall through to ``FULL``.
    """
    data = _as_dict(cfg)
    mode = str(data.get("mode") or "full").lower()
    if mode not in _MODE_TO_CLASS:
        mode = "full"

    out: list[str] = [f"CDMETHOD {_MODE_TO_CLASS[mode]}"]

    ego_prefix = str(data.get("ego_prefix") or "").strip()
    adsb_prefix = str(data.get("adsb_prefix") or "").strip()
    callsigns: Iterable[Any] = data.get("callsigns") or []
    pair_list: Iterable[Any] = data.get("pair_list") or []
    actype = str(data.get("actype") or "").strip()
    vtol_types: Iterable[Any] = data.get("vtol_types") or []

    # ego_prefix is meaningful for ego_only, ego_vs_other, and prefix modes.
    if ego_prefix:
        out.append(f"CDCONFIG ego_prefix {ego_prefix}")

    # adsb_prefix only emitted if the user overrode the default.
    if adsb_prefix and adsb_prefix != "T":
        out.append(f"CDCONFIG adsb_prefix {adsb_prefix}")

    # callsigns: comma-separated, uppercased token list (dedup preserving order)
    cs_clean: list[str] = []
    seen: set[str] = set()
    for cs in callsigns:
        token = str(cs).strip().upper()
        if token and token not in seen:
            seen.add(token)
            cs_clean.append(token)
    if cs_clean:
        out.append(f"CDCONFIG callsigns {','.join(cs_clean)}")

    # pair_list: rendered as a,b,c,d style colon-joined pairs
    pairs_clean: list[str] = []
    for pair in pair_list:
        cleaned = _clean_pair(pair)
        if cleaned is None:
            continue
        a, b = cleaned[0].upper(), cleaned[1].upper()
        pairs_clean.append(f"{a}:{b}")
    if pairs_clean:
        out.append(f"CDCONFIG pair_list {','.join(pairs_clean)}")

    if actype:
        out.append(f"CDCONFIG actype {actype.upper()}")

    # vtol_types: only meaningful for vtol_vs_adsb mode. Emitted as a
    # comma-separated, deduped, uppercased token list.
    if mode == "vtol_vs_adsb":
        vt_clean: list[str] = []
        vt_seen: set[str] = set()
        for t in vtol_types:
            token = str(t).strip().upper()
            if token and token not in vt_seen:
                vt_seen.add(token)
                vt_clean.append(token)
        if vt_clean:
            out.append(f"CDCONFIG vtol_types {','.join(vt_clean)}")

    return out
