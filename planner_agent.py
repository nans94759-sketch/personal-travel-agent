"""
Conversational Travel Agent & Tool Orchestrator
-----------------------------------------------
100% Live Data Driven (Zero Mock Data):
- Full 3,300+ 12306 station & city natural language recognition (including county stations like 玉山, 昆山, 义乌, 婺源, etc.)
- Full Chinese relative date & weekday parser (今天/明天/后天/本周日/下周一/10月25日)
- Multi-turn context inheritance (remembers route & date across follow-up questions)
- Smart Tool Selection (12306 Live Tickets, OTA Live Flights, Train Stopovers, Hub Transfers, Door-to-Door Comparison)
- Live LLM response generation (concise ticket analysis for transport queries; full structured Itinerary Widget when planning a trip)
"""

import datetime
import json
import os
import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from provider_12306 import Train12306Client
from provider_hotels import CTRIP_CITY_ID_MAP

def _load_dotenv() -> None:
    """Load key=value pairs from local .env file if present."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_dotenv()

DASHSCOPE_URL = os.environ.get(
    "LLM_API_URL",
    os.environ.get(
        "DASHSCOPE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    ),
)
DASHSCOPE_API_KEY = os.environ.get(
    "LLM_API_KEY",
    os.environ.get("DASHSCOPE_API_KEY", ""),
)

WEEKDAY_MAP = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

_station_helper = Train12306Client()
ALL_PLACES_SORTED: List[str] = sorted(
    set(
        list(_station_helper.stations_by_name.keys())
        + list(_station_helper.city_to_main_station.keys())
        + list(CTRIP_CITY_ID_MAP.keys())
    ),
    key=len,
    reverse=True,
)


def _match_place_in_token(token: str) -> Optional[str]:
    """Match a token against 3,300+ 12306 stations & cities."""
    if not token:
        return None
    t = token.strip()
    t = re.sub(
        r"^(今天|明天|后天|大后天|本周[一二三四五六日天]|下周[一二三四五六日天]|这周[一二三四五六日天]|周[一二三四五六日天]|星期[一二三四五六日天]|礼拜[一二三四五六日天]|打算|准备|计划|想|要|帮我|请问|查一下|查询|看看|从)+",
        "",
        t,
    )
    t_clean = re.sub(r"(市|站|地区|特别行政区|县|区)$", "", t)

    if t in _station_helper.stations_by_name or t in _station_helper.city_to_main_station:
        return t
    if (
        t_clean in _station_helper.stations_by_name
        or t_clean in _station_helper.city_to_main_station
    ):
        return t_clean

    for place in ALL_PLACES_SORTED:
        if len(place) >= 2 and place in t:
            return place
    return None


def parse_chinese_date(message: str, default_date: Optional[str] = None) -> Tuple[str, bool]:
    """
    Parse Chinese natural language date expressions into YYYY-MM-DD.
    Returns (date_str, matched_from_message).
    """
    today = datetime.date.today()
    if not default_date:
        default_date = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    m_ymd = re.search(r"(202\d)[-/年.](\d{1,2})[-/月.](\d{1,2})", message)
    if m_ymd:
        return (
            f"{int(m_ymd.group(1)):04d}-{int(m_ymd.group(2)):02d}-{int(m_ymd.group(3)):02d}",
            True,
        )

    m_md = re.search(r"(\d{1,2})[月/-](\d{1,2})[日号]?", message)
    if m_md:
        return (
            f"{today.year:04d}-{int(m_md.group(1)):02d}-{int(m_md.group(2)):02d}",
            True,
        )

    if "大后天" in message:
        return (today + datetime.timedelta(days=3)).strftime("%Y-%m-%d"), True
    if "后天" in message:
        return (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d"), True
    if "明天" in message or "明日" in message:
        return (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d"), True
    if "今天" in message or "今日" in message:
        return today.strftime("%Y-%m-%d"), True

    m_week = re.search(
        r"(下周|下星期|下礼拜|本周|这周|本星期|周|星期|礼拜)([一二三四五六日天])", message
    )
    if m_week:
        prefix = m_week.group(1)
        target_wd = WEEKDAY_MAP[m_week.group(2)]
        cur_wd = today.weekday()
        if prefix.startswith("下"):
            days_ahead = (7 - cur_wd) + target_wd
        else:
            days_ahead = target_wd - cur_wd
            if days_ahead < 0:
                days_ahead += 7
        return (today + datetime.timedelta(days=days_ahead)).strftime("%Y-%m-%d"), True

    return default_date, False


def parse_user_intent(
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Parse user natural language message with multi-turn session context fallback.
    Determines:
    - from_city, to_city, date
    - requested_tools: ['trains', 'flights', 'stopovers', 'transfers']
    - needs_itinerary: bool (True only if user asks for trip planning / itinerary / multi-day play)
    - train_code_query: optional specific train code (e.g. 'G1632')
    """
    ctx = context or {}
    default_from = ctx.get("from_city") or ""
    default_to = ctx.get("to_city") or ""
    default_date = ctx.get("date") or (
        datetime.date.today() + datetime.timedelta(days=1)
    ).strftime("%Y-%m-%d")

    target_date, date_from_msg = parse_chinese_date(message, default_date)

    msg_for_cities = re.sub(
        r"(202\d[-/年.]\d{1,2}[-/月.]\d{1,2}|\d{1,2}[月/-]\d{1,2}[日号]?|大后天|后天|明天|明日|今天|今日|(?:下周|下星期|下礼拜|本周|这周|本星期|周|星期|礼拜)[一二三四五六日天])",
        " ",
        message,
    )

    from_city: Optional[str] = None
    to_city: Optional[str] = None

    m_route = re.search(
        r"(?:从)?([\u4e00-\u9fa5]{2,8}?)(?:出发|坐车|乘车)?(?:去|到|飞|前往|至|回|->|—|-|➔)([\u4e00-\u9fa5]{2,8})",
        msg_for_cities,
    )
    if m_route:
        raw_c1, raw_c2 = m_route.group(1), m_route.group(2)
        raw_c2 = re.split(
            r"(玩|出差|旅游|度假|散心|看|办|住|待|呆|有|的|怎么|哪|高|火|机|航|动)", raw_c2
        )[0]
        from_city = _match_place_in_token(raw_c1)
        to_city = _match_place_in_token(raw_c2)

    if not from_city or not to_city:
        hits: List[Tuple[int, str]] = []
        occupied = [False] * len(msg_for_cities)
        for place in ALL_PLACES_SORTED:
            if len(place) < 2:
                continue
            start_pos = 0
            while True:
                idx = msg_for_cities.find(place, start_pos)
                if idx == -1:
                    break
                if not any(occupied[idx : idx + len(place)]):
                    for k in range(idx, idx + len(place)):
                        occupied[k] = True
                    hits.append((idx, place))
                start_pos = idx + len(place)

        hits.sort(key=lambda x: x[0])
        if len(hits) >= 2:
            from_city = from_city or hits[0][1]
            to_city = to_city or hits[1][1]
        elif len(hits) == 1:
            if any(k in msg_for_cities for k in ["去", "到", "飞", "前往", "至", "酒店", "宾馆", "住宿", "住哪", "民宿", "房价", "玩", "旅游", "攻略"]):
                to_city = to_city or hits[0][1]
            else:
                from_city = from_city or hits[0][1]

    single_detected_city = to_city or from_city
    cities_from_msg = bool(from_city and to_city)
    from_city = from_city or default_from
    to_city = to_city or default_to

    # Check if user is asking for hotels / accommodation
    asking_hotels = any(
        k in message for k in ["酒店", "宾馆", "住宿", "住哪", "民宿", "房价", "订房", "旅馆"]
    )
    only_hotel = asking_hotels and not any(
        k in message
        for k in ["高铁", "动车", "火车", "12306", "机票", "飞机", "航班", "怎么去", "坐车"]
    )
    hotel_city = to_city or single_detected_city or default_to or default_from
    hotel_area_keyword = None
    if hotel_city and hotel_city in msg_for_cities:
        m_area = re.search(
            re.escape(hotel_city) + r"[,，、\s]*([\u4e00-\u9fa5]{2,7}?)(?:旁边|附近|周边|商圈|一带|的)?[,，、\s]*(?:酒店|宾馆|住宿|民宿|房价|有什么|有哪些)",
            msg_for_cities,
        )
        if m_area:
            cand = re.sub(r"(旁边|附近|周边|商圈|一带|的)+$", "", m_area.group(1).strip()).strip()
            if cand and cand not in ("有哪些", "有什么", "附近", "周边", "旁边", "便宜", "高分", "好"):
                hotel_area_keyword = cand

    # Check if user is asking for a specific train code's stopovers (e.g. G1632)
    m_train_code = re.search(r"\b([GDCZTK]\d{1,4}|\d{4})\b", message.upper())
    train_code_query = m_train_code.group(1) if m_train_code else None
    asking_stopovers = bool(
        train_code_query
        and any(k in message for k in ["经停", "途经", "时刻", "哪些站", "几站", "停靠"])
    )

    # Determine whether user wants an itinerary / multi-day plan
    days = None
    m_days = re.search(r"(\d+)\s*[天日]", message)
    if m_days:
        days = max(1, min(10, int(m_days.group(1))))
    elif any(k in message for k in ["两天", "两日", "周末游"]):
        days = 2
    elif any(k in message for k in ["三天", "三日"]):
        days = 3
    elif any(k in message for k in ["四天", "四日"]):
        days = 4
    elif any(k in message for k in ["五天", "五日"]):
        days = 5

    needs_itinerary = bool(
        days
        or any(
            k in message
            for k in ["行程", "攻略", "规划", "安排", "怎么玩", "游玩", "旅游", "度假", "景点", "美食"]
        )
    )
    if not days:
        days = ctx.get("days") or 2

    # Determine which tools to invoke
    only_train = any(k in message for k in ["高铁", "动车", "火车", "12306", "列车", "卧铺", "二等座"]) and not any(
        k in message for k in ["机票", "飞机", "航班", "对比", "划算", "还是"]
    )
    only_flight = any(k in message for k in ["机票", "飞机", "航班", "直飞", "航司"]) and not any(
        k in message for k in ["高铁", "火车", "12306", "对比", "划算", "还是"]
    )
    asking_transfer = any(k in message for k in ["中转", "换乘", "联程", "转车"])

    budget = None
    m_budget = re.search(r"预算\s*(\d{3,5})", message) or re.search(
        r"(\d{3,5})\s*元", message
    )
    if m_budget:
        budget = int(m_budget.group(1))

    style = "品质探索"
    if any(k in message for k in ["商务", "出差", "开会", "高效"]):
        style = "高效差旅"
    elif any(k in message for k in ["穷游", "特种兵", "省钱", "学生", "性价比"]):
        style = "高性价比特种兵"
    elif any(k in message for k in ["亲子", "带娃", "老人", "家庭", "轻松", "休闲"]):
        style = "轻松度假/家庭"
    elif any(k in message for k in ["美食", "吃货", "探店", "拍照", "人文"]):
        style = "人文美食深度游"

    return {
        "from_city": from_city,
        "to_city": to_city,
        "date": target_date,
        "date_from_message": date_from_msg,
        "cities_from_message": cities_from_msg,
        "days": days,
        "budget": budget,
        "style": style,
        "needs_itinerary": needs_itinerary,
        "only_train": only_train,
        "only_flight": only_flight,
        "only_hotel": only_hotel,
        "asking_hotels": asking_hotels or needs_itinerary,
        "hotel_city": hotel_city,
        "hotel_area_keyword": hotel_area_keyword,
        "asking_stopovers": asking_stopovers,
        "asking_transfer": asking_transfer,
        "train_code_query": train_code_query,
    }


def ai_route_tools(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    preferred_model: str = "deepseek-v4.1-flash",
) -> Dict[str, Any]:
    """
    AI-Driven Tool Router (Agentic Tool Specification):
    Gives the LLM the exact specification of the 5 real-time tools and current calendar context,
    allowing the AI to autonomously decide which tools to call and extract clean parameters
    (including international cities like 河内/东京/曼谷, county towns like 玉山, and landmarks like 机场旁边/外滩).
    Falls back to fast rule-based `parse_user_intent` if needed.
    """
    base_intent = parse_user_intent(message, context=context)
    today = datetime.date.today()
    weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]

    # Pre-calculate upcoming dates so the LLM never miscalculates calendar dates
    upcoming_days = []
    for d_offset in range(0, 11):
        d = today + datetime.timedelta(days=d_offset)
        wd = ["一", "二", "三", "四", "五", "六", "日"][d.weekday()]
        label = (
            "今天"
            if d_offset == 0
            else "明天"
            if d_offset == 1
            else "后天"
            if d_offset == 2
            else f"+{d_offset}天"
        )
        upcoming_days.append(f"{d.strftime('%Y-%m-%d')}({label}/周{wd})")

    ctx = context or {}
    router_sys = (
        "你是一个出行智能体的【工具调度路由器 (Tool Router)】。\n"
        f"当前系统时间：{today.strftime('%Y-%m-%d')} ({weekday_cn})。\n"
        f"未来10天日历对照：{', '.join(upcoming_days)}。\n"
        f"上一轮对话记忆上下文：出发地={ctx.get('from_city') or '无'}, 目的地={ctx.get('to_city') or '无'}, 日期={ctx.get('date') or '无'}。\n\n"
        "【可用实时工具规范 (Tool Specifications)】：\n"
        "1. `query_trains`: 查询中国大陆 12306 火车/高铁/动车实时余票与票价。参数：`from_city`(出发城市/县), `to_city`(到达城市/县), `date`(YYYY-MM-DD)。\n"
        "2. `query_flights`: 查询中国国内民航实时低价航班。参数：`from_city`, `to_city`, `date`(YYYY-MM-DD)。\n"
        "3. `query_hotels`: 查询全球及全国城市/县城实时酒店房价（支持国内市县如上海/玉山，也支持海外城市如河内/胡志明市/东京/曼谷/新加坡/首尔等）。参数：`hotel_city`(酒店所在城市纯名称，如'河内'、'上海'、'玉山'), `date`(入住日期 YYYY-MM-DD), `hotel_area_keyword`(商圈/机场/地标纯关键词，如用户说'机场旁边'则填'机场'，说'外滩附近'则填'外滩'，没有则填 null)。\n"
        "4. `query_stopovers`: 查询指定车次（如 G1632）途经的所有经停站时刻表。参数：`train_code_query`(车次号)。\n"
        "5. `query_transfers`: 查询 12306 全国枢纽中转换乘方案（当用户问中转/换乘时调用）。\n\n"
        "请根据用户的自然语言输入，自主决定应该调用哪些工具，并只输出如下合法 JSON 对象（不要输出任何 Markdown 代码块或多余解释）：\n"
        "{\n"
        '  "tools": ["query_hotels"], // 从 ["query_trains", "query_flights", "query_hotels", "query_stopovers", "query_transfers"] 中按需选择1个或多个\n'
        '  "from_city": "出发城市或空字符串",\n'
        '  "to_city": "到达城市或空字符串",\n'
        '  "date": "YYYY-MM-DD",\n'
        '  "hotel_city": "酒店所在城市（如只查酒店也必填，例如 河内/上海）",\n'
        '  "hotel_area_keyword": "机场 / 外滩 / 西湖 等纯地标词，无则填 null",\n'
        '  "train_code_query": "G1632 等车次号，无则填 null",\n'
        '  "needs_itinerary": false, // 用户是否要求规划多日游玩行程/旅游攻略\n'
        '  "days": 2 // 游玩天数\n'
        "}"
    )

    raw_out, _, _ = call_llm_raw(
        router_sys,
        f"用户输入：{message}",
        preferred_model=preferred_model,
        timeout=10,
    )
    if raw_out:
        m = re.search(r"\{.*\}", raw_out, re.S)
        if m:
            try:
                ai_plan = json.loads(m.group(0))
                tools = ai_plan.get("tools") or []
                from_c = (ai_plan.get("from_city") or "").strip() or base_intent["from_city"]
                to_c = (ai_plan.get("to_city") or "").strip() or base_intent["to_city"]
                hotel_c = (ai_plan.get("hotel_city") or "").strip() or to_c or base_intent["hotel_city"]
                area_kw = ai_plan.get("hotel_area_keyword")
                if isinstance(area_kw, str):
                    area_kw = re.sub(r"(旁边|附近|周边|一带|商圈|的|酒店|价格|房价)+$", "", area_kw.strip()).strip() or None
                else:
                    area_kw = base_intent.get("hotel_area_keyword")

                date_val = (ai_plan.get("date") or "").strip()
                if base_intent["date_from_message"]:
                    date_val = base_intent["date"]
                elif not re.match(r"^\d{4}-\d{2}-\d{2}$", date_val):
                    date_val = base_intent["date"]

                want_trains = "query_trains" in tools
                want_flights = "query_flights" in tools
                want_hotels = "query_hotels" in tools or bool(ai_plan.get("needs_itinerary"))
                want_stopovers = "query_stopovers" in tools
                want_transfers = "query_transfers" in tools

                # If user only asked for hotels (e.g., "河内，机场旁边，酒店价格，下周一")
                only_hotel = want_hotels and (not want_trains) and (not want_flights)
                only_train = want_trains and (not want_flights)
                only_flight = want_flights and (not want_trains)

                return {
                    **base_intent,
                    "router_mode": "ai_tool_spec",
                    "tools": tools,
                    "from_city": from_c,
                    "to_city": to_c or hotel_c,
                    "date": date_val,
                    "days": int(ai_plan.get("days") or base_intent["days"] or 2),
                    "needs_itinerary": bool(ai_plan.get("needs_itinerary") or base_intent["needs_itinerary"]),
                    "only_train": only_train,
                    "only_flight": only_flight,
                    "only_hotel": only_hotel,
                    "asking_hotels": want_hotels,
                    "hotel_city": hotel_c,
                    "hotel_area_keyword": area_kw,
                    "asking_stopovers": want_stopovers or base_intent["asking_stopovers"],
                    "asking_transfer": want_transfers or base_intent["asking_transfer"],
                    "train_code_query": ai_plan.get("train_code_query") or base_intent["train_code_query"],
                }
            except Exception:
                pass

    return {**base_intent, "router_mode": "rule_fallback"}


def call_llm_raw(
    system_prompt: str,
    user_prompt: str,
    preferred_model: str = "deepseek-v4.1-flash",
    timeout: int = 22,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Call DeepSeek V4.1 / DeepSeek V4 (via Aliyun DashScope Official Node) with zero TLS drops.
    Returns (raw_text, actual_model_name, last_error).
    """
    candidate_models = [preferred_model]
    for fallback_m in [
        "deepseek-v4.1-flash",
        "deepseek-v4-flash-0731",
        "deepseek-v4-pro-0813",
        "qwen-plus",
    ]:
        if fallback_m not in candidate_models:
            candidate_models.append(fallback_m)

    last_err = None
    for model_name in candidate_models:
        try:
            api_key = DASHSCOPE_API_KEY
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.3,
            }
            base_urls = [DASHSCOPE_URL]

            for target_url in base_urls:
                cmd = [
                    "curl",
                    "-sS",
                    "--retry",
                    "1",
                    "--max-time",
                    str(timeout),
                    "-X",
                    "POST",
                    target_url,
                    "-H",
                    f"Authorization: Bearer {api_key}",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(payload, ensure_ascii=False),
                ]
                raw_out = subprocess.check_output(
                    cmd, timeout=timeout + 3, stderr=subprocess.DEVNULL
                ).decode("utf-8", errors="ignore")
                data = json.loads(raw_out)
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
                if content:
                    return content, model_name, None
        except Exception as e:
            last_err = f"{model_name}: {str(e)}"
            continue

    return None, None, last_err


def build_comparison_matrix(
    trains: List[Dict[str, Any]],
    flights: List[Dict[str, Any]],
    transfers: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build quantitative door-to-door comparison between High-Speed Rail, Flight, and Transfer."""
    bookable_trains = [t for t in trains if t.get("can_buy") and t.get("lowest_price")]
    hsr_trains = [
        t for t in bookable_trains if t["train_code"].startswith(("G", "D", "C"))
    ]
    candidate_trains = hsr_trains or bookable_trains or trains

    best_train = None
    fastest_train = None
    cheapest_train = None
    if candidate_trains:
        with_price = [t for t in candidate_trains if t.get("lowest_price")]
        fastest_train = min(candidate_trains, key=lambda x: x["duration_minutes"])
        cheapest_train = (
            min(with_price, key=lambda x: x["lowest_price"])
            if with_price
            else fastest_train
        )
        best_train = min(
            with_price or candidate_trains,
            key=lambda x: x["duration_minutes"] + (x.get("lowest_price") or 500) * 0.35,
        )

    valid_flights = [f for f in flights if f.get("lowest_price", 0) > 0]
    best_flight = None
    cheapest_flight = None
    fastest_flight = None
    if valid_flights:
        cheapest_flight = min(valid_flights, key=lambda x: x["lowest_price"])
        fastest_flight = min(valid_flights, key=lambda x: x["duration_minutes"])
        best_flight = min(
            valid_flights,
            key=lambda x: x["duration_minutes"] + x["lowest_price"] * 0.25,
        )

    train_d2d_mins = (best_train["duration_minutes"] + 70) if best_train else None
    train_d2d_cost = (
        round((best_train["lowest_price"] or 0) + 15, 0) if best_train else None
    )

    flight_d2d_mins = (best_flight["duration_minutes"] + 155) if best_flight else None
    flight_d2d_cost = (
        round((best_flight["lowest_price"] or 0) + 150, 0) if best_flight else None
    )

    recommended_mode = "train"
    reason = ""
    if best_train and best_flight:
        if train_d2d_mins <= flight_d2d_mins + 30 and train_d2d_cost <= flight_d2d_cost:
            recommended_mode = "train"
            reason = (
                f"高铁门到门约 {train_d2d_mins // 60}h{train_d2d_mins % 60}m（综合成本 ¥{int(train_d2d_cost)}），"
                f"比民航省 ¥{int(flight_d2d_cost - train_d2d_cost)} 且免去远郊机场值机折腾。"
            )
        elif flight_d2d_mins + 60 < train_d2d_mins:
            recommended_mode = "flight"
            reason = (
                f"民航门到门约 {flight_d2d_mins // 60}h{flight_d2d_mins % 60}m，"
                f"比高铁快 {(train_d2d_mins - flight_d2d_mins) // 60}h{(train_d2d_mins - flight_d2d_mins) % 60}m，"
                f"长途推荐直飞航班 {best_flight['flight_no']}。"
            )
        elif flight_d2d_cost < train_d2d_cost:
            recommended_mode = "flight"
            reason = f"特价机票 {best_flight['flight_no']} 仅 ¥{best_flight['lowest_price']}，含税总成本低于高铁。"
        else:
            recommended_mode = "train"
            reason = f"首选高铁 {best_train['train_code']}（{best_train['dep_time']}出发），市区直达市区。"
    elif best_train:
        recommended_mode = "train"
        reason = (
            f"直达铁路共 {len(trains)} 趟，优选 {best_train['train_code']} 次"
            f"（{best_train['from_station']} {best_train['dep_time']} → {best_train['to_station']} {best_train['arr_time']}，¥{best_train['lowest_price']}）。"
        )
    elif best_flight:
        recommended_mode = "flight"
        reason = f"推荐搭乘 {best_flight['flight_no']} 航班直飞（¥{best_flight['lowest_price']}）。"
    elif transfers:
        recommended_mode = "transfer"
        reason = f"直达票紧张，推荐经由【{transfers[0]['hub_city']}】中转联程。"

    return {
        "recommended_mode": recommended_mode,
        "recommendation_reason": reason,
        "train_option": {
            "available": bool(best_train),
            "best": best_train,
            "fastest": fastest_train,
            "cheapest": cheapest_train,
            "door_to_door_text": f"{train_d2d_mins // 60}h{train_d2d_mins % 60}m"
            if train_d2d_mins
            else "--",
            "estimated_total_cost": train_d2d_cost,
        },
        "flight_option": {
            "available": bool(best_flight),
            "best": best_flight,
            "fastest": fastest_flight,
            "cheapest": cheapest_flight,
            "door_to_door_text": f"{flight_d2d_mins // 60}h{flight_d2d_mins % 60}m"
            if flight_d2d_mins
            else "--",
            "estimated_total_cost": flight_d2d_cost,
        },
    }


def generate_chat_response(
    user_message: str,
    intent: Dict[str, Any],
    trains: List[Dict[str, Any]],
    flights: List[Dict[str, Any]],
    hotels: List[Dict[str, Any]],
    stopovers: List[Dict[str, Any]],
    transfers: List[Dict[str, Any]],
    comparison: Dict[str, Any],
    history: List[Dict[str, str]],
    preferred_model: str = "deepseek-v4.1-flash",
) -> Dict[str, Any]:
    """
    Generate natural conversational AI response grounded in live tool data.
    If intent['needs_itinerary'] is True, also asks the LLM to include a structured itinerary card.
    """
    from_city = intent.get("from_city", "")
    to_city = intent.get("to_city", "")
    hotel_city = intent.get("hotel_city") or to_city
    date_str = intent.get("date", "")
    needs_itinerary = intent.get("needs_itinerary", False)

    # Summarize live tool results for the LLM context
    trains_brief = [
        f"{t['train_code']}({t['from_station']} {t['dep_time']}->{t['to_station']} {t['arr_time']}, 历时{t['duration_text']}, 最低¥{t['lowest_price']}, 余票:{'有票' if t['can_buy'] else '候补/无'}, 席别:{'/'.join(s['name']+str(s['price'])+'元('+s['available']+')' for s in t['seats'][:3])})"
        for t in trains[:10]
    ]
    flights_brief = [
        f"{f['flight_no']} {f['airline_short']}({f['dep_airport_short']}{f['dep_terminal']} {f['dep_time']}->{f['arr_airport_short']}{f['arr_terminal']} {f['arr_time']}, 历时{f['duration_text']}, ¥{f['lowest_price']} {f['economy_desc']}, {f['meal']})"
        for f in flights[:8]
    ]
    hotels_brief = [
        f"{h['name']}({h['star']}星, 评分:{h['score']}{h['score_desc']}, 实时价:¥{h['price']}, 区域:{h['zone'] or h['position_desc']}, 评价:{h['quote']})"
        for h in hotels[:8]
    ]
    stops_brief = [
        f"{s.get('station_no')}.{s.get('station_name')}(到:{s.get('arrive_time')}, 发:{s.get('start_time')}, 停:{s.get('stopover_time')})"
        for s in stopovers
    ]

    hist_text = ""
    if history:
        recent = history[-4:]
        hist_text = "【近期对话上下文】\n" + "\n".join(
            f"{h.get('role', 'user')}: {h.get('content', '')[:200]}" for h in recent
        )

    if needs_itinerary:
        system_prompt = (
            "你是一个简洁、专业的个人票务、酒店与行程助手。系统已经在气泡上方渲染了实时车次/机票/酒店卡片组件。\n"
            "请严格基于下方提供的【实时12306车次】、【实时航班】与【实时酒店房价】真实数据，返回合法的纯 JSON 对象（不要多余代码块包裹）：\n"
            "{\n"
            '  "reply": "用自然亲切的口吻简明回答用户（100~150字），点出最推荐的具体真实车次/航班/酒店及原因",\n'
            '  "itinerary": {\n'
            '    "title": "行程标题 (如：玉山 ➔ 上海 · 2日周末精华游)",\n'
            '    "budget_total": 1200,\n'
            '    "hotel_tip": "点名推荐上方实时检出的具体酒店名称、实时房价及地铁换乘说明",\n'
            '    "days": [\n'
            '      {\n'
            '        "day": 1,\n'
            '        "date": "YYYY-MM-DD",\n'
            '        "theme": "当日主题",\n'
            '        "items": [\n'
            '          {"time": "08:40", "title": "真实车次/航班/真实酒店或景点餐厅名称", "location": "具体车站/地址", "detail": "简明贴士", "cost": 235}\n'
            '        ]\n'
            '      }\n'
            '    ]\n'
            '  }\n'
            "}"
        )
    else:
        system_prompt = (
            "你是一个极简、干练的个人票务、机票与酒店 AI 助手。系统已经在你的回复气泡里内嵌展示了【实时12306车次表/机票表/携程实时酒店房价表】可视化组件。\n"
            "因此你不需要把所有列表再机械罗列一遍，而是直接针对用户的提问，基于真实数据给出最实用的挑选建议（例如：高性价比选哪家、高分豪华选哪家、早班赶路选哪趟车/航班、最快或最省钱是哪个）。\n"
            "严禁编造数据！直接输出精炼、好读的 Markdown 文本（控制在 120~220 字以内，条理清晰）。"
        )

    user_prompt = (
        f"{hist_text}\n"
        f"【当前用户提问】：{user_message}\n"
        f"【解析路线参数】：{from_city} -> {to_city} (酒店城市: {hotel_city}) | 日期：{date_str} | 天数：{intent.get('days')}天\n"
        f"【12306 实时检出车次（共{len(trains)}趟，前10趟明细）】：{json.dumps(trains_brief, ensure_ascii=False)}\n"
        f"【OTA 实时检出航班（共{len(flights)}架次）】：{json.dumps(flights_brief, ensure_ascii=False)}\n"
        f"【携程/Trip.com 实时检出酒店（共{len(hotels)}家）】：{json.dumps(hotels_brief, ensure_ascii=False)}\n"
        + (f"【列车经停站时刻表】：{json.dumps(stops_brief, ensure_ascii=False)}\n" if stops_brief else "")
        + (f"【空铁比价结论】：{comparison.get('recommendation_reason', '')}\n" if comparison else "")
    )

    raw_text, actual_model, err = call_llm_raw(
        system_prompt, user_prompt, preferred_model=preferred_model, timeout=22
    )

    if not raw_text:
        return {
            "ok": False,
            "model": "error",
            "reply": f"⚠️ 大模型响应超时 ({err or '网络波动'})，但上方组件中已为您展示实时抓取到的 12306 与航班真实数据。",
            "itinerary": None,
        }

    if needs_itinerary:
        m = re.search(r"\{.*\}", raw_text, re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
                return {
                    "ok": True,
                    "model": actual_model,
                    "reply": parsed.get("reply") or "已为您结合实时班次生成以下行程安排：",
                    "itinerary": parsed.get("itinerary"),
                }
            except Exception:
                pass

    return {
        "ok": True,
        "model": actual_model,
        "reply": raw_text,
        "itinerary": None,
    }


def export_itinerary_to_ics(itinerary: Dict[str, Any]) -> str:
    """Convert structured itinerary JSON into standard RFC 5545 iCalendar (.ics) string."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TravelAgent//China Rail & Flight Smart Planner//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{itinerary.get('title', '个人智能行程单')}",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]

    now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    uid_counter = 1000

    for day_obj in itinerary.get("days", []):
        date_str = day_obj.get("date", "")
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            continue
        ymd = date_str.replace("-", "")

        for item in day_obj.get("items", []):
            uid_counter += 1
            t_str = item.get("time", "09:00")
            m_t = re.search(r"(\d{1,2}):(\d{2})", t_str)
            hh = int(m_t.group(1)) if m_t else 9
            mm = int(m_t.group(2)) if m_t else 0
            start_dt = f"{ymd}T{hh:02d}{mm:02d}00"
            end_hh = min(23, hh + 2)
            end_dt = f"{ymd}T{end_hh:02d}{mm:02d}00"

            title = item.get("title", "行程节点").replace("\n", " ")
            loc = item.get("location", "").replace("\n", " ")
            detail = item.get("detail", "").replace("\n", "\\n")
            cost = item.get("cost", 0)
            desc = f"{detail}\\n预计费用: ¥{cost}" if cost else detail

            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:travel-agent-{ymd}-{uid_counter}@local",
                    f"DTSTAMP:{now_utc}",
                    f"DTSTART;TZID=Asia/Shanghai:{start_dt}",
                    f"DTEND;TZID=Asia/Shanghai:{end_dt}",
                    f"SUMMARY:{title}",
                    f"LOCATION:{loc}",
                    f"DESCRIPTION:{desc}",
                    "END:VEVENT",
                ]
            )

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
