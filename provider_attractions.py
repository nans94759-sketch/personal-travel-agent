"""
Open-Source Zero-Key Global Attraction Tickets & Sightseeing Provider
---------------------------------------------------------------------
Combines:
1. Ctrip GlobalSearch Open API (`m.ctrip.com/restapi/h5api/globalsearch/search`)
   to dynamically resolve ANY global or domestic city, country, or attraction keyword
   (e.g., 巴黎, 东京, 新加坡, 伦敦, 罗马, 曼谷, 河内, 悉尼, 纽约, 迪拜, 上海, 北京, 卢浮宫, 东京迪士尼)
   to its Ctrip `districtId` and matching POI items.
2. Ctrip Global Sight & Ticket SSR Engine (`m.ctrip.com/webapp/you/gspoi/sight/{districtId}.html`)
   to extract real-time attraction ticket prices (CNY & foreign currency), ticket package names,
   opening hours, recommended visit duration, review scores, monthly sales, and global rankings.
"""

import json
import re
import subprocess
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

try:
    from opencc import OpenCC
    _cc = OpenCC("t2s")
except Exception:
    _cc = None


def to_simplified(text: str) -> str:
    if not text:
        return ""
    return _cc.convert(text) if _cc else text


# Fast built-in lookup for global & domestic destination districtId
GLOBAL_DISTRICT_ID_MAP: Dict[str, int] = {
    # Europe
    "巴黎": 308,
    "伦敦": 309,
    "罗马": 303,
    "米兰": 304,
    "威尼斯": 340,
    "佛罗伦萨": 341,
    "苏黎世": 301,
    "日内瓦": 666,
    "因特拉肯": 667,
    "卢塞恩": 664,
    "琉森": 664,
    "法兰克福": 305,
    "慕尼黑": 362,
    "柏林": 306,
    "阿姆斯特丹": 302,
    "巴塞罗那": 381,
    "马德里": 378,
    "维也纳": 439,
    "布拉格": 668,
    "伊斯坦布尔": 532,
    # Asia & Middle East
    "东京": 294,
    "大阪": 293,
    "京都": 430,
    "名古屋": 2135,
    "札幌": 495,
    "北海道": 296,
    "冲绳": 292,
    "首尔": 234,
    "釜山": 432,
    "济州岛": 297,
    "曼谷": 191,
    "普吉岛": 364,
    "清迈": 209,
    "芭堤雅": 208,
    "新加坡": 53,
    "吉隆坡": 45,
    "槟城": 44,
    "沙巴": 538,
    "河内": 181,
    "胡志明市": 201,
    "岘港": 663,
    "芽庄": 662,
    "富国岛": 1446109,
    "巴厘岛": 438,
    "迪拜": 1062,
    "阿布扎比": 1491,
    "马尔代夫": 330,
    # Americas, Oceania, Africa
    "纽约": 248,
    "洛杉矶": 253,
    "旧金山": 249,
    "拉斯维加斯": 252,
    "多伦多": 355,
    "温哥华": 354,
    "悉尼": 236,
    "墨尔本": 312,
    "奥克兰": 320,
    "皇后镇": 684,
    "开罗": 326,
    "开普敦": 327,
    # Greater China
    "香港": 38,
    "澳门": 39,
    "台北": 360,
    "上海": 2,
    "北京": 1,
    "广州": 152,
    "深圳": 26,
    "杭州": 14,
    "成都": 104,
    "重庆": 158,
    "西安": 7,
    "南京": 9,
    "苏州": 11,
    "厦门": 21,
    "三亚": 61,
    "桂林": 28,
    "丽江": 32,
    "大理": 31,
    "昆明": 29,
    "武汉": 145,
    "长沙": 148,
    "青岛": 5,
    "哈尔滨": 151,
    "洛阳": 198,
    "上饶": 230,
    "婺源": 442,
    "黄山": 19,
    "张家界": 23,
}


class GlobalAttractionClient:
    def resolve_destination(
        self, query: str
    ) -> Tuple[Optional[int], str, List[Dict[str, Any]]]:
        """
        Resolve a city or attraction keyword into (district_id, resolved_city_name, direct_poi_hits).
        """
        if not query:
            return None, "", []
        clean = re.sub(
            r"(市|县|区|地区|特别行政区|景点|门票|好玩的地方|必去|玩乐|一日游|旅游|攻略)+$",
            "",
            query.strip(),
        ).strip()
        if not clean:
            clean = query.strip()

        district_id = GLOBAL_DISTRICT_ID_MAP.get(clean)
        city_name = clean
        direct_pois: List[Dict[str, Any]] = []

        try:
            url = (
                "https://m.ctrip.com/restapi/h5api/globalsearch/search"
                f"?action=online&source=globalonline&keyword={urllib.parse.quote(query.strip())}"
            )
            raw = subprocess.check_output(
                [
                    "curl",
                    "-sS",
                    "--max-time",
                    "6",
                    url,
                    "-H",
                    "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                ],
                timeout=8,
            ).decode("utf-8", errors="ignore")
            data = json.loads(raw)
            items = data.get("data", [])

            for item in items:
                itype = item.get("type", "")
                did = int(item.get("districtId") or 0)
                dname = to_simplified(item.get("districtName") or item.get("cityName") or "")
                word = to_simplified(item.get("word") or "")

                if not district_id:
                    if itype == "district" and int(item.get("id") or 0) > 0:
                        district_id = int(item["id"])
                        city_name = word or clean
                    elif did > 0:
                        district_id = did
                        if dname:
                            city_name = dname
                    elif itype == "channelgs":
                        m_u = re.search(r"/sight/[a-zA-Z]*(\d+)\.html", item.get("url") or "")
                        if m_u:
                            district_id = int(m_u.group(1))

                if itype in ("sight", "sightplay", "ticket"):
                    direct_pois.append(item)
        except Exception:
            pass

        if district_id and clean not in GLOBAL_DISTRICT_ID_MAP:
            GLOBAL_DISTRICT_ID_MAP[clean] = district_id

        return district_id, city_name, direct_pois

    def query_attractions(
        self, city_or_keyword: str, ticket_keyword: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Query real-time global attractions & ticket prices for a city or specific sight keyword.
        """
        search_q = city_or_keyword
        if ticket_keyword and ticket_keyword not in city_or_keyword:
            search_q = f"{city_or_keyword} {ticket_keyword}"

        district_id, city_name, direct_pois = self.resolve_destination(city_or_keyword)
        if not district_id and ticket_keyword:
            district_id, city_name, direct_pois = self.resolve_destination(ticket_keyword)

        attractions: List[Dict[str, Any]] = []
        if district_id:
            attractions = self._fetch_district_sights_ssr(district_id)

        # If user specified a particular attraction keyword (e.g. "卢浮宫", "迪士尼"), filter or enrich
        if ticket_keyword:
            kw_clean = re.sub(
                r"(门票|景点|预订|价格|多少钱|怎么买|一日游)+$", "", ticket_keyword.strip()
            ).strip()
            if kw_clean and kw_clean != city_name:
                matched = [
                    a
                    for a in attractions
                    if kw_clean.lower() in a["name"].lower()
                    or kw_clean.lower() in a.get("ename", "").lower()
                    or any(kw_clean in t for t in a.get("tags", []))
                ]
                if matched:
                    # Put matched attractions at the top while keeping other top sights of the city
                    others = [a for a in attractions if a not in matched]
                    attractions = matched + others[:6]
                else:
                    # Try fetching specific sight detail via GlobalSearch direct POI hits
                    _, _, kw_pois = self.resolve_destination(f"{city_name} {kw_clean}")
                    detail_cards = self._fetch_specific_pois(kw_pois or direct_pois, district_id)
                    if detail_cards:
                        attractions = detail_cards + attractions[:6]

        if not attractions and direct_pois:
            attractions = self._fetch_specific_pois(direct_pois, district_id or 0)

        if not attractions:
            return {
                "ok": False,
                "error": f"未能获取到 {city_or_keyword} 的实时景点门票数据",
                "city": city_name or city_or_keyword,
                "attractions": [],
            }

        return {
            "ok": True,
            "source": "携程全球景点与门票实时引擎 (Ctrip Global Sight & Ticket SSR)",
            "city": city_name or city_or_keyword,
            "district_id": district_id,
            "keyword": ticket_keyword,
            "count": len(attractions),
            "attractions": attractions[:10],
        }

    def _fetch_district_sights_ssr(self, district_id: int) -> List[Dict[str, Any]]:
        url = f"https://m.ctrip.com/webapp/you/gspoi/sight/{district_id}.html"
        try:
            html = subprocess.check_output(
                [
                    "curl",
                    "-sS",
                    "-L",
                    "--max-time",
                    "10",
                    url,
                    "-H",
                    "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                ],
                timeout=12,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="ignore")

            m = re.search(
                r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S
            )
            if not m:
                return []

            data = json.loads(m.group(1))
            rd = (
                data.get("props", {})
                .get("pageProps", {})
                .get("initialState", {})
                .get("seoDatas", {})
                .get("responseData", {})
            )
            raw_list = rd.get("attractionList") or []
            results: List[Dict[str, Any]] = []

            for item in raw_list:
                card_str = item.get("cardStr")
                if not card_str:
                    continue
                try:
                    c = json.loads(card_str)
                except Exception:
                    continue

                name = to_simplified(c.get("poiName") or "")
                if not name:
                    continue
                ename = c.get("poiEName") or ""
                comment = c.get("commentInfo") or {}
                score = comment.get("commentScore") or 0
                review_count = comment.get("commentCount") or 0

                pi = c.get("priceInfo") or {}
                is_free = bool(pi.get("isFree"))
                raw_price = pi.get("price")
                price_val = 0.0
                if raw_price is not None and str(raw_price).strip():
                    try:
                        price_val = float(raw_price)
                    except Exception:
                        price_val = 0.0

                orig_price = pi.get("underlinedPrice") or pi.get("marketPrice") or ""
                sales_desc = to_simplified(pi.get("salesVolumeDesc") or "")
                ticket_name = ""
                foreign_price_str = ""
                discount_tag = ""

                shelf_str = pi.get("shelfPopoverData")
                if shelf_str:
                    try:
                        shelf = json.loads(shelf_str)
                        ticket_name = to_simplified(shelf.get("minResourceName") or "")
                        f_amt = shelf.get("marketOriginPrice")
                        f_curr = shelf.get("marketOriginPriceCurrency")
                        if f_amt and f_curr and f_curr != "CNY":
                            foreign_price_str = f"{f_curr} {f_amt:g}"
                        promos = shelf.get("promotions") or []
                        if promos and promos[0].get("descriptions"):
                            discount_tag = to_simplified(promos[0]["descriptions"][0])
                    except Exception:
                        pass

                dist_info = c.get("distanceInfo") or {}
                dist_str = ""
                if dist_info.get("startDistanceStr") and dist_info.get("endDistanceStr"):
                    dist_str = f"{dist_info['startDistanceStr']}{dist_info['endDistanceStr']}"

                rank_info = c.get("rankingInfo") or {}
                rank_desc = to_simplified(rank_info.get("rankDesc") or "")

                booking_tags = [
                    to_simplified(t.get("name"))
                    for t in (c.get("otherTagList") or [])
                    if t.get("name")
                ]
                features = [to_simplified(f) for f in (c.get("shortFeatures") or []) if f]
                tags = [to_simplified(t) for t in (c.get("tagNameList") or []) if t]

                results.append(
                    {
                        "name": name,
                        "ename": ename,
                        "poi_id": c.get("poiId"),
                        "business_id": c.get("businessId"),
                        "district_name": to_simplified(c.get("districtName") or ""),
                        "address": to_simplified(c.get("address") or ""),
                        "distance": dist_str,
                        "score": score,
                        "review_count": review_count,
                        "open_time": to_simplified(
                            c.get("openTimeDesc") or c.get("openStatus") or "全天或详见景区公告"
                        ),
                        "play_duration": to_simplified(c.get("playSpendTime") or ""),
                        "is_free": is_free,
                        "price": int(round(price_val)) if price_val > 0 else 0,
                        "original_price": orig_price if orig_price and str(orig_price) != "0" else "",
                        "foreign_price": foreign_price_str,
                        "ticket_name": ticket_name,
                        "discount_tag": discount_tag,
                        "sales": sales_desc,
                        "rank_badge": rank_desc,
                        "features": features,
                        "tags": tags,
                        "booking_tags": booking_tags,
                    }
                )
            return results
        except Exception:
            return []

    def _fetch_specific_pois(
        self, poi_items: List[Dict[str, Any]], fallback_district_id: int
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for item in poi_items[:3]:
            did = int(item.get("districtId") or fallback_district_id or 0)
            bid = int(item.get("id") or 0)
            if not did or not bid:
                continue
            url = f"https://m.ctrip.com/webapp/you/gspoi/sight/{did}/{bid}.html"
            try:
                html = subprocess.check_output(
                    [
                        "curl",
                        "-sS",
                        "-L",
                        "--max-time",
                        "8",
                        url,
                        "-H",
                        "User-Agent: Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
                    ],
                    timeout=10,
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="ignore")
                m = re.search(
                    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S
                )
                if not m:
                    continue
                d = json.loads(m.group(1))
                bd = (
                    d.get("props", {})
                    .get("pageProps", {})
                    .get("initialState", {})
                    .get("baseData", {})
                )
                if not bd or not bd.get("poiName"):
                    continue

                tag_info = bd.get("tagInfo") or {}
                price_val = 0
                for ti in tag_info.get("tagItemList") or []:
                    if ti.get("type") == "ticket":
                        pdesc = (ti.get("ticketTag") or {}).get("priceDesc") or ""
                        mp = re.search(r"(\d+(?:\.\d+)?)", pdesc)
                        if mp:
                            price_val = int(round(float(mp.group(1))))

                open_time = (bd.get("openInfo") or {}).get("openTime") or "详见景区公告"
                rank_desc = (bd.get("rankingInfo") or {}).get("description") or ""
                results.append(
                    {
                        "name": to_simplified(bd.get("poiName") or ""),
                        "ename": bd.get("poiEName") or "",
                        "poi_id": bd.get("poiId"),
                        "business_id": bd.get("businessId"),
                        "district_name": to_simplified(bd.get("districtName") or ""),
                        "address": to_simplified(bd.get("address") or ""),
                        "distance": "",
                        "score": bd.get("commentScore") or 4.7,
                        "review_count": bd.get("commentCount") or 0,
                        "open_time": to_simplified(open_time),
                        "play_duration": "",
                        "is_free": bool(bd.get("isFree")),
                        "price": price_val,
                        "original_price": "",
                        "foreign_price": "",
                        "ticket_name": "官方门票/电子票",
                        "discount_tag": "",
                        "sales": "",
                        "rank_badge": to_simplified(rank_desc),
                        "features": [to_simplified(f) for f in (bd.get("shortFeatures") or [])],
                        "tags": [to_simplified(t) for t in (bd.get("tagName") or [])[:3]],
                        "booking_tags": ["线上预约"] if bd.get("needTicket") else [],
                    }
                )
            except Exception:
                continue
        return results
