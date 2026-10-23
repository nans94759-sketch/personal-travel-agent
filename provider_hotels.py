"""
Open-Source Zero-Key Real-Time Hotel Scraper Provider
-----------------------------------------------------
Combines:
1. Ctrip H5 Open GlobalSearch API (`m.ctrip.com/restapi/h5api/globalsearch/search`)
   to dynamically resolve ANY Chinese city or county (including 玉山, 婺源, 阳朔, 延吉, etc.) to its Ctrip `cityId`.
2. Trip.com SSR Real-Time Hotel Engine (`sg.trip.com/hotels/list?locale=zh-SG&curr=CNY`)
   to extract live CNY room prices, tax-inclusive totals, star ratings, review scores,
   review counts, one-sentence guest reviews, and business zone / metro proximity
   without login or slider captchas.
3. Supports business zone filtering (`&zone={zoneId}`) when the user specifies an area
   keyword (e.g., "外滩", "静安寺", "西湖", "春熙路", "虹桥").
"""

import datetime
import json
import re
import subprocess
import urllib.parse
from typing import Any, Dict, List, Optional

try:
    from opencc import OpenCC
    _cc = OpenCC("t2s")
except Exception:
    _cc = None


def to_simplified(text: str) -> str:
    if not text:
        return ""
    return _cc.convert(text) if _cc else text


# Fast built-in lookup for top Chinese cities (fallback to live Ctrip globalsearch API for all others)
CTRIP_CITY_ID_MAP: Dict[str, int] = {
    "北京": 1,
    "上海": 2,
    "天津": 3,
    "重庆": 4,
    "哈尔滨": 5,
    "大连": 6,
    "青岛": 7,
    "西安": 10,
    "南京": 12,
    "苏州": 14,
    "杭州": 17,
    "厦门": 25,
    "成都": 28,
    "深圳": 30,
    "广州": 32,
    "桂林": 33,
    "昆明": 34,
    "三亚": 43,
    "海口": 42,
    "济南": 144,
    "武汉": 477,
    "长沙": 206,
    "郑州": 559,
    "沈阳": 451,
    "长春": 158,
    "合肥": 278,
    "南昌": 376,
    "福州": 258,
    "贵阳": 38,
    "南宁": 380,
    "拉萨": 41,
    "乌鲁木齐": 39,
    "兰州": 100,
    "西宁": 124,
    "银川": 99,
    "太原": 105,
    "石家庄": 428,
    "呼和浩特": 103,
    "丽江": 37,
    "大理": 36,
    "西双版纳": 35,
    "威海": 479,
    "烟台": 533,
    "泉州": 406,
    "珠海": 31,
    "宁波": 375,
    "无锡": 13,
    "温州": 491,
    "洛阳": 350,
    "开封": 331,
    "景德镇": 405,
    "上饶": 411,
    "玉山": 1001,
    "婺源": 458,
    "义乌": 536,
    "昆山": 46,
    "阳朔": 702,
    "延吉": 1123,
    "张家界": 27,
    "黄山": 23,
    "香港": 58,
    "澳门": 59,
    "台北": 617,
    "河内": 286,
    "胡志明市": 301,
    "岘港": 1356,
    "芽庄": 1359,
    "富国岛": 1446,
    "东京": 228,
    "大阪": 219,
    "京都": 734,
    "首尔": 274,
    "济州岛": 737,
    "曼谷": 359,
    "普吉岛": 725,
    "清迈": 623,
    "新加坡": 73,
    "吉隆坡": 315,
}


class HotelScraperClient:
    def resolve_city_id(self, city_name: str) -> Optional[int]:
        if not city_name:
            return None
        clean = re.sub(r"(市|县|区|站|地区|特别行政区)$", "", city_name.strip())
        if clean in CTRIP_CITY_ID_MAP:
            return CTRIP_CITY_ID_MAP[clean]
        if city_name.strip() in CTRIP_CITY_ID_MAP:
            return CTRIP_CITY_ID_MAP[city_name.strip()]

        # Live resolution via Ctrip Open GlobalSearch API
        try:
            url = (
                "https://m.ctrip.com/restapi/h5api/globalsearch/search"
                f"?action=online&source=globalonline&keyword={urllib.parse.quote(clean)}"
            )
            raw = subprocess.check_output(
                ["curl", "-sS", "--max-time", "6", url, "-H", "User-Agent: Mozilla/5.0"],
                timeout=8,
            ).decode("utf-8", errors="ignore")
            data = json.loads(raw)
            items = data.get("data", [])
            # Pass 1: Exact match on item['cityName'] or item['word']
            for item in items:
                cname = to_simplified(item.get("cityName") or "")
                word = to_simplified(item.get("word") or "")
                cid = item.get("cityId")
                if cid and int(cid) > 0 and (cname == clean or word == clean):
                    CTRIP_CITY_ID_MAP[clean] = int(cid)
                    return int(cid)
            # Pass 2: Any item with a valid cityId
            for item in items:
                cid = item.get("cityId") or (
                    item.get("id") if item.get("type") == "district" else 0
                )
                if cid and int(cid) > 0:
                    CTRIP_CITY_ID_MAP[clean] = int(cid)
                    return int(cid)
        except Exception:
            pass
        return None

    def query_hotels(
        self,
        city_name: str,
        checkin_date: str,
        checkout_date: Optional[str] = None,
        area_keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Query real-time hotels and live CNY room rates in `city_name` for `checkin_date`.
        If `area_keyword` is provided (e.g. "外滩", "静安寺", "西湖"), automatically matches
        the city's business zone ID or filters matching hotels.
        """
        city_id = self.resolve_city_id(city_name)
        if not city_id:
            return {
                "ok": False,
                "error": f"未能解析城市酒店编码: {city_name}",
                "hotels": [],
            }

        try:
            dt_in = datetime.date.fromisoformat(checkin_date)
        except Exception:
            dt_in = datetime.date.today() + datetime.timedelta(days=1)
            checkin_date = dt_in.strftime("%Y-%m-%d")

        if not checkout_date:
            dt_out = dt_in + datetime.timedelta(days=1)
            checkout_date = dt_out.strftime("%Y-%m-%d")

        cin_slash = checkin_date.replace("-", "/")
        cout_slash = checkout_date.replace("-", "/")

        base_url = (
            f"https://sg.trip.com/hotels/list?city={city_id}"
            f"&checkin={cin_slash}&checkout={cout_slash}"
            f"&locale=zh-SG&curr=CNY"
        )

        full_str = self._fetch_ssr_full_str(base_url)
        if not full_str:
            return {
                "ok": False,
                "error": "酒店实时页面加载超时",
                "city": city_name,
                "checkin": checkin_date,
                "checkout": checkout_date,
                "hotels": [],
            }

        matched_zone_name = None
        if area_keyword:
            kw_clean = re.sub(
                r"(附近|周边|旁边|商圈|地区|地铁站|站|的|酒店|宾馆|住宿|高分|便宜)+$",
                "",
                area_keyword.strip(),
            ).strip()
            if kw_clean and kw_clean != city_name:
                filter_param, zone_title = self._find_area_filter_in_ssr(full_str, kw_clean)
                if filter_param:
                    matched_zone_name = zone_title
                    zone_str = self._fetch_ssr_full_str(f"{base_url}&{filter_param}")
                    if zone_str and '"hotelList":[' in zone_str:
                        full_str = zone_str
                else:
                    # Fallback to direct &keyword= search on sg.trip.com
                    matched_zone_name = kw_clean
                    kw_str = self._fetch_ssr_full_str(
                        f"{base_url}&keyword={urllib.parse.quote(kw_clean)}"
                    )
                    if kw_str and '"hotelList":[' in kw_str:
                        full_str = kw_str

        hotels = self._parse_hotels_from_ssr(full_str)
        return {
            "ok": True,
            "source": "携程/Trip.com 实时房价引擎 (CNY)",
            "city": city_name,
            "city_id": city_id,
            "zone": matched_zone_name or area_keyword or "全城热门",
            "checkin": checkin_date,
            "checkout": checkout_date,
            "count": len(hotels),
            "hotels": hotels,
        }

    @staticmethod
    def _fetch_ssr_full_str(url: str) -> str:
        import time
        for attempt in range(3):
            try:
                html = subprocess.check_output(
                    [
                        "curl",
                        "-sS",
                        "--tlsv1.2",
                        "--tls-max",
                        "1.2",
                        "--http1.1",
                        "--max-time",
                        "12",
                        url,
                        "-H",
                        "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                    ],
                    timeout=14,
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="ignore")
                chunks = []
                for m in re.finditer(r"self\.__next_f\.push\(\[1,(\".*?\")\]\)", html, re.S):
                    try:
                        chunks.append(json.loads(m.group(1)))
                    except Exception:
                        pass
                res = "".join(chunks)
                if res and '"hotelList":[' in res:
                    return res
            except Exception:
                pass
            time.sleep(0.3 * (attempt + 1))
        return ""

    @staticmethod
    def _find_area_filter_in_ssr(full_str: str, keyword: str) -> tuple[Optional[str], Optional[str]]:
        """
        Match area/landmark/airport filter inside SSR filter list:
        - Business zone (`filterID: "8|118"`) -> `zone=118`
        - Airport/Station (`filterID: "10|4325913"`) -> `filterId=10%7C4325913`
        - Landmark (`filterID: "13|6790577"`) -> `filterId=13%7C6790577`
        """
        for m in re.finditer(
            r'"title":"([^"]+)","value":"[^"]+","filterID":"((?:8|10|13)\|\d+)"', full_str
        ):
            raw_title = to_simplified(m.group(1))
            fid = m.group(2)
            if keyword in raw_title or raw_title in keyword:
                if fid.startswith("8|"):
                    return f"zone={fid.split('|')[1]}", raw_title
                return f"filterId={urllib.parse.quote(fid)}", raw_title
        return None, None

    @staticmethod
    def _parse_hotels_from_ssr(full_str: str) -> List[Dict[str, Any]]:
        idx = full_str.find('"hotelList":[')
        if idx == -1:
            return []
        try:
            arr, _ = json.JSONDecoder().raw_decode(full_str[idx + len('"hotelList":') :])
        except Exception:
            return []

        hotels: List[Dict[str, Any]] = []
        for item in arr:
            hi = item.get("hotelInfo") or {}
            ri_list = item.get("roomInfo") or []
            ri = ri_list[0] if ri_list else {}
            pi = ri.get("priceInfo") or {}

            name = to_simplified((hi.get("nameInfo") or {}).get("name", ""))
            if not name:
                continue

            star = (hi.get("hotelStar") or {}).get("star", 0)
            comment = hi.get("commentInfo") or {}
            score = comment.get("commentScore", "")
            score_desc = to_simplified(comment.get("commentDescription", ""))
            review_count = to_simplified(comment.get("commenterNumber", ""))
            one_sentence = ""
            osc = comment.get("oneSentenceComment") or []
            if osc and isinstance(osc[0], dict):
                one_sentence = to_simplified(osc[0].get("tagTitle", "")).strip('"')

            pos = hi.get("positionInfo") or {}
            pos_desc = to_simplified(pos.get("positionDesc", ""))
            address = to_simplified(pos.get("address", ""))
            zones = [to_simplified(z) for z in (pos.get("zoneNames") or [])]

            price = pi.get("price") or 0
            delete_price = pi.get("deletePrice") or 0
            tax_note = to_simplified(pi.get("priceExplanation", ""))

            hotels.append(
                {
                    "name": name,
                    "star": star,
                    "score": score,
                    "score_desc": score_desc,
                    "review_count": review_count,
                    "quote": one_sentence,
                    "position_desc": pos_desc,
                    "address": address,
                    "zone": zones[0] if zones else "",
                    "price": price,
                    "original_price": delete_price if delete_price > price else None,
                    "tax_note": tax_note,
                }
            )

        return hotels
