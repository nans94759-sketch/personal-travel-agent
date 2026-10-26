"""Booking links for the paid product.

All links go through compliance.build_link, so they only exist when an
affiliate account is configured. Without credentials you get None and the UI
shows "booking link not configured" instead of a link that earns nothing.

Base URLs are landing pages, not deep links: Trip.com's deep-link parameter
names must be confirmed once the affiliate account is approved, and inventing
them would produce dead links.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from compliance import build_link

TRIP_COM_TRAINS = "https://www.trip.com/trains/china/"
TRIP_COM_FLIGHTS = "https://www.trip.com/flights/"
TRIP_COM_HOTELS = "https://www.trip.com/hotels/"


def _sub_id(parts: str) -> Dict[str, str]:
    return {"trip_sub1": parts[:50]}


def train_link(from_city: str, to_city: str, date: str) -> Optional[str]:
    return build_link(
        TRIP_COM_TRAINS,
        "trip_com",
        extra=_sub_id(f"rail-{from_city}-{to_city}-{date}"),
    )


def flight_link(from_iata: str, to_iata: str, date: str) -> Optional[str]:
    return build_link(
        TRIP_COM_FLIGHTS,
        "trip_com",
        extra=_sub_id(f"air-{from_iata}-{to_iata}-{date}"),
    )


def hotel_link(city: str, checkin: str, checkout: str) -> Optional[str]:
    return build_link(
        TRIP_COM_HOTELS,
        "trip_com",
        extra=_sub_id(f"htl-{city}-{checkin}-{checkout}"),
    )


def attach_links(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Add booking links to every leg and city. Missing config stays None."""
    for leg in plan.get("legs", []):
        if leg["mode"] == "train":
            leg["booking_url"] = train_link(
                leg["from_code"], leg["to_code"], plan.get("start", "")
            )
        else:
            leg["booking_url"] = flight_link(
                leg["from_code"], leg["to_code"], plan.get("start", "")
            )
    for city in plan.get("cities", []):
        city["hotel_url"] = hotel_link(
            city["en"], plan.get("start", ""), plan.get("end", "")
        )
    plan["affiliate_ready"] = bool(
        any(leg.get("booking_url") for leg in plan.get("legs", []))
    )
    return plan
