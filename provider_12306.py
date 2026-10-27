"""
12306 Live Ticket & Station Provider
------------------------------------
Zero-key open-source reverse-engineered 12306 client:
- Auto-fetches & caches 3300+ station telecodes from kyfw.12306.cn
- Dynamically detects CLeftTicketUrl (e.g. leftTicket/queryG, queryZ)
- Parses 57-field pipe-separated raw ticket records
- Decodes embedded seat prices from field[39] (zero extra HTTP calls)
- Queries stopover timetables via czxx/queryByTrainNo
- Smart multi-hub transfer (中转联程) computation across major national rail hubs
"""

import concurrent.futures
import datetime
import email.utils
import http.cookiejar
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)
STATION_CACHE_FILE = os.path.join(CACHE_DIR, "12306_stations.json")
BUNDLED_STATION_FILE = os.path.join(BASE_DIR, "data", "12306_stations.json")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Seat type code -> (Seat Name, Field Index in 12306 pipe array)
SEAT_CODE_MAP: Dict[str, Tuple[str, int]] = {
    "9": ("商务座", 32),
    "P": ("特等座", 25),
    "M": ("一等座", 31),
    "O": ("二等座", 30),
    "6": ("高级软卧", 21),
    "4": ("软卧/一等卧", 23),
    "I": ("一等卧", 23),
    "F": ("动卧", 33),
    "3": ("硬卧/二等卧", 28),
    "J": ("二等卧", 28),
    "2": ("软座", 24),
    "1": ("硬座", 29),
    "W": ("无座", 26),
}

# Major national high-speed rail transfer hubs for smart 1-stop transfer calculation
NATIONAL_HUBS = [
    ("上海", "SHH"),
    ("杭州", "HZH"),
    ("南京", "NJH"),
    ("武汉", "WHN"),
    ("南昌", "NCG"),
    ("长沙", "CSQ"),
    ("郑州", "ZZF"),
    ("广州", "GZQ"),
    ("徐州", "XCH"),
    ("北京", "BJP"),
    ("西安", "XAY"),
    ("成都", "CDW"),
    ("沈阳", "SYT"),
]


class Train12306Client:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.ssl_ctx = ssl._create_unverified_context()
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cj),
            urllib.request.HTTPSHandler(context=self.ssl_ctx),
        )
        self.query_url_path = "leftTicket/queryG"
        self.last_init_ts = 0.0
        self.stations_by_name: Dict[str, Dict[str, str]] = {}
        self.stations_by_code: Dict[str, Dict[str, str]] = {}
        self.city_to_main_station: Dict[str, str] = {}
        self._load_stations()

    def _load_stations(self) -> None:
        """Load 12306 station dictionary from cache, bundled data, or live fetch."""
        for candidate_file, max_age in [
            (STATION_CACHE_FILE, 86400 * 7),
            (BUNDLED_STATION_FILE, 86400 * 3650),
        ]:
            if os.path.exists(candidate_file) and (
                time.time() - os.path.getmtime(candidate_file) < max_age
            ):
                try:
                    with open(candidate_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    self.stations_by_name = cached.get("by_name", {})
                    self.stations_by_code = cached.get("by_code", {})
                    self.city_to_main_station = cached.get("city_main", {})
                    if self.stations_by_name:
                        return
                except Exception:
                    pass

        raw_js = ""
        try:
            req = urllib.request.Request(
                "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js",
                headers={"User-Agent": USER_AGENT},
            )
            with self.opener.open(req, timeout=10) as resp:
                raw_js = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"[12306] Warning: failed to fetch station_name.js: {e}")

        if raw_js:
            # Format: @bjb|北京北|VAP|beijingbei|bjb|0|0357|北京|||
            items = raw_js.split("@")[1:]
            for item in items:
                parts = item.split("|")
                if len(parts) >= 8:
                    name = parts[1].strip()
                    code = parts[2].strip()
                    pinyin = parts[3].strip()
                    short_py = parts[4].strip()
                    city = parts[7].strip() or name
                    info = {
                        "name": name,
                        "code": code,
                        "pinyin": pinyin,
                        "short_py": short_py,
                        "city": city,
                    }
                    self.stations_by_name[name] = info
                    self.stations_by_code[code] = info
                    # If city exact match or first seen for city
                    if name == city or city not in self.city_to_main_station:
                        self.city_to_main_station[city] = code

            try:
                tmp_file = f"{STATION_CACHE_FILE}.{os.getpid()}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "by_name": self.stations_by_name,
                            "by_code": self.stations_by_code,
                            "city_main": self.city_to_main_station,
                        },
                        f,
                        ensure_ascii=False,
                    )
                os.replace(tmp_file, STATION_CACHE_FILE)
            except Exception:
                pass

    def resolve_station(self, query: str) -> Optional[Dict[str, str]]:
        """Resolve city name, station name, or telecode to 12306 station dict."""
        if not query:
            return None
        q = query.strip()
        # Strip trailing 市 / 站 if needed
        q_clean = re.sub(r"(市|站|地区|特别行政区)$", "", q)

        if q.upper() in self.stations_by_code:
            return self.stations_by_code[q.upper()]
        if q in self.stations_by_name:
            return self.stations_by_name[q]
        if q_clean in self.stations_by_name:
            return self.stations_by_name[q_clean]
        if q_clean in self.city_to_main_station:
            code = self.city_to_main_station[q_clean]
            return self.stations_by_code.get(code)

        # Prefix match
        for name, info in self.stations_by_name.items():
            if name.startswith(q_clean) or info["city"].startswith(q_clean):
                return info
        return None

    def _ensure_session(self) -> None:
        if time.time() - self.last_init_ts < 600:
            return
        with self._lock:
            if time.time() - self.last_init_ts < 600:
                return
            try:
                req = urllib.request.Request(
                    "https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc",
                    headers={"User-Agent": USER_AGENT},
                )
                with self.opener.open(req, timeout=8) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                    m = re.search(r"var\s+CLeftTicketUrl\s*=\s*['\"]([^'\"]+)['\"]", html)
                    if m:
                        self.query_url_path = m.group(1)
                    self.last_init_ts = time.time()
            except Exception:
                self.query_url_path = "leftTicket/queryG"

    @staticmethod
    def decode_field39_prices(
        field39: str, f_array: List[str]
    ) -> Tuple[List[Dict[str, Any]], Optional[float]]:
        """
        Decode 12306 field[39] (infoAll_list) 10-char chunks:
        [0]: seat_code
        [1:6]: price in 0.1 RMB (e.g. '05530' -> 553.0)
        [6:10]: flag/quantity
        """
        seats: List[Dict[str, Any]] = []
        seen_names = set()
        lowest_price: Optional[float] = None

        if field39 and len(field39) >= 10:
            for i in range(0, len(field39) - 9, 10):
                chunk = field39[i : i + 10]
                seat_code = chunk[0]
                price_raw = chunk[1:6]
                flag_raw = chunk[6:10]
                if not price_raw.isdigit():
                    continue
                price_val = round(int(price_raw) / 10.0, 1)
                if price_val <= 0:
                    continue

                # Determine seat name and availability
                if flag_raw.startswith("3"):
                    seat_name = "无座"
                    avail = f_array[26] if len(f_array) > 26 else ""
                elif seat_code in SEAT_CODE_MAP:
                    seat_name, idx = SEAT_CODE_MAP[seat_code]
                    avail = f_array[idx] if len(f_array) > idx else ""
                else:
                    continue

                avail_str = avail.strip() if avail else "无"
                if not avail_str or avail_str == "--":
                    avail_str = "无"

                if seat_name in seen_names:
                    continue
                seen_names.add(seat_name)

                has_ticket = avail_str not in ("无", "0", "*", "--", "")
                seats.append(
                    {
                        "name": seat_name,
                        "code": seat_code,
                        "price": price_val,
                        "available": avail_str,
                        "bookable": has_ticket,
                    }
                )
                if lowest_price is None or price_val < lowest_price:
                    lowest_price = price_val

        # Fallback if field39 was empty
        if not seats:
            fallback_pairs = [
                ("二等座", 30),
                ("一等座", 31),
                ("商务座", 32),
                ("硬卧", 28),
                ("软卧", 23),
                ("硬座", 29),
                ("无座", 26),
            ]
            for sname, idx in fallback_pairs:
                if len(f_array) > idx and f_array[idx] and f_array[idx] != "--":
                    avail_str = f_array[idx]
                    seats.append(
                        {
                            "name": sname,
                            "code": "",
                            "price": None,
                            "available": avail_str,
                            "bookable": avail_str not in ("无", "0", "*"),
                        }
                    )

        return seats, lowest_price

    def query_trains(
        self,
        from_city: str,
        to_city: str,
        train_date: str,
        only_high_speed: bool = False,
    ) -> Dict[str, Any]:
        """Query live 12306 tickets between from_city and to_city on train_date (YYYY-MM-DD)."""
        self._ensure_session()
        from_st = self.resolve_station(from_city)
        to_st = self.resolve_station(to_city)
        if not from_st or not to_st:
            return {
                "ok": False,
                "error": f"未能识别车站简码: {from_city} -> {to_city}",
                "trains": [],
            }

        from_code = from_st["code"]
        to_code = to_st["code"]

        cookie_from = urllib.parse.quote(f"{from_st['name']},{from_code}")
        cookie_to = urllib.parse.quote(f"{to_st['name']},{to_code}")

        for path_candidate in [self.query_url_path, "leftTicket/queryG", "leftTicket/queryZ", "leftTicket/query"]:
            url = (
                f"https://kyfw.12306.cn/otn/{path_candidate}"
                f"?leftTicketDTO.train_date={train_date}"
                f"&leftTicketDTO.from_station={from_code}"
                f"&leftTicketDTO.to_station={to_code}"
                f"&purpose_codes=ADULT"
            )
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc",
                    "Cookie": f"_jc_save_fromStation={cookie_from}; _jc_save_toStation={cookie_to}",
                },
            )
            try:
                with self.opener.open(req, timeout=10) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                data = json.loads(raw)
                if data.get("status") and "data" in data:
                    self.query_url_path = path_candidate
                    return self._parse_ticket_response(
                        data["data"], from_st, to_st, train_date, only_high_speed
                    )
            except Exception:
                continue

        return {
            "ok": False,
            "error": "12306 接口暂时繁忙或日期超出预售期（15天内）",
            "from_station": from_st,
            "to_station": to_st,
            "date": train_date,
            "trains": [],
        }

    def _parse_ticket_response(
        self,
        data_Node: Dict[str, Any],
        from_st: Dict[str, str],
        to_st: Dict[str, str],
        train_date: str,
        only_high_speed: bool,
    ) -> Dict[str, Any]:
        station_map = data_Node.get("map", {})
        raw_list = data_Node.get("result", [])
        trains = []

        for item in raw_list:
            f = item.split("|")
            if len(f) < 35:
                continue
            train_code = f[3]
            if only_high_speed and not train_code.startswith(("G", "D", "C")):
                continue

            train_no = f[2]
            start_st_code = f[4]
            end_st_code = f[5]
            dep_st_code = f[6]
            arr_st_code = f[7]
            dep_time = f[8]
            arr_time = f[9]
            duration_str = f[10]
            can_buy = f[11] == "Y"
            field39 = f[39] if len(f) > 39 else ""

            # Parse duration in minutes
            dur_parts = duration_str.split(":")
            duration_minutes = (
                int(dur_parts[0]) * 60 + int(dur_parts[1])
                if len(dur_parts) == 2 and dur_parts[0].isdigit()
                else 9999
            )

            seats, lowest_price = self.decode_field39_prices(field39, f)

            # Calculate day difference
            day_diff = 0
            try:
                dh, dm = map(int, dep_time.split(":"))
                L_h, L_m = map(int, dur_parts)
                total_m = dh * 60 + dm + L_h * 60 + L_m
                day_diff = total_m // (24 * 60)
            except Exception:
                pass

            train_type = "高铁/动车" if train_code.startswith(("G", "D", "C")) else "普速列车"
            if train_code.startswith("G"):
                train_type_badge = "复兴号/高铁"
            elif train_code.startswith("D"):
                train_type_badge = "动车组"
            elif train_code.startswith("C"):
                train_type_badge = "城际高铁"
            elif train_code.startswith("Z"):
                train_type_badge = "直达特快"
            elif train_code.startswith("T"):
                train_type_badge = "特快"
            else:
                train_type_badge = "快速/普快"

            trains.append(
                {
                    "train_no": train_no,
                    "train_code": train_code,
                    "train_type": train_type,
                    "train_type_badge": train_type_badge,
                    "from_station": station_map.get(dep_st_code, dep_st_code),
                    "from_station_code": dep_st_code,
                    "to_station": station_map.get(arr_st_code, arr_st_code),
                    "to_station_code": arr_st_code,
                    "start_station": self.stations_by_code.get(start_st_code, {}).get(
                        "name", start_st_code
                    ),
                    "end_station": self.stations_by_code.get(end_st_code, {}).get(
                        "name", end_st_code
                    ),
                    "is_origin": start_st_code == dep_st_code,
                    "is_terminal": end_st_code == arr_st_code,
                    "dep_time": dep_time,
                    "arr_time": arr_time,
                    "day_diff": day_diff,
                    "duration": duration_str,
                    "duration_text": f"{int(dur_parts[0])}小时{int(dur_parts[1])}分"
                    if len(dur_parts) == 2
                    else duration_str,
                    "duration_minutes": duration_minutes,
                    "can_buy": can_buy,
                    "lowest_price": lowest_price,
                    "seats": seats,
                    "has_fuxing": len(f) > 46 and "1" in f[46],
                    "is_quiet_car": len(f) > 54 and "j" in f[54].lower(),
                }
            )

        return {
            "ok": True,
            "source": "12306 官方实时接口 (kyfw.12306.cn)",
            "from_station": from_st,
            "to_station": to_st,
            "date": train_date,
            "count": len(trains),
            "trains": trains,
        }

    def query_stopovers(
        self, train_no: str, from_code: str, to_code: str, train_date: str
    ) -> List[Dict[str, Any]]:
        """Query stopover timetable for a specific train via czxx/queryByTrainNo."""
        url = (
            f"https://kyfw.12306.cn/otn/czxx/queryByTrainNo"
            f"?train_no={train_no}"
            f"&from_station_telecode={from_code}"
            f"&to_station_telecode={to_code}"
            f"&depart_date={train_date}"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc",
            },
        )
        try:
            with self.opener.open(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                return data.get("data", {}).get("data", [])
        except Exception:
            return []

    def query_smart_transfers(
        self, from_city: str, to_city: str, train_date: str, max_hubs: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Compute smart 1-stop high-speed rail transfers across national hubs
        in parallel when direct trains are scarce or sold out.
        """
        from_st = self.resolve_station(from_city)
        to_st = self.resolve_station(to_city)
        if not from_st or not to_st:
            return []

        candidate_hubs = [
            (h_name, h_code)
            for h_name, h_code in NATIONAL_HUBS
            if h_code not in (from_st["code"], to_st["code"])
            and h_name not in (from_st["city"], to_st["city"])
        ][:max_hubs]

        def _fetch_hub_legs(hub_name: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as sub_pool:
                f1 = sub_pool.submit(self.query_trains, from_city, hub_name, train_date, True)
                f2 = sub_pool.submit(self.query_trains, hub_name, to_city, train_date, True)
                r1 = f1.result()
                r2 = f2.result()
            l1 = [t for t in r1.get("trains", []) if t.get("can_buy")]
            l2 = [t for t in r2.get("trains", []) if t.get("can_buy")]
            return hub_name, l1, l2

        transfer_plans: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(candidate_hubs))) as pool:
            hub_results = list(pool.map(lambda item: _fetch_hub_legs(item[0]), candidate_hubs))

        for hub_name, leg1_trains, leg2_trains in hub_results:
            if not leg1_trains or not leg2_trains:
                continue

            for t1 in leg1_trains[:8]:
                if t1["day_diff"] > 0:
                    continue
                arr1_h, arr1_m = map(int, t1["arr_time"].split(":"))
                arr1_mins = arr1_h * 60 + arr1_m

                for t2 in leg2_trains:
                    if t1["train_code"] == t2["train_code"]:
                        continue
                    dep2_h, dep2_m = map(int, t2["dep_time"].split(":"))
                    dep2_mins = dep2_h * 60 + dep2_m
                    wait_mins = dep2_mins - arr1_mins
                    same_station = t1["to_station"] == t2["from_station"]
                    min_wait = 25 if same_station else 70
                    if min_wait <= wait_mins <= 180:
                        total_mins = t1["duration_minutes"] + wait_mins + t2["duration_minutes"]
                        total_price = (t1["lowest_price"] or 0) + (t2["lowest_price"] or 0)
                        wait_str = (
                            f"{wait_mins // 60}小时{wait_mins % 60}分"
                            if wait_mins >= 60
                            else f"{wait_mins}分钟"
                        )
                        transfer_plans.append(
                            {
                                "transfer_type": "rail_rail",
                                "badge_text": "同站换乘 · 高铁中转" if same_station else "同城换乘 · 高铁中转",
                                "hub_city": hub_name,
                                "transfer_station": t1["to_station"]
                                if same_station
                                else f"{t1['to_station']} ➔ {t2['from_station']}",
                                "same_station": same_station,
                                "wait_minutes": wait_mins,
                                "wait_text": f"{'同站换乘' if same_station else '异站换乘'} · 停留 {wait_str}",
                                "total_minutes": total_mins,
                                "total_duration_text": f"{total_mins // 60}小时{total_mins % 60}分",
                                "total_lowest_price": round(total_price, 1),
                                "dep_time": t1["dep_time"],
                                "arr_time": t2["arr_time"],
                                "arr_time_local": t2["arr_time"],
                                "arr_time_beijing": t2["arr_time"],
                                "dep_tz_offset": 8,
                                "arr_tz_offset": 8,
                                "leg1": {
                                    **t1,
                                    "mode": "train",
                                    "code": t1["train_code"],
                                    "type_badge": t1.get("train_type_badge", "高铁"),
                                    "seat_desc": f"¥{t1['lowest_price']} 起" if t1.get("lowest_price") else "有票",
                                },
                                "leg2": {
                                    **t2,
                                    "mode": "train",
                                    "code": t2["train_code"],
                                    "type_badge": t2.get("train_type_badge", "高铁"),
                                    "seat_desc": f"¥{t2['lowest_price']} 起" if t2.get("lowest_price") else "有票",
                                },
                            }
                        )
                        break
                if len(transfer_plans) >= 4:
                    break

        transfer_plans.sort(key=lambda x: (x["total_lowest_price"] or 9999, x["total_minutes"]))
        return transfer_plans[:4]
