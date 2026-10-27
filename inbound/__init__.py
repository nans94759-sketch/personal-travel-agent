"""Inbound-traveller product line (commercial branch).

English and Chinese, USD, no scraping. The planner is pure computation over
static city data; booking links only appear when affiliate credentials exist.
"""

from .data import CITIES, City, display_name, get, highlights_for, popular, resolve
from .i18n import DEFAULT_LANG, LANGS, mode_label, normalise, t, ui_strings, weekday_name
from .links import attach_links, flight_link, hotel_link, train_link
from .planner import build_plan, fmt_duration
from .render import render

__all__ = [
    "CITIES",
    "DEFAULT_LANG",
    "LANGS",
    "City",
    "attach_links",
    "build_plan",
    "display_name",
    "flight_link",
    "fmt_duration",
    "get",
    "highlights_for",
    "hotel_link",
    "mode_label",
    "normalise",
    "popular",
    "render",
    "resolve",
    "t",
    "train_link",
    "ui_strings",
]
