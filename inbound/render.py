"""Printable itinerary renderer.

Output is a single self-contained HTML document with print CSS, so the buyer
can hit Ctrl/Cmd+P and get a clean PDF. No external assets.
"""

from __future__ import annotations

import html
from typing import Any, Dict

from .planner import _fmt

CSS = """
:root { --ink:#1a1a1a; --muted:#6b6b6b; --line:#e2e0da; --accent:#185FA5; --bg:#faf9f6; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.65 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif; }
.page { max-width:820px; margin:0 auto; padding:56px 48px 72px; background:#fff; }
h1 { font-size:30px; line-height:1.25; margin:0 0 8px; font-weight:600; letter-spacing:-.02em; }
.sub { color:var(--muted); font-size:14px; margin:0 0 32px; }
.meta { display:flex; gap:28px; flex-wrap:wrap; padding:16px 0; margin-bottom:36px;
  border-top:1px solid var(--line); border-bottom:1px solid var(--line); font-size:13px; }
.meta b { display:block; font-size:15px; font-weight:500; margin-top:2px; }
h2 { font-size:13px; text-transform:uppercase; letter-spacing:.09em; color:var(--muted);
  margin:36px 0 14px; font-weight:500; }
.leg { display:grid; grid-template-columns:96px 1fr auto; gap:14px; align-items:baseline;
  padding:11px 0; border-bottom:1px solid var(--line); }
.leg .mode { font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); }
.leg .dur { font-size:13px; color:var(--muted); white-space:nowrap; }
.leg small { color:var(--muted); }
.day { padding:16px 0; border-bottom:1px solid var(--line); page-break-inside:avoid; }
.day h3 { font-size:16px; margin:0 0 2px; font-weight:500; }
.day .d { font-size:12px; color:var(--muted); letter-spacing:.04em; text-transform:uppercase; }
.item { display:grid; grid-template-columns:56px 1fr; gap:12px; padding:5px 0; }
.item .t { font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
.item .x { font-size:14px; }
.item .x small { display:block; color:var(--muted); font-size:12.5px; }
.btn { display:inline-block; margin-top:4px; padding:5px 12px; border:1px solid var(--accent);
  border-radius:5px; color:var(--accent); text-decoration:none; font-size:12.5px; }
.no { font-size:12px; color:var(--muted); }
.foot { margin-top:40px; padding-top:18px; border-top:1px solid var(--line);
  font-size:12px; color:var(--muted); line-height:1.7; }
@media print {
  body { background:#fff; }
  .page { padding:0; max-width:none; }
  .noprint { display:none; }
}
"""


def _e(value: Any) -> str:
    return html.escape(str(value or ""))


def render(plan: Dict[str, Any]) -> str:
    if not plan.get("ok"):
        return f"<!doctype html><title>Itinerary</title><p>{_e(plan.get('error'))}</p>"

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(plan['title'])}</title>",
        f"<style>{CSS}</style>",
        "</head><body><div class='page'>",
        '<p class="noprint" style="text-align:right;margin:0 0 12px">',
        '<button onclick="window.print()" style="padding:8px 16px;border:1px solid #185FA5;',
        'background:#fff;color:#185FA5;border-radius:6px;cursor:pointer">Save as PDF</button></p>',
        f"<h1>{_e(plan['title'])}</h1>",
        f"<p class='sub'>{_e(plan['start'])} → {_e(plan['end'])} · "
        f"{len(plan.get('cities', []))} cities · {_e(plan.get('pace', 'balanced'))} pace</p>",
        '<div class="meta">',
        f"<div>Dates<b>{_e(plan['start'])} → {_e(plan['end'])}</b></div>",
        f"<div>Distance<b>{plan.get('total_distance_km', 0):,} km</b></div>",
        f"<div>Stops<b>{len(plan.get('cities', []))} cities</b></div>",
        "</div>",
    ]

    if plan.get("legs"):
        parts.append("<h2>Getting between cities</h2>")
        for leg in plan["legs"]:
            badge = "indicative" if leg.get("indicative") else "typical"
            parts.append(
                "<div class='leg'>"
                f"<div class='mode'>{_e(leg['mode'])}</div>"
                f"<div>{_e(leg['from'])} → {_e(leg['to'])}<br>"
                f"<small>{_e(leg['detail'])} · {_e(badge)}</small></div>"
                f"<div class='dur'>{_e(_fmt(leg['duration_min']))}</div>"
                "</div>"
            )

    parts.append("<h2>Day by day</h2>")
    for day in plan.get("days", []):
        parts.append("<div class='day'>")
        parts.append(f"<div class='d'>{_e(day['date'])}</div>")
        parts.append(f"<h3>{_e(day['city'])} <small style='color:#6b6b6b'>{_e(day.get('city_zh'))}</small></h3>")
        for item in day.get("items", []):
            parts.append(
                "<div class='item'>"
                f"<div class='t'>{_e(item['time'])}</div>"
                f"<div class='x'>{_e(item['title'])}<small>{_e(item['detail'])}</small></div>"
                "</div>"
            )
        parts.append("</div>")

    parts.append("<h2>Booking</h2>")
    if plan.get("affiliate_ready"):
        for leg in plan.get("legs", []):
            if leg.get("booking_url"):
                parts.append(
                    f"<div class='item'><div class='t'>{_e(leg['mode'])}</div>"
                    f"<div class='x'>{_e(leg['from'])} → {_e(leg['to'])}"
                    f"<a class='btn' href='{_e(leg['booking_url'])}' rel='nofollow sponsored' "
                    f"target='_blank'>Check times &amp; book</a></div></div>"
                )
    else:
        parts.append(
            "<p class='no'>Booking links are not configured yet "
            "(missing Trip.com affiliate credentials). Search your route on Trip.com directly.</p>"
        )

    parts.append(
        "<div class='foot'>"
        "Times shown are indicative and meant for planning. Confirm departure times, "
        "seat availability and prices with the booking provider before you travel.<br>"
        "Carry your passport on every train — it is required for ticket collection and "
        "station entry. Visa and entry rules are set by the Chinese authorities; check the "
        "latest requirements for your nationality.<br>"
        "Generated by Personal Travel Agent · commercial branch."
        "</div></div></body></html>"
    )
    return "".join(parts)
