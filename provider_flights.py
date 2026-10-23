"""
Open-Source Zero-Key Flight Scraper Provider
--------------------------------------------
Fetches live domestic flight schedules, real-time cabin discounts, aircraft types,
meal info, and on-time rates via Tongcheng (ly.com) SSR Nuxt state + city IATA resolver,
with zero API keys required.
"""

import json
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from provider_12306 import Train12306Client

_rail_helper = Train12306Client()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Common Chinese city -> IATA city code dictionary for instant resolution
CITY_IATA_MAP: Dict[str, Dict[str, str]] = {
    "北京": {"city": "北京", "code": "BJS"},
    "上海": {"city": "上海", "code": "SHA"},
    "广州": {"city": "广州", "code": "CAN"},
    "深圳": {"city": "深圳", "code": "SZX"},
    "成都": {"city": "成都", "code": "CTU"},
    "杭州": {"city": "杭州", "code": "HGH"},
    "重庆": {"city": "重庆", "code": "CKG"},
    "西安": {"city": "西安", "code": "SIA"},
    "武汉": {"city": "武汉", "code": "WUH"},
    "长沙": {"city": "长沙", "code": "CSX"},
    "南京": {"city": "南京", "code": "NKG"},
    "厦门": {"city": "厦门", "code": "XMN"},
    "昆明": {"city": "昆明", "code": "KMG"},
    "三亚": {"city": "三亚", "code": "SYX"},
    "海口": {"city": "海口", "code": "HAK"},
    "青岛": {"city": "青岛", "code": "TAO"},
    "大连": {"city": "大连", "code": "DLC"},
    "哈尔滨": {"city": "哈尔滨", "code": "HRB"},
    "沈阳": {"city": "沈阳", "code": "SHE"},
    "长春": {"city": "长春", "code": "CGQ"},
    "郑州": {"city": "郑州", "code": "CGO"},
    "天津": {"city": "天津", "code": "TSN"},
    "乌鲁木齐": {"city": "乌鲁木齐", "code": "URC"},
    "拉萨": {"city": "拉萨", "code": "LXA"},
    "贵阳": {"city": "贵阳", "code": "KWE"},
    "桂林": {"city": "桂林", "code": "KWL"},
    "南宁": {"city": "南宁", "code": "NNG"},
    "福州": {"city": "福州", "code": "FOC"},
    "济南": {"city": "济南", "code": "TNA"},
    "太原": {"city": "太原", "code": "TYN"},
    "合肥": {"city": "合肥", "code": "HFE"},
    "南昌": {"city": "南昌", "code": "KHN"},
    "兰州": {"city": "兰州", "code": "LHW"},
    "西宁": {"city": "西宁", "code": "XNN"},
    "银川": {"city": "银川", "code": "INC"},
    "呼和浩特": {"city": "呼和浩特", "code": "HET"},
    "丽江": {"city": "丽江", "code": "LJG"},
    "大理": {"city": "大理", "code": "DLU"},
    "西双版纳": {"city": "西双版纳", "code": "JHG"},
    "珠海": {"city": "珠海", "code": "ZUH"},
    "宁波": {"city": "宁波", "code": "NGB"},
    "温州": {"city": "温州", "code": "WNZ"},
    "无锡": {"city": "无锡", "code": "WUX"},
    "烟台": {"city": "烟台", "code": "YNT"},
    "泉州": {"city": "泉州", "code": "JJN"},
    "汕头": {"city": "汕头", "code": "SWA"},
    "揭阳": {"city": "揭阳", "code": "SWA"},
    "敦煌": {"city": "敦煌", "code": "DNH"},
    "张家界": {"city": "张家界", "code": "DYG"},
    "桂林": {"city": "桂林", "code": "KWL"},
    "威海": {"city": "威海", "code": "WEH"},
}


class FlightScraperClient:
    def __init__(self) -> None:
        self.ssl_ctx = ssl.create_default_context()

    def resolve_city_code(self, city_query: str) -> Optional[Dict[str, str]]:
        if not city_query:
            return None
        q = re.sub(r"(市|地区|特别行政区|国际机场|机场|站|县|区)$", "", city_query.strip())
        if q in CITY_IATA_MAP:
            return CITY_IATA_MAP[q]

        # If q is a county/station in 12306 (e.g. 玉山 -> prefecture city 上饶), also try its prefecture city
        st_info = _rail_helper.resolve_station(q)
        prefecture_city = st_info.get("city") if st_info else None
        if prefecture_city and prefecture_city in CITY_IATA_MAP:
            return CITY_IATA_MAP[prefecture_city]

        candidates = [q]
        if prefecture_city and prefecture_city != q:
            candidates.append(prefecture_city)

        # Live lookup via ly.com queryflightnearbylist
        for cand in candidates:
            try:
                payload = {
                    "keyword": cand,
                    "lon": 1,
                    "lat": 1,
                    "loccounty": "中国",
                    "rescitytp": 1,
                    "calltype": "",
                }
                req = urllib.request.Request(
                    "https://www.ly.com/flights/api/home/queryflightnearbylist",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={
                        "User-Agent": USER_AGENT,
                        "Content-Type": "application/json",
                        "Referer": "https://www.ly.com/flights/home",
                    },
                )
                with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=8) as resp:
                    res = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    rl = res.get("body", {}).get("rl", [])
                    if rl:
                        item = rl[0]
                        code = item.get("cityCode") or item.get("code")
                        name = item.get("cityName") or item.get("name") or cand
                        if code:
                            info = {"city": name, "code": code.upper()}
                            CITY_IATA_MAP[q] = info
                            return info
            except Exception:
                continue
        return None

    def query_flights(
        self, from_city: str, to_city: str, flight_date: str
    ) -> Dict[str, Any]:
        """
        Scrape real-time flights for from_city -> to_city on flight_date (YYYY-MM-DD)
        from Tongcheng SSR window.__NUXT__ state.
        """
        dep_info = self.resolve_city_code(from_city)
        arr_info = self.resolve_city_code(to_city)
        if not dep_info or not arr_info:
            return {
                "ok": False,
                "error": f"未能解析城市机场三字码: {from_city} -> {to_city}",
                "flights": [],
            }

        dep_code = dep_info["code"]
        arr_code = arr_info["code"]
        dep_name = dep_info["city"]
        arr_name = arr_info["city"]

        url = (
            f"https://www.ly.com/flights/itinerary/oneway/{dep_code}-{arr_code}"
            f"?date={flight_date}"
            f"&from={urllib.parse.quote(dep_name)}"
            f"&to={urllib.parse.quote(arr_name)}"
        )

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, context=self.ssl_ctx, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            m = re.search(r"window\.__NUXT__\s*=\s*(.*?);</script>", html, re.S)
            if not m:
                return {
                    "ok": False,
                    "error": "未从页面提取到实时航班状态数据",
                    "from_city": dep_info,
                    "to_city": arr_info,
                    "date": flight_date,
                    "flights": [],
                }

            js_expr = m.group(1)
            node_script = (
                f"const nuxt = {js_expr}; "
                "console.log(JSON.stringify((nuxt.state && nuxt.state.book1 && nuxt.state.book1.flightLists) || []));"
            )
            raw_out = subprocess.check_output(
                ["node", "-e", node_script], timeout=6
            ).decode("utf-8", errors="ignore")
            raw_flights = json.loads(raw_out)

            flights = self._normalize_flights(raw_flights)
            return {
                "ok": True,
                "source": "同程机票 SSR 实时航班引擎 (ly.com)",
                "from_city": dep_info,
                "to_city": arr_info,
                "date": flight_date,
                "count": len(flights),
                "flights": flights,
            }
        except Exception as e:
            return {
                "ok": False,
                "error": f"实时航班抓取异常: {str(e)}",
                "from_city": dep_info,
                "to_city": arr_info,
                "date": flight_date,
                "flights": [],
            }

    @staticmethod
    def _normalize_flights(raw_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in raw_list:
            flight_no = item.get("flightNo", "")
            if not flight_no:
                continue

            dep_time = item.get("flyOffOnlyTime", "")
            arr_time = item.get("arrivalOnlyTime", "")
            span_str = item.get("spantime", "")

            # Parse duration in minutes
            dur_mins = 0
            hm = re.search(r"(\d+)\s*小时", span_str)
            mm = re.search(r"(\d+)\s*分", span_str)
            if hm:
                dur_mins += int(hm.group(1)) * 60
            if mm:
                dur_mins += int(mm.group(1))
            if dur_mins == 0:
                dur_mins = 150

            lowest_price = item.get("flightPrice") or item.get("lcp") or 0
            try:
                lowest_price = int(lowest_price)
            except Exception:
                lowest_price = 0

            # Extract cabins (economy / business)
            cabins = []
            for c in item.get("cfts", []):
                try:
                    c_price = int(c.get("ap") or c.get("cp") or 0)
                except Exception:
                    c_price = 0
                if c_price > 0:
                    cabins.append(
                        {
                            "desc": c.get("rd", "经济舱"),
                            "discount": f"{c.get('adr', '')}折" if c.get("adr") else "",
                            "price": c_price,
                            "cabin_code": c.get("iv", ""),
                        }
                    )

            sts = item.get("sts") or {}
            normalized.append(
                {
                    "flight_no": flight_no,
                    "airline_code": item.get("airCompanyCode", ""),
                    "airline_name": item.get("airCompanyName", ""),
                    "airline_short": item.get("awmsn", item.get("airCompanyName", "")),
                    "aircraft": item.get("equipmentName")
                    or (item.get("am") or {}).get("n", ""),
                    "dep_airport": item.get("oapname", ""),
                    "dep_airport_short": item.get("otsn") or item.get("oasns", ""),
                    "dep_airport_code": item.get("originAirportCode", ""),
                    "dep_terminal": item.get("offPoint", ""),
                    "arr_airport": item.get("aapname", ""),
                    "arr_airport_short": item.get("atsn") or item.get("aasns", ""),
                    "arr_airport_code": item.get("arriveAirportCode", ""),
                    "arr_terminal": item.get("boardPoint", ""),
                    "dep_time": dep_time,
                    "arr_time": arr_time,
                    "duration_text": span_str,
                    "duration_minutes": dur_mins,
                    "lowest_price": lowest_price,
                    "economy_desc": item.get("lcd", "经济舱"),
                    "discount_rate": item.get("ldr", ""),
                    "business_price": item.get("lbcp") or None,
                    "business_desc": item.get("lbcd", ""),
                    "meal": item.get("mfgd") or sts.get("mfg") or "无餐食",
                    "ontime_rate": sts.get("fRate")
                    or (f"准点率{item.get('fRate')}%" if item.get("fRate") else ""),
                    "aircraft_age": sts.get("fYear", ""),
                    "bridge_rate": sts.get("bridge", ""),
                    "stop_num": item.get("stopNum", 0),
                    "cabins": cabins,
                }
            )

        normalized.sort(key=lambda x: (x["lowest_price"] if x["lowest_price"] > 0 else 99999, x["dep_time"]))
        return normalized
