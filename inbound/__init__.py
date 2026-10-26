"""Inbound-traveller product line (commercial branch).

English-first, USD, no scraping. The planner is pure computation over static
city data; booking links only appear when affiliate credentials exist.
"""

from .data import CITIES, City, get, popular, resolve
from .links import attach_links, flight_link, hotel_link, train_link
from .planner import build_plan
from .render import render

__all__ = [
    "CITIES",
    "City",
    "attach_links",
    "build_plan",
    "flight_link",
    "get",
    "hotel_link",
    "popular",
    "render",
    "resolve",
    "train_link",
]
