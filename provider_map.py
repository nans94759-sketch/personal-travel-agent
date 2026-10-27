"""
Open-Source Zero-Key Smart Route & Map Engine (Leaflet + OSRM + TSP + Multi-Modal Arcs)
---------------------------------------------------------------------------------------
Provides:
1. Coordinate System Normalization (BD09 <-> GCJ02 <-> WGS84) so both global WGS84 tiles
   (CartoDB Voyager / OpenStreetMap) and domestic GCJ02 tiles (Gaode / AMap) align with zero drift.
2. Fast Multi-Tier Geocoder:
   - Tier 1: Built-in high-precision WGS84 coordinate dictionary for 140+ global & Chinese cities,
             airports, and railway hubs (0ms).
   - Tier 2: Live Ctrip H5 GlobalSearch API (`m.ctrip.com/restapi/h5api/globalsearch/search`) with disk cache.
   - Tier 3: OpenStreetMap Nominatim API fallback.
3. TSP Smart Route Optimizer (Nearest Neighbor + 2-Opt Local Search):
   - Automatically sequences daily hotels & attractions into a non-backtracking loop/path.
4. OSRM Real Street Routing (`router.project-osrm.org`):
   - Computes real road/walking GeoJSON trajectories and per-leg distance & duration.
5. Intercity Multi-Modal Geodesic / Bezier Arcs:
   - Generates distinct curved trajectories for Flights, High-Speed Rail, Euro Rail,
     Smart 1-Stop Hub Transfers (Air-Rail / Flight-Flight / Rail-Rail), and Train Stopover Stations.
"""

import json
import math
import os
import re
import subprocess
import threading
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from compliance import guard, source_allowed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, ".cache")
GEOCODE_CACHE_FILE = os.path.join(CACHE_DIR, "geocode_cache.json")

# ============================================================================
# 1. Coordinate System Conversion Utilities (BD09 <-> GCJ02 <-> WGS84)
# ============================================================================
_X_PI = math.pi * 3000.0 / 180.0
_A = 6378245.0
_EE = 0.00669342162296594323


def is_in_mainland_china(lat: float, lon: float) -> bool:
    """Rough bounding box check for Mainland China (excluding HK/Macau/Taiwan for basic check)."""
    return 18.1 <= lat <= 53.6 and 73.5 <= lon <= 135.1


def _transform_lat(lon: float, lat: float) -> float:
    ret = (
        -100.0
        + 2.0 * lon
        + 3.0 * lat
        + 0.2 * lat * lat
        + 0.1 * lon * lat
        + 0.2 * math.sqrt(abs(lon))
    )
    ret += (20.0 * math.sin(6.0 * lon * math.pi) + 20.0 * math.sin(2.0 * lon * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(lon: float, lat: float) -> float:
    ret = (
        300.0
        + lon
        + 2.0 * lat
        + 0.1 * lon * lon
        + 0.1 * lon * lat
        + 0.1 * math.sqrt(abs(lon))
    )
    ret += (20.0 * math.sin(6.0 * lon * math.pi) + 20.0 * math.sin(2.0 * lon * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lon * math.pi) + 40.0 * math.sin(lon / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lon / 12.0 * math.pi) + 300.0 * math.sin(lon / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def bd09_to_gcj02(bd_lat: float, bd_lon: float) -> Tuple[float, float]:
    x = bd_lon - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * _X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * _X_PI)
    gcj_lon = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    return gcj_lat, gcj_lon


def gcj02_to_wgs84(gcj_lat: float, gcj_lon: float) -> Tuple[float, float]:
    if not is_in_mainland_china(gcj_lat, gcj_lon):
        return gcj_lat, gcj_lon
    dlat = _transform_lat(gcj_lon - 105.0, gcj_lat - 35.0)
    dlon = _transform_lon(gcj_lon - 105.0, gcj_lat - 35.0)
    radlat = gcj_lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    mglat = gcj_lat + dlat
    mglon = gcj_lon + dlon
    return gcj_lat * 2 - mglat, gcj_lon * 2 - mglon


def wgs84_to_gcj02(wgs_lat: float, wgs_lon: float) -> Tuple[float, float]:
    if not is_in_mainland_china(wgs_lat, wgs_lon):
        return wgs_lat, wgs_lon
    dlat = _transform_lat(wgs_lon - 105.0, wgs_lat - 35.0)
    dlon = _transform_lon(wgs_lon - 105.0, wgs_lat - 35.0)
    radlat = wgs_lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return wgs_lat + dlat, wgs_lon + dlon


def normalize_to_wgs84(lat: float, lon: float, coord_type: str = "WGS84") -> Tuple[float, float]:
    ctype = (coord_type or "WGS84").upper()
    if "BD" in ctype:
        glat, glon = bd09_to_gcj02(lat, lon)
        return gcj02_to_wgs84(glat, glon)
    if "GCJ" in ctype or "GD" in ctype or "GAODE" in ctype:
        return gcj02_to_wgs84(lat, lon)
    return lat, lon


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * r * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


# ============================================================================
# 2. Built-in High-Precision WGS84 Coordinate Dictionary (0ms Lookup)
# ============================================================================
KNOWN_COORDS_WGS84: Dict[str, Tuple[float, float]] = {
    # Major Chinese Cities & Stations
    "北京": (39.9042, 116.4074),
    "北京南": (39.8651, 116.3785),
    "北京西": (39.8948, 116.3216),
    "北京朝阳": (39.9485, 116.5015),
    "北京丰台": (39.8475, 116.2995),
    "首都国际机场": (40.0799, 116.6031),
    "大兴国际机场": (39.5098, 116.4105),
    "上海": (31.2304, 121.4737),
    "上海虹桥": (31.1941, 121.3204),
    "虹桥": (31.1941, 121.3204),
    "上海浦东": (31.1443, 121.8083),
    "浦东国际机场": (31.1443, 121.8083),
    "上海南": (31.1545, 121.4297),
    "外滩": (31.2378, 121.4903),
    "广州": (23.1291, 113.2644),
    "广州南": (22.9888, 113.2690),
    "广州东": (23.1506, 113.3249),
    "广州白云": (23.3924, 113.2988),
    "白云国际机场": (23.3924, 113.2988),
    "深圳": (22.5431, 114.0579),
    "深圳北": (22.6096, 114.0294),
    "深圳宝安": (22.6393, 113.8107),
    "杭州": (30.2741, 120.1551),
    "杭州东": (30.2909, 120.2127),
    "杭州南": (30.1725, 120.2923),
    "杭州西": (30.2982, 119.9922),
    "萧山国际机场": (30.2295, 120.4344),
    "西湖": (30.2427, 120.1487),
    "南京": (32.0603, 118.7969),
    "南京南": (31.9690, 118.7979),
    "苏州": (31.2990, 120.5853),
    "苏州北": (31.4216, 120.6428),
    "昆山": (31.3856, 120.9807),
    "昆山南": (31.3526, 120.9532),
    "无锡": (31.4912, 120.3119),
    "宁波": (29.8683, 121.5440),
    "温州": (27.9938, 120.6994),
    "温州南": (27.9693, 120.5877),
    "义乌": (29.3069, 120.0750),
    "福州": (26.0745, 119.2965),
    "福州南": (25.9863, 119.3911),
    "厦门": (24.4798, 118.0894),
    "厦门北": (24.6368, 118.0740),
    "泉州": (24.8741, 118.6757),
    "延平": (26.6177, 118.2345),
    "南平市": (27.3317, 118.1225),
    "武夷山北": (27.7918, 118.0372),
    "建瓯西": (27.0493, 118.2738),
    "上饶": (28.4549, 117.9434),
    "玉山": (28.6821, 118.2451),
    "玉山南": (28.6479, 118.2572),
    "婺源": (29.2484, 117.8614),
    "景德镇": (29.2688, 117.1784),
    "景德镇北": (29.3292, 117.2236),
    "南昌": (28.6820, 115.8579),
    "南昌西": (28.6272, 115.7924),
    "黄山": (29.7147, 118.3375),
    "黄山北": (29.8185, 118.2717),
    "合肥": (31.8206, 117.2272),
    "合肥南": (31.7983, 117.2903),
    "武汉": (30.5928, 114.3055),
    "汉口": (30.6192, 114.2553),
    "武昌": (30.5290, 114.3168),
    "长沙": (28.2282, 112.9388),
    "长沙南": (28.1472, 113.0652),
    "张家界": (29.1171, 110.4792),
    "成都": (30.5728, 104.0668),
    "成都东": (30.6289, 104.1411),
    "天府国际机场": (30.3125, 104.4447),
    "双流国际机场": (30.5785, 103.9471),
    "重庆": (29.5630, 106.5516),
    "重庆北": (29.6085, 106.5503),
    "重庆西": (29.5015, 106.4352),
    "西安": (34.3416, 108.9398),
    "西安北": (34.4437, 108.9387),
    "郑州": (34.7466, 113.6253),
    "郑州东": (34.7592, 113.7785),
    "洛阳": (34.6197, 112.4540),
    "开封": (34.7972, 114.3076),
    "济南": (36.6512, 117.1201),
    "济南西": (36.6694, 116.8917),
    "青岛": (36.0671, 120.3826),
    "威海": (37.5131, 122.1204),
    "烟台": (37.4638, 121.4479),
    "天津": (39.0842, 117.2010),
    "大连": (38.9140, 121.6147),
    "沈阳": (41.8057, 123.4315),
    "长春": (43.8171, 125.3235),
    "哈尔滨": (45.8038, 126.5350),
    "延吉": (42.8913, 129.5089),
    "昆明": (25.0389, 102.7183),
    "昆明南": (24.8850, 102.8622),
    "大理": (25.6065, 100.2676),
    "丽江": (26.8550, 100.2278),
    "西双版纳": (22.0088, 100.7972),
    "贵阳": (26.6470, 106.6302),
    "贵阳北": (26.6198, 106.6416),
    "桂林": (25.2736, 110.2900),
    "桂林北": (25.3289, 110.3016),
    "阳朔": (24.7784, 110.4966),
    "南宁": (22.8170, 108.3665),
    "南宁东": (22.8388, 108.4148),
    "凭祥": (22.0968, 106.7586),
    "海口": (20.0440, 110.1999),
    "三亚": (18.2528, 109.5120),
    "珠海": (22.2710, 113.5767),
    "拉萨": (29.6525, 91.1721),
    "乌鲁木齐": (43.8256, 87.6168),
    "兰州": (36.0611, 103.8343),
    "西宁": (36.6171, 101.7782),
    "银川": (38.4872, 106.2309),
    "太原": (37.8706, 112.5489),
    "石家庄": (38.0428, 114.5149),
    "呼和浩特": (40.8426, 111.7492),
    # HK / Macau / Taiwan
    "香港": (22.3193, 114.1694),
    "香港西九龙": (22.3039, 114.1652),
    "澳门": (22.1987, 113.5439),
    "台北": (25.0330, 121.5654),
    # Vietnam & SE Asia
    "河内": (21.0285, 105.8542),
    "内排国际机场": (21.2187, 105.8042),
    "河内内排": (21.2187, 105.8042),
    "嘉林": (21.0522, 105.8794),
    "胡志明市": (10.8231, 106.6297),
    "胡志明": (10.8231, 106.6297),
    "新山一国际机场": (10.8188, 106.6519),
    "岘港": (16.0544, 108.2022),
    "芽庄": (12.2388, 109.1967),
    "富国岛": (10.2289, 103.9572),
    "曼谷": (13.7563, 100.5018),
    "素万那普": (13.6900, 100.7501),
    "廊曼": (13.9126, 100.6068),
    "普吉岛": (7.8804, 98.3923),
    "清迈": (18.7883, 98.9853),
    "芭堤雅": (12.9236, 100.8825),
    "新加坡": (1.3521, 103.8198),
    "樟宜机场": (1.3644, 103.9915),
    "吉隆坡": (3.1390, 101.6869),
    "槟城": (5.4141, 100.3288),
    "沙巴": (5.9804, 116.0735),
    "巴厘岛": (-8.4095, 115.1889),
    # Japan & Korea
    "东京": (35.6762, 139.6503),
    "成田": (35.7720, 140.3929),
    "羽田": (35.5494, 139.7798),
    "大阪": (34.6937, 135.5023),
    "关西": (34.4320, 135.2304),
    "京都": (35.0116, 135.7681),
    "名古屋": (35.1815, 136.9066),
    "札幌": (43.0618, 141.3545),
    "北海道": (43.0618, 141.3545),
    "冲绳": (26.2124, 127.6809),
    "首尔": (37.5665, 126.9780),
    "仁川": (37.4602, 126.4407),
    "釜山": (35.1796, 129.0756),
    "济州岛": (33.4996, 126.5312),
    # Europe & Middle East
    "巴黎": (48.8566, 2.3522),
    "伦敦": (51.5072, -0.1276),
    "罗马": (41.9028, 12.4964),
    "米兰": (45.4642, 9.1900),
    "威尼斯": (45.4408, 12.3155),
    "佛罗伦萨": (43.7696, 11.2558),
    "苏黎世": (47.3769, 8.5417),
    "日内瓦": (46.2044, 6.1432),
    "因特拉肯": (46.6863, 7.8632),
    "卢塞恩": (47.0502, 8.3093),
    "琉森": (47.0502, 8.3093),
    "伯尔尼": (46.9480, 7.4474),
    "巴塞尔": (47.5596, 7.5886),
    "采尔马特": (46.0207, 7.7491),
    "洛桑": (46.5197, 6.6323),
    "蒙特勒": (46.4312, 6.9107),
    "法兰克福": (50.1109, 8.6821),
    "慕尼黑": (48.1351, 11.5820),
    "柏林": (52.5200, 13.4050),
    "斯图加特": (48.7758, 9.1829),
    "斯特拉斯堡": (48.5734, 7.7521),
    "里昂": (45.7640, 4.8357),
    "阿姆斯特丹": (52.3676, 4.9041),
    "巴塞罗那": (41.3874, 2.1686),
    "马德里": (40.4168, -3.7038),
    "维也纳": (48.2082, 16.3738),
    "布拉格": (50.0755, 14.4378),
    "伊斯坦布尔": (41.0082, 28.9784),
    "迪拜": (25.2048, 55.2708),
    "阿布扎比": (24.4539, 54.3773),
    "开罗": (30.0444, 31.2357),
    "开普敦": (-33.9249, 18.4241),
    "马尔代夫": (4.1755, 73.5093),
    # Americas & Oceania
    "纽约": (40.7128, -74.0060),
    "洛杉矶": (34.0522, -118.2437),
    "旧金山": (37.7749, -122.4194),
    "拉斯维加斯": (36.1699, -115.1398),
    "温哥华": (49.2827, -123.1207),
    "多伦多": (43.6532, -79.3832),
    "悉尼": (-33.8688, 151.2093),
    "墨尔本": (-37.8136, 144.9631),
    "奥克兰": (-36.8485, 174.7633),
    "皇后镇": (-45.0312, 168.6626),
}


class SmartRouteMapEngine:
    def __init__(self) -> None:
        self._cache_lock = threading.Lock()
        self._mem_cache: Dict[str, Tuple[float, float]] = dict(KNOWN_COORDS_WGS84)
        self._load_disk_cache()

    def _load_disk_cache(self) -> None:
        if not os.path.exists(GEOCODE_CACHE_FILE):
            return
        try:
            with open(GEOCODE_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if isinstance(v, list) and len(v) == 2:
                        self._mem_cache[k] = (float(v[0]), float(v[1]))
        except Exception:
            pass

    def _save_disk_cache(self) -> None:
        with self._cache_lock:
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                serializable = {
                    k: [round(v[0], 6), round(v[1], 6)]
                    for k, v in list(self._mem_cache.items())
                    if k not in KNOWN_COORDS_WGS84
                }
                tmp_file = f"{GEOCODE_CACHE_FILE}.{threading.get_ident()}.tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(serializable, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, GEOCODE_CACHE_FILE)
            except Exception:
                pass

    def resolve_point(
        self, query: str, city_hint: str = "", allow_network: bool = True
    ) -> Optional[Tuple[float, float]]:
        """
        Resolve any place name (city, train station, airport, attraction, hotel) into WGS84 (lat, lon).
        """
        if not query:
            return None
        q_raw = query.strip()
        q_clean = re.sub(
            r"(火车站|高铁站|动车站|站|国际机场|机场|市|县|区|地区|特别行政区|T\d|航站楼)+$",
            "",
            q_raw,
        ).strip()

        for candidate in (q_raw, q_clean, f"{city_hint}{q_raw}", f"{city_hint} {q_raw}"):
            c = candidate.strip()
            if c and c in self._mem_cache:
                return self._mem_cache[c]

        # Partial match against known hubs
        for k, coords in KNOWN_COORDS_WGS84.items():
            if len(k) >= 2 and (k == q_clean or (len(q_clean) >= 2 and k in q_raw)):
                return coords

        if not allow_network:
            if city_hint and city_hint in self._mem_cache:
                return self._mem_cache[city_hint]
            return None

        # Live lookup via Ctrip Open GlobalSearch API (skipped in commercial mode)
        search_kw = f"{city_hint} {q_raw}".strip() if city_hint and city_hint not in q_raw else q_raw
        if source_allowed("ctrip.h5api_globalsearch"):
            try:
                url = (
                    "https://m.ctrip.com/restapi/h5api/globalsearch/search"
                    f"?action=online&source=globalonline&keyword={urllib.parse.quote(search_kw)}"
                )
                raw = subprocess.check_output(
                    ["curl", "-sS", "--max-time", "4", url, "-H", "User-Agent: Mozilla/5.0"],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="ignore")
                items = json.loads(raw).get("data", [])
                for item in items:
                    lat = float(item.get("lat") or 0.0)
                    lon = float(item.get("lon") or 0.0)
                    if abs(lat) > 0.01 and abs(lon) > 0.01:
                        # Ctrip globalsearch returns GCJ02 for Mainland China items, WGS84 for overseas
                        if is_in_mainland_china(lat, lon):
                            wlat, wlon = gcj02_to_wgs84(lat, lon)
                        else:
                            wlat, wlon = lat, lon
                        self._mem_cache[q_raw] = (wlat, wlon)
                        if q_clean:
                            self._mem_cache[q_clean] = (wlat, wlon)
                        self._save_disk_cache()
                        return (wlat, wlon)
            except Exception:
                pass

        # Whitelisted fallback: OpenStreetMap Nominatim
        if source_allowed("nominatim.geocode"):
            try:
                nom_url = (
                    "https://nominatim.openstreetmap.org/search"
                    f"?q={urllib.parse.quote(search_kw)}&format=json&limit=1"
                )
                raw_nom = subprocess.check_output(
                    [
                        "curl",
                        "-sS",
                        "--max-time",
                        "4",
                        nom_url,
                        "-H",
                        "User-Agent: PersonalTravelAgent/1.0",
                    ],
                    timeout=5,
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="ignore")
                nom_items = json.loads(raw_nom)
                if isinstance(nom_items, list) and nom_items:
                    wlat = float(nom_items[0].get("lat") or 0.0)
                    wlon = float(nom_items[0].get("lon") or 0.0)
                    if abs(wlat) > 0.01 and abs(wlon) > 0.01:
                        self._mem_cache[q_raw] = (wlat, wlon)
                        if q_clean:
                            self._mem_cache[q_clean] = (wlat, wlon)
                        self._save_disk_cache()
                        return (wlat, wlon)
            except Exception:
                pass

        if city_hint and city_hint != q_raw:
            return self.resolve_point(city_hint, "", allow_network=False)
        return None

    @staticmethod
    def make_point_dict(
        name: str,
        lat: float,
        lon: float,
        category: str = "poi",
        subtitle: str = "",
        price: Optional[float] = None,
        foreign_price: str = "",
        score: Optional[Any] = None,
        badge: str = "",
        order_index: Optional[int] = None,
    ) -> Dict[str, Any]:
        glat, glon = wgs84_to_gcj02(lat, lon)
        return {
            "name": name,
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "gcj_lat": round(glat, 6),
            "gcj_lon": round(glon, 6),
            "category": category,  # 'origin' | 'destination' | 'hub' | 'stopover' | 'hotel' | 'attraction' | 'itinerary'
            "subtitle": subtitle,
            "price": int(round(price)) if price and float(price) > 0 else 0,
            "foreign_price": foreign_price,
            "score": score or "",
            "badge": badge,
            "order": order_index,
        }

    # ========================================================================
    # 3. TSP Intelligent Route Optimizer (Nearest Neighbor + 2-Opt)
    # ========================================================================
    @staticmethod
    def optimize_tsp_route(
        points: List[Dict[str, Any]], fix_start: bool = True
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Given a list of point dicts with ('lat', 'lon'), finds an optimized visiting order
        using Nearest-Neighbor + 2-Opt local search to minimize backtracking.
        """
        n = len(points)
        if n <= 2:
            out = []
            for idx, p in enumerate(points):
                cp = dict(p)
                cp["order"] = idx + 1
                out.append(cp)
            dist = (
                haversine_km(points[0]["lat"], points[0]["lon"], points[1]["lat"], points[1]["lon"])
                if n == 2
                else 0.0
            )
            return out, {
                "original_km": round(dist, 2),
                "optimized_km": round(dist, 2),
                "saved_km": 0.0,
                "saved_pct": 0,
            }

        def path_length(order: List[int]) -> float:
            return sum(
                haversine_km(
                    points[order[i]]["lat"],
                    points[order[i]]["lon"],
                    points[order[i + 1]]["lat"],
                    points[order[i + 1]]["lon"],
                )
                for i in range(len(order) - 1)
            )

        orig_order = list(range(n))
        orig_len = path_length(orig_order)

        # Step 1: Nearest Neighbor starting from index 0 (e.g. Anchor Hotel or Arrival Hub)
        start_idx = 0 if fix_start else min(range(n), key=lambda i: points[i]["lon"] - points[i]["lat"])
        unvisited = set(range(n))
        unvisited.remove(start_idx)
        tour = [start_idx]
        curr = start_idx
        while unvisited:
            nxt = min(
                unvisited,
                key=lambda j: haversine_km(
                    points[curr]["lat"], points[curr]["lon"], points[j]["lat"], points[j]["lon"]
                ),
            )
            tour.append(nxt)
            unvisited.remove(nxt)
            curr = nxt

        # Step 2: 2-Opt Local Search Refinement (uncross intersecting edges)
        improved = True
        max_iter = 60
        it = 0
        while improved and it < max_iter:
            improved = False
            it += 1
            for i in range(1 if fix_start else 0, n - 1):
                for k in range(i + 1, n):
                    new_tour = tour[:i] + list(reversed(tour[i : k + 1])) + tour[k + 1 :]
                    if path_length(new_tour) + 1e-4 < path_length(tour):
                        tour = new_tour
                        improved = True

        opt_len = path_length(tour)
        if opt_len > orig_len:
            tour = orig_order
            opt_len = orig_len

        saved_km = max(0.0, orig_len - opt_len)
        saved_pct = int(round((saved_km / orig_len) * 100)) if orig_len > 0.1 else 0

        ordered_points = []
        for rank, idx in enumerate(tour):
            cp = dict(points[idx])
            cp["order"] = rank + 1
            ordered_points.append(cp)

        return ordered_points, {
            "original_km": round(orig_len, 2),
            "optimized_km": round(opt_len, 2),
            "saved_km": round(saved_km, 2),
            "saved_pct": saved_pct,
        }

    # ========================================================================
    # 4. OSRM Real Street Routing (`router.project-osrm.org`)
    # ========================================================================
    @staticmethod
    def fetch_osrm_route(
        points: List[Dict[str, Any]], profile: str = "driving"
    ) -> Dict[str, Any]:
        """
        Query OSRM public router for real road/walking geometry and leg-by-leg distance/duration.
        Falls back cleanly to Haversine segments if points are cross-ocean or OSRM times out.
        """
        if len(points) < 2:
            return {
                "ok": False,
                "geometry": [],
                "gcj_geometry": [],
                "legs": [],
                "total_distance_km": 0.0,
                "total_duration_mins": 0,
                "source": "单点位置",
            }

        osrm_profile = "foot" if profile in ("foot", "walk", "walking") else "driving"
        coord_str = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in points)
        url = (
            f"https://router.project-osrm.org/route/v1/{osrm_profile}/{coord_str}"
            "?overview=full&geometries=geojson&steps=false"
        )

        try:
            raw = subprocess.check_output(
                ["curl", "-sS", "--max-time", "5", url, "-H", "User-Agent: Mozilla/5.0"],
                timeout=6,
                stderr=subprocess.DEVNULL,
            ).decode("utf-8", errors="ignore")
            data = json.loads(raw)
            if data.get("code") == "Ok" and data.get("routes"):
                route = data["routes"][0]
                raw_coords = (route.get("geometry") or {}).get("coordinates") or []
                # Downsample if overly dense (> 320 vertices) while preserving endpoints
                step = max(1, len(raw_coords) // 300)
                sampled = raw_coords[::step]
                if raw_coords and sampled[-1] != raw_coords[-1]:
                    sampled.append(raw_coords[-1])

                wgs_geom = [[round(c[1], 6), round(c[0], 6)] for c in sampled]
                gcj_geom = []
                for lat, lon in wgs_geom:
                    glat, glon = wgs84_to_gcj02(lat, lon)
                    gcj_geom.append([round(glat, 6), round(glon, 6)])

                osrm_legs = route.get("legs") or []
                legs_out = []
                total_km = 0.0
                total_mins = 0
                for idx in range(len(points) - 1):
                    p_from = points[idx]
                    p_to = points[idx + 1]
                    leg_obj = osrm_legs[idx] if idx < len(osrm_legs) else {}
                    dist_m = float(leg_obj.get("distance") or 0.0)
                    dur_s = float(leg_obj.get("duration") or 0.0)
                    km = round(dist_m / 1000.0, 2)
                    # Realistic urban travel time adjustment
                    if osrm_profile == "foot" or km < 1.3:
                        mins = max(3, int(round((km / 4.5) * 60)))
                        mode_label = "步行" if km <= 1.8 else "步行/短途"
                    else:
                        mins = max(4, int(round(dur_s / 60.0 * 1.15)))
                        mode_label = "打车/驾车"
                    walk_mins = max(2, int(round((km / 4.6) * 60)))
                    drive_mins = max(3, int(round(max(dur_s / 60.0, km / 0.45))))
                    total_km += km
                    total_mins += drive_mins if osrm_profile == "driving" else walk_mins
                    legs_out.append(
                        {
                            "from_name": p_from["name"],
                            "to_name": p_to["name"],
                            "from_order": idx + 1,
                            "to_order": idx + 2,
                            "distance_km": km,
                            "duration_mins": mins,
                            "drive_mins": drive_mins,
                            "walk_mins": walk_mins,
                            "mode_label": mode_label,
                        }
                    )

                return {
                    "ok": True,
                    "profile": osrm_profile,
                    "geometry": wgs_geom,
                    "gcj_geometry": gcj_geom,
                    "legs": legs_out,
                    "total_distance_km": round(total_km, 1),
                    "total_duration_mins": total_mins,
                    "source": "OSRM 真实街道导航引擎",
                }
        except Exception:
            pass

        # Fallback: smooth interpolated segments + Haversine estimates
        wgs_geom = []
        gcj_geom = []
        legs_out = []
        total_km = 0.0
        total_mins = 0
        for idx in range(len(points) - 1):
            p1, p2 = points[idx], points[idx + 1]
            arc_wgs, arc_gcj = SmartRouteMapEngine.build_curved_arc(
                p1["lat"], p1["lon"], p2["lat"], p2["lon"], curvature=0.08, num_points=20
            )
            wgs_geom.extend(arc_wgs if idx == 0 else arc_wgs[1:])
            gcj_geom.extend(arc_gcj if idx == 0 else arc_gcj[1:])
            km = round(haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]) * 1.25, 2)
            walk_mins = max(3, int(round((km / 4.5) * 60)))
            drive_mins = max(4, int(round((km / 26.0) * 60)) + 3)
            mins = walk_mins if osrm_profile == "foot" else drive_mins
            total_km += km
            total_mins += mins
            legs_out.append(
                {
                    "from_name": p1["name"],
                    "to_name": p2["name"],
                    "from_order": idx + 1,
                    "to_order": idx + 2,
                    "distance_km": km,
                    "duration_mins": mins,
                    "drive_mins": drive_mins,
                    "walk_mins": walk_mins,
                    "mode_label": "步行" if km <= 1.6 else "打车/驾车",
                }
            )

        return {
            "ok": True,
            "profile": osrm_profile,
            "geometry": wgs_geom,
            "gcj_geometry": gcj_geom,
            "legs": legs_out,
            "total_distance_km": round(total_km, 1),
            "total_duration_mins": total_mins,
            "source": "智能地理路径计算",
        }

    # ========================================================================
    # 5. Geodesic / Quadratic Bezier Arc Builder for Intercity Flights & Trains
    # ========================================================================
    @staticmethod
    def build_curved_arc(
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
        curvature: float = 0.16,
        num_points: int = 36,
    ) -> Tuple[List[List[float]], List[List[float]]]:
        """
        Generate a smooth quadratic Bezier arc in both WGS84 and GCJ02 coordinates.
        `curvature` > 0 bends left/north, < 0 bends right/south, 0 is straight.
        """
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        mid_lat = (lat1 + lat2) / 2.0 - dlon * curvature
        mid_lon = (lon1 + lon2) / 2.0 + dlat * curvature

        wgs_pts: List[List[float]] = []
        gcj_pts: List[List[float]] = []
        for i in range(num_points + 1):
            t = i / float(num_points)
            u = 1.0 - t
            lat = u * u * lat1 + 2 * u * t * mid_lat + t * t * lat2
            lon = u * u * lon1 + 2 * u * t * mid_lon + t * t * lon2
            glat, glon = wgs84_to_gcj02(lat, lon)
            wgs_pts.append([round(lat, 5), round(lon, 5)])
            gcj_pts.append([round(glat, 5), round(glon, 5)])
        return wgs_pts, gcj_pts

    # ========================================================================
    # 6. Full Turn Smart Route Map Builder
    # ========================================================================
    def build_turn_map_data(
        self,
        intent: Dict[str, Any],
        trains: List[Dict[str, Any]],
        euro_trains: List[Dict[str, Any]],
        flights: List[Dict[str, Any]],
        transfers: List[Dict[str, Any]],
        stopovers: List[Dict[str, Any]],
        hotels: List[Dict[str, Any]],
        attractions: List[Dict[str, Any]],
        comparison: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Synthesize both:
        1) Intercity / Cross-border Transport Network (Direct Rail, Direct Flight, Smart Multi-Modal Transfers, Stopover Chain)
        2) Local Destination Smart Route (Hotel Anchor + Attractions + TSP Optimization + OSRM Street Polyline)
        """
        from_city = intent.get("from_city") or ""
        to_city = intent.get("to_city") or ""
        hotel_city = intent.get("hotel_city") or to_city
        attraction_city = intent.get("attraction_city") or to_city or hotel_city
        target_city = to_city or hotel_city or attraction_city

        # --------------------------------------------------------------------
        # Part A: Intercity Transport Network Layer
        # --------------------------------------------------------------------
        intercity_markers: List[Dict[str, Any]] = []
        intercity_arcs: List[Dict[str, Any]] = []
        intercity_route_groups: List[Dict[str, Any]] = []

        c_from = self.resolve_point(from_city, allow_network=True) if from_city else None
        c_to = self.resolve_point(to_city, allow_network=True) if to_city else None

        if c_from and c_to and from_city != to_city:
            dist_direct_km = round(haversine_km(c_from[0], c_from[1], c_to[0], c_to[1]), 0)
            intercity_markers.append(
                self.make_point_dict(
                    name=from_city,
                    lat=c_from[0],
                    lon=c_from[1],
                    category="origin",
                    subtitle="出发城市",
                    badge="起点",
                )
            )
            intercity_markers.append(
                self.make_point_dict(
                    name=to_city,
                    lat=c_to[0],
                    lon=c_to[1],
                    category="destination",
                    subtitle=f"目的地 · 直线约 {int(dist_direct_km)} km",
                    badge="终点",
                )
            )

            # 1. Direct High-Speed Rail / Train Arc
            if trains:
                best_t = (
                    ((comparison or {}).get("train_option") or {}).get("best")
                    or trains[0]
                )
                wgs_arc, gcj_arc = self.build_curved_arc(
                    c_from[0], c_from[1], c_to[0], c_to[1], curvature=-0.10
                )
                gid = "direct_rail"
                intercity_route_groups.append(
                    {
                        "id": gid,
                        "label": f"🚄 直达高铁 {best_t['train_code']}",
                        "summary": f"{best_t['from_station']} → {best_t['to_station']} · {best_t['duration_text']} · ¥{best_t.get('lowest_price') or '--'}",
                    }
                )
                intercity_arcs.append(
                    {
                        "group_id": gid,
                        "mode": "train",
                        "code": best_t["train_code"],
                        "label": f"🚄 {best_t['train_code']} ({best_t['duration_text']})",
                        "detail": f"{best_t['from_station']} {best_t['dep_time']} → {best_t['to_station']} {best_t['arr_time']}",
                        "price": best_t.get("lowest_price") or 0,
                        "geometry": wgs_arc,
                        "gcj_geometry": gcj_arc,
                        "color": "#1D4ED8",
                        "dashed": False,
                    }
                )

            # 2. Direct European Rail Arc
            if euro_trains:
                et0 = euro_trains[0]
                wgs_arc, gcj_arc = self.build_curved_arc(
                    c_from[0], c_from[1], c_to[0], c_to[1], curvature=-0.10
                )
                gid = "euro_rail"
                intercity_route_groups.append(
                    {
                        "id": gid,
                        "label": f"🚄 欧铁 {et0['train_code']}",
                        "summary": f"{et0['from_station']} → {et0['to_station']} · {et0['duration_text']}",
                    }
                )
                intercity_arcs.append(
                    {
                        "group_id": gid,
                        "mode": "train",
                        "code": et0["train_code"],
                        "label": f"🚄 {et0['train_code']} ({et0['duration_text']})",
                        "detail": f"{et0['from_station']} {et0['dep_time']} → {et0['to_station']} {et0['arr_time']}",
                        "price": 0,
                        "geometry": wgs_arc,
                        "gcj_geometry": gcj_arc,
                        "color": "#1D4ED8",
                        "dashed": False,
                    }
                )

            # 3. Direct Flight Arc
            if flights:
                best_f = (
                    ((comparison or {}).get("flight_option") or {}).get("best")
                    or min(flights, key=lambda x: x.get("lowest_price") or 999999)
                )
                wgs_arc, gcj_arc = self.build_curved_arc(
                    c_from[0], c_from[1], c_to[0], c_to[1], curvature=0.18
                )
                gid = "direct_flight"
                intercity_route_groups.append(
                    {
                        "id": gid,
                        "label": f"✈️ 直飞 {best_f['flight_no']}",
                        "summary": f"{best_f.get('dep_airport_short','')} → {best_f.get('arr_airport_short','')} · {best_f.get('duration_text','')} · ¥{best_f.get('lowest_price') or '--'}",
                    }
                )
                intercity_arcs.append(
                    {
                        "group_id": gid,
                        "mode": "flight",
                        "code": best_f["flight_no"],
                        "label": f"✈️ {best_f['flight_no']} ({best_f.get('duration_text','')})",
                        "detail": f"{best_f.get('dep_airport_short','')} {best_f.get('dep_time','')} → {best_f.get('arr_airport_short','')} {best_f.get('arr_time_local') or best_f.get('arr_time','')}",
                        "price": best_f.get("lowest_price") or 0,
                        "geometry": wgs_arc,
                        "gcj_geometry": gcj_arc,
                        "color": "#EA580C",
                        "dashed": True,
                    }
                )

            # 4. Smart Multi-Modal Transfers (Origin -> Hub -> Destination)
            seen_hubs = set()
            for idx_tr, tr in enumerate(transfers[:4]):
                hub_city = (tr.get("hub_city") or "").strip()
                if not hub_city:
                    continue
                c_hub = self.resolve_point(hub_city, allow_network=True)
                if not c_hub:
                    continue

                l1 = tr.get("leg1") or {}
                l2 = tr.get("leg2") or {}
                l1_mode = l1.get("mode") or "train"
                l2_mode = l2.get("mode") or "flight"
                l1_code = l1.get("code") or ""
                l2_code = l2.get("code") or ""
                gid = f"transfer_{idx_tr}"

                if hub_city not in seen_hubs:
                    seen_hubs.add(hub_city)
                    intercity_markers.append(
                        self.make_point_dict(
                            name=f"{hub_city} (中转枢纽)",
                            lat=c_hub[0],
                            lon=c_hub[1],
                            category="hub",
                            subtitle=f"{tr.get('badge_text', '中转换乘')} · {tr.get('wait_text', '')}",
                            price=tr.get("total_lowest_price"),
                            badge=f"经{hub_city}",
                        )
                    )

                intercity_route_groups.append(
                    {
                        "id": gid,
                        "label": f"🔀 经{hub_city} ({l1_code}+{l2_code})",
                        "summary": f"{tr.get('badge_text', '智能中转')} · 全程 {tr.get('total_duration_text', '')} · 合计 ¥{tr.get('total_lowest_price') or '--'}",
                    }
                )

                wgs_1, gcj_1 = self.build_curved_arc(
                    c_from[0], c_from[1], c_hub[0], c_hub[1], curvature=-0.12 if l1_mode == "train" else 0.14
                )
                wgs_2, gcj_2 = self.build_curved_arc(
                    c_hub[0], c_hub[1], c_to[0], c_to[1], curvature=0.15 if l2_mode == "flight" else -0.10
                )

                intercity_arcs.append(
                    {
                        "group_id": gid,
                        "mode": l1_mode,
                        "code": l1_code,
                        "label": f"{'🚄' if l1_mode == 'train' else '✈️'} 第一程 {l1_code}",
                        "detail": f"{l1.get('from_station','')} {l1.get('dep_time','')} → {l1.get('to_station','')} {l1.get('arr_time','')}",
                        "price": l1.get("lowest_price") or 0,
                        "geometry": wgs_1,
                        "gcj_geometry": gcj_1,
                        "color": "#1D4ED8" if l1_mode == "train" else "#EA580C",
                        "dashed": l1_mode == "flight",
                    }
                )
                intercity_arcs.append(
                    {
                        "group_id": gid,
                        "mode": l2_mode,
                        "code": l2_code,
                        "label": f"{'🚄' if l2_mode == 'train' else '✈️'} 第二程 {l2_code}",
                        "detail": f"{l2.get('from_station','')} {l2.get('dep_time','')} → {l2.get('to_station','')} {l2.get('arr_time_local') or l2.get('arr_time','')}",
                        "price": l2.get("lowest_price") or 0,
                        "geometry": wgs_2,
                        "gcj_geometry": gcj_2,
                        "color": "#059669" if l2_mode == "train" else "#EA580C",
                        "dashed": l2_mode == "flight",
                    }
                )

        # 5. Train Stopover Station Chain (if stopovers were queried)
        stopover_points = self.geocode_stopovers(stopovers)

        # --------------------------------------------------------------------
        # Part B: Destination City POI + TSP + OSRM Street Route Layer
        # --------------------------------------------------------------------
        city_pois: List[Dict[str, Any]] = []
        city_center = self.resolve_point(target_city, allow_network=True) if target_city else None

        # Collect geocoded hotels (up to 5)
        for idx_h, h in enumerate(hotels[:5]):
            h_lat = h.get("lat")
            h_lon = h.get("lon")
            if not (h_lat and h_lon):
                resolved = self.resolve_point(
                    h.get("name") or "", city_hint=hotel_city, allow_network=(idx_h < 2)
                )
                if resolved:
                    h_lat, h_lon = resolved
                elif city_center:
                    # Slight deterministic spiral offset around city center if exact hotel coord missing
                    angle = idx_h * 1.3
                    h_lat = city_center[0] + 0.008 * math.cos(angle)
                    h_lon = city_center[1] + 0.008 * math.sin(angle)
            if h_lat and h_lon:
                city_pois.append(
                    self.make_point_dict(
                        name=h["name"],
                        lat=float(h_lat),
                        lon=float(h_lon),
                        category="hotel",
                        subtitle=f"{h.get('zone') or h.get('position_desc') or hotel_city} · 评分 {h.get('score') or '4.7'}分",
                        price=h.get("price"),
                        score=h.get("score"),
                        badge="推荐入住" if idx_h == 0 else f"酒店#{idx_h + 1}",
                    )
                )

        # Collect geocoded attractions (up to 7)
        for idx_a, a in enumerate(attractions[:7]):
            a_lat = a.get("lat")
            a_lon = a.get("lon")
            if not (a_lat and a_lon):
                resolved = self.resolve_point(
                    a.get("name") or "", city_hint=attraction_city, allow_network=(idx_a < 3)
                )
                if resolved:
                    a_lat, a_lon = resolved
            if a_lat and a_lon:
                city_pois.append(
                    self.make_point_dict(
                        name=a["name"],
                        lat=float(a_lat),
                        lon=float(a_lon),
                        category="attraction",
                        subtitle=f"{a.get('rank_badge') or a.get('open_time') or attraction_city}",
                        price=a.get("price"),
                        foreign_price=a.get("foreign_price") or "",
                        score=a.get("score"),
                        badge="必去景点" if idx_a == 0 else f"打卡#{idx_a + 1}",
                    )
                )

        # Build Smart City Route:
        # If we have both hotels and attractions, anchor at Top Hotel (#1) + Top 5 Attractions.
        # If only hotels, route across top 5 hotels (e.g. airport/business zone comparison chain).
        # If only attractions, route across top 6 attractions.
        route_candidates: List[Dict[str, Any]] = []
        hotel_pts = [p for p in city_pois if p["category"] == "hotel"]
        attr_pts = [p for p in city_pois if p["category"] == "attraction"]

        if hotel_pts and attr_pts:
            route_candidates = [hotel_pts[0]] + attr_pts[:5]
        elif attr_pts:
            route_candidates = attr_pts[:6]
        elif hotel_pts:
            route_candidates = hotel_pts[:5]

        city_route_data: Optional[Dict[str, Any]] = None
        if len(route_candidates) >= 2:
            ordered_waypoints, tsp_stats = self.optimize_tsp_route(
                route_candidates, fix_start=bool(hotel_pts and attr_pts)
            )
            osrm_res = self.fetch_osrm_route(ordered_waypoints, profile="driving")
            city_route_data = {
                "city": target_city,
                "waypoints": ordered_waypoints,
                "all_pois": city_pois,
                "tsp_stats": tsp_stats,
                "osrm": osrm_res,
            }
        elif len(city_pois) == 1:
            city_pois[0]["order"] = 1
            city_route_data = {
                "city": target_city,
                "waypoints": city_pois,
                "all_pois": city_pois,
                "tsp_stats": {"original_km": 0, "optimized_km": 0, "saved_km": 0, "saved_pct": 0},
                "osrm": {"ok": False, "geometry": [], "gcj_geometry": [], "legs": [], "total_distance_km": 0, "total_duration_mins": 0},
            }

        has_intercity = bool(intercity_arcs or len( stopover_points) >= 2)
        has_city_route = bool(city_route_data and city_route_data.get("waypoints"))

        default_tab = "intercity"
        if has_city_route and (
            not has_intercity
            or intent.get("only_hotel")
            or intent.get("only_attraction")
            or intent.get("needs_itinerary")
        ):
            default_tab = "city_route"
        elif len(stopover_points) >= 2:
            default_tab = "stopovers"

        return {
            "ok": has_intercity or has_city_route,
            "default_tab": default_tab,
            "has_intercity": has_intercity,
            "has_city_route": has_city_route,
            "has_stopovers": len(stopover_points) >= 2,
            "intercity": {
                "from_city": from_city,
                "to_city": to_city,
                "markers": intercity_markers,
                "arcs": intercity_arcs,
                "route_groups": intercity_route_groups,
            },
            "stopovers": {
                "train_code": intent.get("train_code_query") or "",
                "points": stopover_points,
            },
            "city_route": city_route_data,
        }

    def geocode_stopovers(self, stopovers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Resolve a list of 12306 stopover stations into ordered map coordinates."""
        if not stopovers:
            return []
        pts: List[Dict[str, Any]] = []
        for idx, s in enumerate(stopovers):
            st_name = (s.get("station_name") or "").strip()
            if not st_name:
                continue
            coord = self.resolve_point(st_name, allow_network=(idx < 12))
            if coord:
                pts.append(
                    self.make_point_dict(
                        name=f"{st_name}站",
                        lat=coord[0],
                        lon=coord[1],
                        category="stopover",
                        subtitle=f"第{s.get('station_no', idx+1)}站 · 到 {s.get('arrive_time','--')} / 发 {s.get('start_time','--')} (停{s.get('stopover_time','--')})",
                        badge=f"#{s.get('station_no', idx+1)}",
                        order_index=idx + 1,
                    )
                )
        return pts
