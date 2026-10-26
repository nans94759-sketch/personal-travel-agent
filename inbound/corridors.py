"""Indicative high-speed-rail timings between major city pairs.

These are representative durations for the fastest sensible connection, not a
live timetable. The UI must label them as indicative and point users to the
booking link for confirmed times. Live seat data requires an affiliate feed,
which this branch does not have yet.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# minutes, keyed by the two city keys sorted alphabetically
TYPICAL_RAIL_MINUTES: Dict[Tuple[str, str], int] = {
    ("beijing", "harbin"): 330,
    ("beijing", "hangzhou"): 285,
    ("beijing", "luoyang"): 240,
    ("beijing", "nanjing"): 230,
    ("beijing", "pingyao"): 195,
    ("beijing", "qingdao"): 285,
    ("beijing", "shanghai"): 270,
    ("beijing", "xian"): 275,
    ("changsha", "guangzhou"): 150,
    ("changsha", "zhangjiajie"): 150,
    ("chengdu", "chongqing"): 75,
    ("chengdu", "kunming"): 300,
    ("chengdu", "xian"): 210,
    ("dali", "kunming"): 120,
    ("dali", "lijiang"): 120,
    ("guangzhou", "guilin"): 150,
    ("guangzhou", "shenzhen"): 35,
    ("guangzhou", "xiamen"): 240,
    ("guilin", "yangshuo"): 25,
    ("hangzhou", "huangshan"): 95,
    ("hangzhou", "nanjing"): 90,
    ("hangzhou", "suzhou"): 90,
    ("huangshan", "nanjing"): 135,
    ("lijiang", "kunming"): 210,
    ("luoyang", "pingyao"): 165,
    ("luoyang", "xian"): 90,
    ("nanjing", "shanghai"): 80,
    ("shanghai", "suzhou"): 30,
    ("shanghai", "hangzhou"): 60,
    ("shanghai", "huangshan"): 165,
    ("shanghai", "xiamen"): 300,
    ("shenzhen", "xiamen"): 210,
}


def _normalise() -> Dict[Tuple[str, str], int]:
    """Order-insensitive index, with a collision check at import time."""
    index: Dict[Tuple[str, str], int] = {}
    for pair, minutes in TYPICAL_RAIL_MINUTES.items():
        key = tuple(sorted(pair))
        if key in index and index[key] != minutes:
            raise ValueError(f"Conflicting rail timings for {key}: {index[key]} vs {minutes}")
        index[key] = minutes
    return index


_INDEX = _normalise()


def rail_minutes(a: str, b: str) -> Optional[int]:
    return _INDEX.get(tuple(sorted((a, b))))


def is_rail_corridor(a: str, b: str) -> bool:
    return rail_minutes(a, b) is not None
