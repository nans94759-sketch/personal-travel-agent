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
from provider_attractions import GlobalAttractionClient
from provider_flights import FlightScraperClient
from provider_global_transit import (
    CITY_COUNTRY_CURRENCY_MAP,
    EURO_RAIL_STATION_MAP,
    GlobalTransitClient,
)
from provider_hotels import HotelScraperClient

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

train_client = Train12306Client()
flight_client = FlightScraperClient()
hotel_client = HotelScraperClient()
attraction_client = GlobalAttractionClient()
transit_client = GlobalTransitClient()


async def handle_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def handle_chat_tools(request: web.Request) -> web.Response:
    """
    Step 1 of a chat turn:
    Parse user message + session context, invoke live 12306 / Global Flight / Euro Rail /
    Global Transit Passes / Global Hotel / Global Attraction Ticket / FX & Timezone tools,
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
    attraction_city = intent.get("attraction_city", "") or to_city or hotel_city
    date_str = intent.get("date", "")
    only_hotel = intent.get("only_hotel", False)
    asking_hotels = intent.get("asking_hotels", False)
    asking_attractions = intent.get("asking_attractions", False)
    only_attraction = intent.get("only_attraction", False)
    asking_euro_trains = intent.get("asking_euro_trains", False)
    asking_global_passes = intent.get("asking_global_passes", False)
    only_global_pass = intent.get("only_global_pass", False)

    has_route = bool(from_city and to_city)
    has_single_city_task = bool(
        (asking_hotels and hotel_city)
        or (asking_attractions and attraction_city)
        or (asking_global_passes and (attraction_city or to_city or hotel_city or from_city))
        or (intent.get("needs_itinerary") and (to_city or hotel_city or attraction_city))
    )

    if not has_route and not has_single_city_task:
        return web.json_response(
            {
                "ok": True,
                "needs_route_clarification": True,
                "intent": intent,
                "tool_badges": [],
                "trains": [],
                "euro_trains": [],
                "flights": [],
                "hotels": [],
                "attractions": [],
                "global_passes": [],
                "destination_essentials": None,
                "stopovers": [],
                "transfers": [],
                "comparison": None,
            }
        )

    tool_badges: List[Dict[str, str]] = []
    trains: List[Dict[str, Any]] = []
    euro_trains: List[Dict[str, Any]] = []
    flights: List[Dict[str, Any]] = []
    hotels: List[Dict[str, Any]] = []
    hotel_meta: Dict[str, Any] = {}
    attractions: List[Dict[str, Any]] = []
    attraction_meta: Dict[str, Any] = {}
    global_passes: List[Dict[str, Any]] = []
    destination_essentials: Dict[str, Any] = {}
    stopovers: List[Dict[str, Any]] = []
    transfers: List[Dict[str, Any]] = []

    is_euro_route = bool(
        from_city in EURO_RAIL_STATION_MAP or to_city in EURO_RAIL_STATION_MAP
    )
    skip_route_transport = bool(only_hotel or only_attraction or only_global_pass)

    tasks = []
    run_12306 = (
        has_route
        and (not skip_route_transport)
        and (not intent.get("only_flight", False))
        and (not is_euro_route)
    )
    run_euro_rail = (
        has_route
        and (not skip_route_transport)
        and (not intent.get("only_flight", False))
        and (is_euro_route or asking_euro_trains)
    )
    run_flights = (
        has_route
        and (not skip_route_transport)
        and (not intent.get("only_train", False))
    )
    run_hotels = bool(asking_hotels and hotel_city)
    run_attractions = bool(asking_attractions and attraction_city)
    pass_target_city = attraction_city or to_city or hotel_city or from_city
    run_global_passes = bool(asking_global_passes and pass_target_city)

    dest_city_for_fx = to_city or hotel_city or attraction_city
    dest_city_info = (
        flight_client.resolve_city_code(dest_city_for_fx)
        if dest_city_for_fx
        else None
    ) or {}
    is_intl_dest = bool(
        (dest_city_for_fx and dest_city_for_fx in CITY_COUNTRY_CURRENCY_MAP)
        or dest_city_info.get("is_inter")
    )

    if run_12306:
        tasks.append(
            ("trains", loop.run_in_executor(None, train_client.query_trains, from_city, to_city, date_str, False))
        )
    if run_euro_rail:
        tasks.append(
            ("euro_trains", loop.run_in_executor(None, transit_client.query_european_trains, from_city, to_city, date_str))
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
    if run_attractions:
        tasks.append(
            (
                "attractions",
                loop.run_in_executor(
                    None,
                    attraction_client.query_attractions,
                    attraction_city,
                    intent.get("attraction_keyword"),
                ),
            )
        )
    if run_global_passes:
        tasks.append(
            (
                "global_passes",
                loop.run_in_executor(
                    None,
                    transit_client.query_global_transport_passes,
                    intent.get("pass_keyword") or pass_target_city,
                ),
            )
        )
    if is_intl_dest:
        tasks.append(
            (
                "essentials",
                loop.run_in_executor(
                    None,
                    transit_client.query_destination_essentials,
                    dest_city_for_fx,
                ),
            )
        )

    results = await asyncio.gather(*(t[1] for t in tasks))
    for (name, _), res in zip(tasks, results):
        if name == "trains":
            trains = res.get("trains", [])
            if trains or not is_intl_dest:
                tool_badges.append(
                    {
                        "tool": "12306",
                        "text": f"{from_city} → {to_city} 火车票",
                        "count": f"{len(trains)} 趟",
                    }
                )
        elif name == "euro_trains":
            euro_trains = res.get("trains", [])
            if euro_trains:
                tool_badges.append(
                    {
                        "tool": "euro_rail",
                        "text": f"{from_city} → {to_city} 欧洲铁路",
                        "count": f"{len(euro_trains)} 趟",
                    }
                )
        elif name == "flights":
            flights = res.get("flights", [])
            if flights or intent.get("only_flight"):
                tool_badges.append(
                    {
                        "tool": "flight",
                        "text": f"{from_city} → {to_city} 机票",
                        "count": f"{len(flights)} 班",
                    }
                )
        elif name == "hotels":
            hotels = res.get("hotels", [])
            hotel_meta = {
                "city": res.get("city") or hotel_city,
                "zone": res.get("zone") or "热门区域",
                "checkin": res.get("checkin") or date_str,
                "checkout": res.get("checkout") or "",
            }
            tool_badges.append(
                {
                    "tool": "hotel",
                    "text": f"{hotel_meta['city']} · {hotel_meta['zone']}酒店",
                    "count": f"{len(hotels)} 家",
                }
            )
        elif name == "attractions":
            attractions = res.get("attractions", [])
            attraction_meta = {
                "city": res.get("city") or attraction_city,
                "district_id": res.get("district_id"),
                "source": res.get("source", ""),
            }
            tool_badges.append(
                {
                    "tool": "attraction",
                    "text": f"{attraction_meta['city']}景点门票",
                    "count": f"{len(attractions)} 处",
                }
            )
        elif name == "global_passes":
            global_passes = res if isinstance(res, list) else res.get("passes", [])
            if global_passes:
                tool_badges.append(
                    {
                        "tool": "global_pass",
                        "text": f"{pass_target_city}交通卡券",
                        "count": f"{len(global_passes)} 项",
                    }
                )
        elif name == "essentials":
            destination_essentials = res or {}
            if destination_essentials.get("fx_rate_text"):
                tool_badges.append(
                    {
                        "tool": "fx",
                        "text": f"{dest_city_for_fx}汇率与时差",
                        "count": f"{destination_essentials.get('timezone_desc', '')}",
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
                    "text": f"{matched_t['train_code']} 经停时刻",
                    "count": f"{len(stopovers)} 站",
                }
            )

    # Auto-trigger Smart Multi-Modal Transfers when:
    # 1) User explicitly asks for transfers / 空铁联运 / 转机 / 换乘
    # 2) Route is cross-border / international (e.g. 杭州 -> 河内, 苏州 -> 东京)
    # 3) Direct domestic trains/flights are scarce or zero
    need_auto_transfer = bool(
        has_route
        and not skip_route_transport
        and not is_euro_route
        and (
            intent.get("asking_transfer")
            or is_intl_dest
            or (run_12306 and len(trains) <= 2)
            or (run_flights and len(flights) == 0)
        )
    )
    if need_auto_transfer:
        transfers = await loop.run_in_executor(
            None,
            flight_client.query_smart_multi_transfers,
            from_city,
            to_city,
            date_str,
            train_client,
        )
        if transfers:
            tool_badges.append(
                {
                    "tool": "transfer",
                    "text": f"{from_city} → {to_city} 智能中转",
                    "count": f"{len(transfers)} 套",
                }
            )

    comparison = build_comparison_matrix(trains, flights, transfers)

    # Generate smart contextual follow-up chips
    follow_ups = []
    target_c = to_city or hotel_city or attraction_city
    if trains:
        best_t = (comparison.get("train_option") or {}).get("best") or trains[0]
        follow_ups.append(f"{best_t['train_code']} 沿途停靠哪些站")
    if target_c and not hotels:
        follow_ups.append(f"{target_c}有哪些高分酒店")
    if target_c and not attractions:
        follow_ups.append(f"{target_c}热门景点门票")
    if target_c and not global_passes and is_intl_dest:
        follow_ups.append(f"{target_c}交通卡与机场快线")
    if target_c and not intent.get("needs_itinerary"):
        follow_ups.append(f"规划{target_c} 3 天游玩行程")
    if from_city and to_city:
        follow_ups.append(f"后天{from_city}去{to_city}呢")

    return web.json_response(
        {
            "ok": True,
            "needs_route_clarification": False,
            "intent": intent,
            "tool_badges": tool_badges,
            "trains": trains,
            "euro_trains": euro_trains,
            "flights": flights,
            "hotels": hotels,
            "hotel_meta": hotel_meta,
            "attractions": attractions,
            "attraction_meta": attraction_meta,
            "global_passes": global_passes,
            "destination_essentials": destination_essentials,
            "stopovers": stopovers,
            "transfers": transfers,
            "comparison": comparison,
            "follow_ups": follow_ups[:4],
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
    euro_trains = body.get("euro_trains") or []
    flights = body.get("flights") or []
    hotels = body.get("hotels") or []
    attractions = body.get("attractions") or []
    global_passes = body.get("global_passes") or []
    destination_essentials = body.get("destination_essentials") or {}
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
        attractions,
        euro_trains,
        global_passes,
        destination_essentials,
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
        None,
        flight_client.query_smart_multi_transfers,
        from_city,
        to_city,
        date_str,
        train_client,
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


async def handle_fx(request: web.Request) -> web.Response:
    force = request.query.get("refresh") == "1"
    loop = asyncio.get_running_loop()
    fx_data = await loop.run_in_executor(None, transit_client.get_all_fx_rates, force)
    return web.json_response(
        {
            **fx_data,
            "cities": CITY_COUNTRY_CURRENCY_MAP,
        }
    )


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/fx", handle_fx)
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
