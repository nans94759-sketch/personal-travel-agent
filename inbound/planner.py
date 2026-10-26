"""Itinerary planner for inbound travellers.

Pure computation: static city data plus great-circle distance. No network, no
scraping, no LLM. That is deliberate — this module is what makes the paid
deliverable reproducible and auditable.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import corridors
from .data import CITIES, City, get

RAIL_MAX_MINUTES = 330
RAIL_KMH = 250.0
FLIGHT_KMH = 750.0
AIRPORT_BUFFER_MIN = 90


def haversine_km(a: City, b: City) -> float:
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _round10(minutes: float) -> int:
    return int(round(minutes / 10.0) * 10)


def order_cities(keys: Sequence[str]) -> List[City]:
    """Keep the first city fixed (usually the arrival city), then nearest neighbour."""
    cities = [get(k) for k in keys]
    cities = [c for c in cities if c is not None]
    if len(cities) <= 2:
        return cities
    remaining = cities[1:]
    ordered = [cities[0]]
    while remaining:
        last = ordered[-1]
        nxt = min(remaining, key=lambda c: haversine_km(last, c))
        ordered.append(nxt)
        remaining.remove(nxt)
    return ordered


def allocate_days(cities: Sequence[City], total_days: int) -> List[int]:
    """Split the trip across cities, proportional to each city's weight."""
    if not cities:
        return []
    total_days = max(len(cities), int(total_days))
    weight = sum(c.days for c in cities)
    alloc = [max(1, round(total_days * c.days / weight)) for c in cities]

    while sum(alloc) > total_days:
        i = max(range(len(alloc)), key=lambda k: (alloc[k], cities[k].days))
        if alloc[i] <= 1:
            break
        alloc[i] -= 1
    while sum(alloc) < total_days:
        i = max(range(len(alloc)), key=lambda k: cities[k].days)
        alloc[i] += 1
    return alloc


def build_leg(a: City, b: City) -> Dict[str, Any]:
    distance = haversine_km(a, b)
    rail = corridors.rail_minutes(a.key, b.key)

    if rail is not None and rail <= RAIL_MAX_MINUTES:
        return {
            "from": a.en,
            "to": b.en,
            "mode": "train",
            "duration_min": rail,
            "distance_km": round(distance),
            "detail": f"{a.station} → {b.station}",
            "from_code": a.station,
            "to_code": b.station,
            "indicative": False,
        }

    rail_estimate = rail if rail is not None else _round10(distance / RAIL_KMH * 60 + 30)
    if rail_estimate <= RAIL_MAX_MINUTES:
        return {
            "from": a.en,
            "to": b.en,
            "mode": "train",
            "duration_min": rail_estimate,
            "distance_km": round(distance),
            "detail": f"{a.station} → {b.station}",
            "from_code": a.station,
            "to_code": b.station,
            "indicative": rail is None,
        }

    return {
        "from": a.en,
        "to": b.en,
        "mode": "flight",
        "duration_min": _round10(distance / FLIGHT_KMH * 60 + AIRPORT_BUFFER_MIN),
        "distance_km": round(distance),
        "detail": f"{a.airport} → {b.airport}",
        "from_code": a.airport,
        "to_code": b.airport,
        "indicative": True,
    }


def _fmt(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}" if h else f"{m}min"


def build_plan(
    city_keys: Sequence[str],
    start_date: str,
    days: int,
    pace: str = "balanced",
) -> Dict[str, Any]:
    cities = order_cities(city_keys)
    if not cities:
        return {"ok": False, "error": "No recognised destination. Try Beijing, Shanghai, Xi'an, Chengdu."}

    try:
        start = datetime.date.fromisoformat(start_date)
    except ValueError:
        start = datetime.date.today() + datetime.timedelta(days=30)

    requested = max(1, min(int(days or 7), 30))
    if len(cities) > requested:
        cities = cities[:requested]
    days = max(len(cities), requested)

    alloc = allocate_days(cities, days)
    legs = [build_leg(cities[i], cities[i + 1]) for i in range(len(cities) - 1)]

    per_day = {"slow": 2, "balanced": 3, "fast": 4}.get(pace, 3)

    plan_days: List[Dict[str, Any]] = []
    cursor = start
    for idx, city in enumerate(cities):
        for j in range(alloc[idx]):
            items: List[Dict[str, Any]] = []
            date_str = cursor.isoformat()
            first_day = j == 0
            if first_day and idx == 0:
                items.append({
                    "time": "15:00",
                    "title": f"Arrive in {city.en}",
                    "location": f"{city.en} · {city.zh}",
                    "detail": "Check in, get a local SIM or eSIM, set up Alipay. Take it easy.",
                    "cost": 0,
                })
                items.append({
                    "time": "19:00",
                    "title": city.highlights[0],
                    "location": city.en,
                    "detail": "Easy first evening near your hotel.",
                    "cost": 0,
                })
            elif first_day:
                leg = legs[idx - 1]
                items.append({
                    "time": "08:00",
                    "title": f"{leg['mode'].title()} to {city.en}",
                    "location": leg["detail"],
                    "detail": f"About {_fmt(leg['duration_min'])}. Book ahead; bring your passport.",
                    "cost": 0,
                })
                items.append({
                    "time": "15:00",
                    "title": f"Check in, then {city.highlights[0]}",
                    "location": city.en,
                    "detail": "Half-day pace after the transfer.",
                    "cost": 0,
                })
            else:
                picks = city.highlights[: max(1, per_day)]
                slots = ["09:00", "14:00", "19:00", "20:30"][: len(picks)]
                for slot, pick in zip(slots, picks):
                    items.append({
                        "time": slot,
                        "title": pick,
                        "location": city.en,
                        "detail": f"Day {j + 1} in {city.en}.",
                        "cost": 0,
                    })
            plan_days.append({
                "date": date_str,
                "city": city.en,
                "city_zh": city.zh,
                "items": items,
            })
            cursor += datetime.timedelta(days=1)

    title = f"{days}-day China: " + " · ".join(c.en for c in cities)
    return {
        "ok": True,
        "title": title,
        "start": plan_days[0]["date"] if plan_days else start.isoformat(),
        "end": plan_days[-1]["date"] if plan_days else start.isoformat(),
        "days": plan_days,
        "cities": [
            {"key": c.key, "en": c.en, "zh": c.zh, "station": c.station,
             "airport": c.airport, "days": alloc[i], "note": c.note}
            for i, c in enumerate(cities)
        ],
        "legs": legs,
        "total_distance_km": sum(leg["distance_km"] for leg in legs),
        "pace": pace,
    }
