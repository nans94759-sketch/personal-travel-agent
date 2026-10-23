"""
Personal Travel & Ticket Conversational Agent Server
----------------------------------------------------
Pure Chat + Inline Tool-Call Widgets Architecture:
- GET  /                    -> Minimalist Pure Chat Web UI
- POST /api/chat/tools      -> Step 1: Parse intent & execute live 12306 / Flight / Stopover / Transfer APIs (<1s)
- POST /api/chat/reply      -> Step 2: Generate natural LLM answer (+ optional Itinerary Widget) grounded in live data
- GET  /api/stopovers       -> Live 12306 train stopover timetable (czxx/queryByTrainNo)
- GET  /api/transfers       -> Smart 1-stop national hub rail transfer solver
- POST /api/export/ics      -> Generate downloadable RFC 5545 .ics calendar file
"""

import asyncio
import os
from typing import Any, Dict, List

from aiohttp import web

from planner_agent import (
    ai_route_tools,
    build_comparison_matrix,
    export_itinerary_to_ics,
    generate_chat_response,
    parse_user_intent,
)
from provider_12306 import Train12306Client
from provider_flights import FlightScraperClient
from provider_hotels import HotelScraperClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

train_client = Train12306Client()
flight_client = FlightScraperClient()
hotel_client = HotelScraperClient()


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def handle_chat_tools(request: web.Request) -> web.Response:
    """
    Step 1 of a chat turn:
    Parse user message + session context, invoke live 12306 / Flight / Hotel / Stopover / Transfer tools,
    and return structured inline widgets immediately.
    """
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        body = {}

    message = (body.get("message") or "").strip()
    context = body.get("context") or {}
    preferred_model = (body.get("model") or "deepseek-v4.1-flash").strip()

    loop = asyncio.get_running_loop()
    intent = await loop.run_in_executor(
        None, ai_route_tools, message, context, preferred_model
    )
    from_city = intent.get("from_city", "")
    to_city = intent.get("to_city", "")
    hotel_city = intent.get("hotel_city", "")
    date_str = intent.get("date", "")
    only_hotel = intent.get("only_hotel", False)
    asking_hotels = intent.get("asking_hotels", False)

    if not only_hotel and (not from_city or not to_city):
        if asking_hotels and hotel_city:
            only_hotel = True
        else:
            return web.json_response(
                {
                    "ok": True,
                    "needs_route_clarification": True,
                    "intent": intent,
                    "tool_badges": [],
                    "trains": [],
                    "flights": [],
                    "hotels": [],
                    "stopovers": [],
                    "transfers": [],
                    "comparison": None,
                }
            )
    elif only_hotel and not hotel_city:
        return web.json_response(
            {
                "ok": True,
                "needs_route_clarification": True,
                "intent": intent,
                "tool_badges": [],
                "trains": [],
                "flights": [],
                "hotels": [],
                "stopovers": [],
                "transfers": [],
                "comparison": None,
            }
        )

    loop = asyncio.get_running_loop()
    tool_badges: List[Dict[str, str]] = []
    trains: List[Dict[str, Any]] = []
    flights: List[Dict[str, Any]] = []
    hotels: List[Dict[str, Any]] = []
    hotel_meta: Dict[str, Any] = {}
    stopovers: List[Dict[str, Any]] = []
    transfers: List[Dict[str, Any]] = []

    # Execute 12306, Flight, and/or Hotel queries in parallel
    tasks = []
    run_trains = (not only_hotel) and bool(from_city and to_city) and (not intent.get("only_flight", False))
    run_flights = (not only_hotel) and bool(from_city and to_city) and (not intent.get("only_train", False))
    run_hotels = bool(asking_hotels and hotel_city)

    if run_trains:
        tasks.append(
            ("trains", loop.run_in_executor(None, train_client.query_trains, from_city, to_city, date_str, False))
        )
    if run_flights:
        tasks.append(
            ("flights", loop.run_in_executor(None, flight_client.query_flights, from_city, to_city, date_str))
        )
    if run_hotels:
        tasks.append(
            (
                "hotels",
                loop.run_in_executor(
                    None,
                    hotel_client.query_hotels,
                    hotel_city,
                    date_str,
                    None,
                    intent.get("hotel_area_keyword"),
                ),
            )
        )

    results = await asyncio.gather(*(t[1] for t in tasks))
    for (name, _), res in zip(tasks, results):
        if name == "trains":
            trains = res.get("trains", [])
            tool_badges.append(
                {
                    "tool": "12306",
                    "text": f"12306 实时余票接口 ({from_city} → {to_city} · {date_str})",
                    "count": f"{len(trains)} 趟列车",
                }
            )
        elif name == "flights":
            flights = res.get("flights", [])
            if flights or intent.get("only_flight"):
                tool_badges.append(
                    {
                        "tool": "flight",
                        "text": f"民航 SSR 实时航班接口 ({from_city} → {to_city} · {date_str})",
                        "count": f"{len(flights)} 架次",
                    }
                )
        elif name == "hotels":
            hotels = res.get("hotels", [])
            hotel_meta = {
                "city": res.get("city") or hotel_city,
                "zone": res.get("zone") or "全城热门",
                "checkin": res.get("checkin") or date_str,
                "checkout": res.get("checkout") or "",
            }
            tool_badges.append(
                {
                    "tool": "hotel",
                    "text": f"携程/Trip.com 实时酒店房价接口 ({hotel_meta['city']} · {hotel_meta['zone']} · {date_str}入住)",
                    "count": f"{len(hotels)} 家实时房价",
                }
            )

    # If user asked for a specific train's stopovers (e.g. "G1632经停哪些站")
    train_code_q = intent.get("train_code_query")
    if train_code_q and trains:
        matched_t = next(
            (t for t in trains if t["train_code"].upper() == train_code_q.upper()),
            None,
        )
        if matched_t and intent.get("asking_stopovers"):
            stopovers = await loop.run_in_executor(
                None,
                train_client.query_stopovers,
                matched_t["train_no"],
                matched_t["from_station_code"],
                matched_t["to_station_code"],
                date_str,
            )
            tool_badges.append(
                {
                    "tool": "stopover",
                    "text": f"12306 列车经停时刻表 ({matched_t['train_code']})",
                    "count": f"{len(stopovers)} 站",
                }
            )

    # If user explicitly asked for transfers OR direct trains == 0
    if intent.get("asking_transfer") or (run_trains and len(trains) == 0):
        transfers = await loop.run_in_executor(
            None, train_client.query_smart_transfers, from_city, to_city, date_str, 2
        )
        if transfers:
            tool_badges.append(
                {
                    "tool": "transfer",
                    "text": f"12306 全国枢纽联程中转计算 ({from_city} → {to_city})",
                    "count": f"{len(transfers)} 套方案",
                }
            )

    comparison = build_comparison_matrix(trains, flights, transfers)

    # Generate smart contextual follow-up chips
    follow_ups = []
    if trains:
        best_t = (comparison.get("train_option") or {}).get("best") or trains[0]
        follow_ups.append(f"查看 {best_t['train_code']} 途经哪些经停站")
        if not hotels:
            follow_ups.append(f"查一下{date_str}{to_city}有哪些高分酒店")
        if not intent.get("needs_itinerary"):
            follow_ups.append(f"基于 {best_t['train_code']} 帮我规划{to_city}2天行程")
    elif hotels and hotel_city:
        follow_ups.append(f"明天去{hotel_city}有哪些高铁和机票？")
        follow_ups.append(f"帮我规划{hotel_city}2天游行程")
    if flights and not intent.get("only_flight"):
        follow_ups.append(f"只看{from_city}到{to_city}的便宜机票")
    if from_city and to_city:
        follow_ups.append(f"那后天从{from_city}到{to_city}呢？")

    return web.json_response(
        {
            "ok": True,
            "needs_route_clarification": False,
            "intent": intent,
            "tool_badges": tool_badges,
            "trains": trains,
            "flights": flights,
            "hotels": hotels,
            "hotel_meta": hotel_meta,
            "stopovers": stopovers,
            "transfers": transfers,
            "comparison": comparison,
            "follow_ups": follow_ups[:3],
        }
    )


async def handle_chat_reply(request: web.Request) -> web.Response:
    """
    Step 2 of a chat turn:
    Call live LLM with the real tool results to produce a concise conversational reply
    (and optional structured Itinerary Widget if user asked for trip planning).
    """
    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        body = {}

    message = (body.get("message") or "").strip()
    intent = body.get("intent") or {}
    trains = body.get("trains") or []
    flights = body.get("flights") or []
    hotels = body.get("hotels") or []
    stopovers = body.get("stopovers") or []
    transfers = body.get("transfers") or []
    comparison = body.get("comparison") or {}
    history = body.get("history") or []
    preferred_model = (body.get("model") or "deepseek-v4.1-flash").strip()

    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(
        None,
        generate_chat_response,
        message,
        intent,
        trains,
        flights,
        hotels,
        stopovers,
        transfers,
        comparison,
        history,
        preferred_model,
    )
    return web.json_response(res)


async def handle_stopovers(request: web.Request) -> web.Response:
    train_no = request.query.get("train_no", "").strip()
    from_code = request.query.get("from_code", "").strip()
    to_code = request.query.get("to_code", "").strip()
    date_str = request.query.get("date", "").strip()
    if not (train_no and from_code and to_code and date_str):
        return web.json_response({"ok": False, "stops": []})

    loop = asyncio.get_running_loop()
    stops = await loop.run_in_executor(
        None, train_client.query_stopovers, train_no, from_code, to_code, date_str
    )
    return web.json_response({"ok": True, "stops": stops})


async def handle_transfers(request: web.Request) -> web.Response:
    from_city = request.query.get("from", "北京").strip()
    to_city = request.query.get("to", "上海").strip()
    date_str = request.query.get("date", "").strip()
    loop = asyncio.get_running_loop()
    transfers = await loop.run_in_executor(
        None, train_client.query_smart_transfers, from_city, to_city, date_str, 3
    )
    return web.json_response({"ok": True, "transfers": transfers})


async def handle_export_ics(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    itinerary = body.get("itinerary") or {}
    ics_text = export_itinerary_to_ics(itinerary)
    return web.Response(
        text=ics_text,
        content_type="text/calendar",
        headers={
            "Content-Disposition": 'attachment; filename="smart_itinerary.ics"',
            "Cache-Control": "no-cache",
        },
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_post("/api/chat/tools", handle_chat_tools)
    app.router.add_post("/api/chat/reply", handle_chat_reply)
    app.router.add_get("/api/stopovers", handle_stopovers)
    app.router.add_get("/api/transfers", handle_transfers)
    app.router.add_post("/api/export/ics", handle_export_ics)
    if os.path.exists(STATIC_DIR):
        app.router.add_static("/static", STATIC_DIR)
    return app


if __name__ == "__main__":
    os.makedirs(STATIC_DIR, exist_ok=True)
    port = int(os.environ.get("PORT", 8868))
    print(f"🚀 个人行程安排智能体 (纯对话版) 已启动: http://127.0.0.1:{port}", flush=True)
    web.run_app(create_app(), host="127.0.0.1", port=port, print=None)
