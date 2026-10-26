"""Curated destination data for travellers, bilingual.

Every `station` here must exist in the bundled 12306 station dictionary
(`data/12306_stations.json`), which is static data and therefore allowed in
commercial mode. `test_inbound.py` enforces that invariant.

No live scraping happens anywhere in this package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional

BUNDLED_STATIONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "12306_stations.json",
)


@dataclass(frozen=True)
class City:
    key: str
    en: str
    zh: str
    station: str
    airport: str
    lat: float
    lon: float
    days: int
    region: str
    highlights: List[str]
    highlights_zh: List[str]
    note: str = ""
    note_zh: str = ""


CITIES: Dict[str, City] = {
    c.key: c
    for c in (
        City("beijing", "Beijing", "北京", "北京南", "PEK", 39.90, 116.41, 4, "North",
             ["Forbidden City and Jingshan Park", "Mutianyu Great Wall", "Hutong walk in Shichahai"],
             ["故宫与景山公园", "慕田峪长城", "什刹海胡同漫步"]),
        City("shanghai", "Shanghai", "上海", "上海虹桥", "PVG", 31.23, 121.47, 3, "East",
             ["The Bund at dusk", "Former French Concession", "Yu Garden and old town"],
             ["傍晚外滩", "原法租界梧桐区", "豫园与老城厢"]),
        City("xian", "Xi'an", "西安", "西安北", "XIY", 34.34, 108.94, 3, "Northwest",
             ["Terracotta Army", "City Wall by bike", "Muslim Quarter food street"],
             ["兵马俑", "城墙骑行", "回民街小吃"]),
        City("chengdu", "Chengdu", "成都", "成都东", "CTU", 30.66, 104.07, 3, "Southwest",
             ["Giant Panda Base, early morning", "People's Park teahouse", "Sichuan hotpot"],
             ["清晨大熊猫基地", "人民公园茶馆", "四川火锅"]),
        City("chongqing", "Chongqing", "重庆", "重庆北", "CKG", 29.56, 106.55, 2, "Southwest",
             ["Hongyadong at night", "Yangtze cableway", "Ci Qi Kou old town"],
             ["夜晚洪崖洞", "长江索道", "磁器口古镇"]),
        City("hangzhou", "Hangzhou", "杭州", "杭州东", "HGH", 30.27, 120.16, 2, "East",
             ["West Lake loop", "Lingyin Temple", "Longjing tea village"],
             ["西湖环湖", "灵隐寺", "龙井茶村"]),
        City("suzhou", "Suzhou", "苏州", "苏州北", "SHA", 31.30, 120.58, 2, "East",
             ["Humble Administrator's Garden", "Pingjiang Road", "Silk Museum"],
             ["拙政园", "平江路", "苏州丝绸博物馆"],
             note="No airport; fly into Shanghai (SHA/PVG) and take the train.",
             note_zh="本地无机场，先飞上海（SHA/PVG）再坐高铁。"),
        City("nanjing", "Nanjing", "南京", "南京南", "NKG", 32.06, 118.80, 2, "East",
             ["Sun Yat-sen Mausoleum", "Ming city wall", "Nanjing Museum"],
             ["中山陵", "明城墙", "南京博物院"]),
        City("guilin", "Guilin", "桂林", "桂林北", "KWL", 25.27, 110.29, 2, "South",
             ["Li River cruise to Yangshuo", "Longji rice terraces", "Reed Flute Cave"],
             ["漓江游船到阳朔", "龙脊梯田", "芦笛岩"]),
        City("yangshuo", "Yangshuo", "阳朔", "阳朔", "KWL", 24.78, 110.49, 2, "South",
             ["Yulong River bamboo raft", "Cycling the countryside", "Xianggong Hill sunset"],
             ["遇龙河竹筏", "乡野骑行", "相公山日落"],
             note="Small station, fewer departures. Guilin is the bigger rail hub.",
             note_zh="小站，车次较少，桂林是更大的铁路枢纽。"),
        City("zhangjiajie", "Zhangjiajie", "张家界", "张家界西", "DYG", 29.12, 110.48, 3, "Central",
             ["Zhangjiajie National Forest Park", "Tianmen Mountain glass walk", "Baofeng Lake"],
             ["张家界国家森林公园", "天门山玻璃栈道", "宝峰湖"]),
        City("changsha", "Changsha", "长沙", "长沙南", "CSX", 28.23, 112.94, 2, "Central",
             ["Orange Isle", "Yuelu Academy", "Pozi Street night food"],
             ["橘子洲", "岳麓书院", "坡子街夜市"]),
        City("lijiang", "Lijiang", "丽江", "丽江", "LJG", 26.87, 100.23, 2, "Southwest",
             ["Old Town at dawn", "Jade Dragon Snow Mountain", "Baisha village"],
             ["清晨古城", "玉龙雪山", "白沙古镇"]),
        City("dali", "Dali", "大理", "大理", "DLU", 25.61, 100.27, 2, "Southwest",
             ["Erhai Lake loop", "Three Pagodas", "Xizhou old town"],
             ["环洱海", "崇圣寺三塔", "喜洲古镇"]),
        City("kunming", "Kunming", "昆明", "昆明南", "KMG", 25.04, 102.72, 2, "Southwest",
             ["Stone Forest", "Green Lake Park", "Yunnan Provincial Museum"],
             ["石林", "翠湖公园", "云南省博物馆"]),
        City("guangzhou", "Guangzhou", "广州", "广州南", "CAN", 23.13, 113.26, 2, "South",
             ["Chen Clan Ancestral Hall", "Shamian Island", "Dim sum breakfast"],
             ["陈家祠", "沙面岛", "早茶"]),
        City("shenzhen", "Shenzhen", "深圳", "深圳北", "SZX", 22.54, 114.06, 2, "South",
             ["OCT Loft art district", "Dafen oil painting village", "Lianhuashan Park"],
             ["华侨城创意园", "大芬油画村", "莲花山公园"]),
        City("xiamen", "Xiamen", "厦门", "厦门北", "XMN", 24.48, 118.09, 2, "Southeast",
             ["Gulangyu island", "Nanputuo Temple", "Huandao Road coast"],
             ["鼓浪屿", "南普陀寺", "环岛路海岸"]),
        City("qingdao", "Qingdao", "青岛", "青岛北", "TAO", 36.07, 120.38, 2, "East",
             ["Badaguan architecture", "Trestle Bridge", "Tsingtao Brewery museum"],
             ["八大关建筑群", "栈桥", "青岛啤酒博物馆"]),
        City("huangshan", "Huangshan", "黄山", "黄山北", "TXN", 29.71, 118.34, 2, "East",
             ["Yellow Mountain sunrise", "Hongcun village", "Tunxi old street"],
             ["黄山日出", "宏村", "屯溪老街"]),
        City("pingyao", "Pingyao", "平遥", "平遥古城", "TYN", 37.20, 112.18, 2, "North",
             ["Ancient city wall", "Rishengchang exchange house", "Shuanglin Temple"],
             ["古城墙", "日升昌票号", "双林寺"],
             note="Nearest airport is Taiyuan (TYN), about 1.5h by road.",
             note_zh="最近机场是太原（TYN），约 1.5 小时车程。"),
        City("luoyang", "Luoyang", "洛阳", "洛阳龙门", "LYA", 34.62, 112.45, 2, "Central",
             ["Longmen Grottoes", "White Horse Temple", "Old town water banquet"],
             ["龙门石窟", "白马寺", "老城水席"]),
        City("dunhuang", "Dunhuang", "敦煌", "敦煌", "DNH", 40.14, 94.66, 2, "Northwest",
             ["Mogao Caves", "Singing Sand Dunes", "Yumen Pass"],
             ["莫高窟", "鸣沙山月牙泉", "玉门关"]),
        City("harbin", "Harbin", "哈尔滨", "哈尔滨西", "HRB", 45.80, 126.53, 2, "Northeast",
             ["Ice and Snow World (winter)", "Saint Sophia Cathedral", "Central Street"],
             ["冰雪大世界（冬季）", "圣索菲亚教堂", "中央大街"]),
    )
}


def get(key: str) -> Optional[City]:
    return CITIES.get(key.strip().lower())


def resolve(text: str) -> Optional[City]:
    """Match an English name, Chinese name, or key, case-insensitively."""
    if not text:
        return None
    q = text.strip().lower()
    if q in CITIES:
        return CITIES[q]
    for city in CITIES.values():
        if q == city.en.lower() or q == city.zh:
            return city
    for city in CITIES.values():
        if q in city.en.lower() or q in city.zh:
            return city
    return None


def display_name(city: City, lang: str) -> str:
    return city.zh if lang == "zh" else city.en


def highlights_for(city: City, lang: str) -> List[str]:
    return city.highlights_zh if lang == "zh" and city.highlights_zh else city.highlights


def popular(limit: int = 8) -> List[City]:
    order = ["beijing", "shanghai", "xian", "chengdu", "guilin", "hangzhou", "zhangjiajie", "chongqing"]
    return [CITIES[k] for k in order[:limit]]
