"""Self-tests for the inbound product. Stdlib only, no network.

    python -m inbound.test_inbound

Guards two things that are easy to break: station names drifting away from the
bundled dictionary, and the inbound package quietly pulling in a scraper.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, List, Tuple

from . import CITIES, attach_links, build_plan, render, resolve
from . import corridors
from .data import BUNDLED_STATIONS
from .i18n import STRINGS
from .planner import allocate_days, order_cities

CHECKS: List[Tuple[str, Callable[[], None]]] = []


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def wrap(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((name, fn))
        return fn

    return wrap


@check("每个城市的车站都在内置站点表里")
def _stations_exist() -> None:
    with open(BUNDLED_STATIONS, encoding="utf-8") as f:
        names = json.load(f)["by_name"]
    missing = [(c.key, c.station) for c in CITIES.values() if c.station not in names]
    assert not missing, f"站点不在内置字典中: {missing}"


@check("城市名解析支持英文/中文/键名")
def _resolve() -> None:
    assert resolve("Beijing").key == "beijing"
    assert resolve("上海").key == "shanghai"
    assert resolve("xian").key == "xian"
    assert resolve("Xi'an").key == "xian"
    assert resolve("Atlantis") is None


@check("走廊表大小写与顺序无关")
def _corridor_lookup() -> None:
    assert corridors.rail_minutes("shanghai", "hangzhou") == 60
    assert corridors.rail_minutes("hangzhou", "shanghai") == 60
    assert corridors.rail_minutes("xian", "luoyang") == corridors.rail_minutes("luoyang", "xian")


@check("行程天数与请求一致")
def _day_count() -> None:
    for days in (3, 5, 7, 12):
        plan = build_plan(["beijing", "shanghai", "xian"], "2026-11-02", days)
        assert plan["ok"], plan.get("error")
        assert len(plan["days"]) == days, f"{days} 天却生成了 {len(plan['days'])} 天"
        assert sum(c["days"] for c in plan["cities"]) == days


@check("城市数超过天数时自动截断")
def _truncate() -> None:
    plan = build_plan(["beijing", "shanghai", "xian", "chengdu"], "2026-11-02", 2)
    assert len(plan["cities"]) == 2
    assert len(plan["days"]) == 2


@check("日期连续且无空洞")
def _dates_contiguous() -> None:
    import datetime

    plan = build_plan(["beijing", "xian"], "2026-11-02", 6)
    dates = [datetime.date.fromisoformat(d["date"]) for d in plan["days"]]
    for a, b in zip(dates, dates[1:]):
        assert (b - a).days == 1, f"日期不连续: {a} -> {b}"


@check("路线按最近邻排序，首站不变")
def _ordering() -> None:
    ordered = order_cities(["beijing", "guilin", "xian", "shanghai"])
    assert ordered[0].key == "beijing"
    assert len(ordered) == 4


@check("长距离自动改用航班")
def _flight_fallback() -> None:
    plan = build_plan(["beijing", "dunhuang"], "2026-11-02", 4)
    assert plan["legs"][0]["mode"] == "flight", plan["legs"][0]


@check("没有联盟凭据时链接为空且不报错")
def _links_fail_closed() -> None:
    for env in ("TRIP_COM_ALLIANCEID", "TRIP_COM_SID"):
        os.environ.pop(env, None)
    plan = attach_links(build_plan(["beijing", "xian"], "2026-11-02", 5))
    assert plan["affiliate_ready"] is False
    assert all(leg.get("booking_url") is None for leg in plan["legs"])
    assert all(city.get("hotel_url") is None for city in plan["cities"])


@check("有凭据时链接带上联盟参数")
def _links_present() -> None:
    os.environ["TRIP_COM_ALLIANCEID"] = "test-a"
    os.environ["TRIP_COM_SID"] = "test-s"
    plan = attach_links(build_plan(["beijing", "xian"], "2026-11-02", 5))
    assert plan["affiliate_ready"] is True
    assert "Allianceid=test-a" in plan["legs"][0]["booking_url"]
    for env in ("TRIP_COM_ALLIANCEID", "TRIP_COM_SID"):
        os.environ.pop(env, None)


@check("渲染出的 HTML 包含标题与免责声明")
def _render() -> None:
    plan = attach_links(build_plan(["shanghai", "hangzhou"], "2026-11-02", 5))
    page = render(plan)
    assert plan["title"] in page
    assert "indicative" in page.lower()
    assert "passport" in page.lower()


@check("inbound 包不导入任何抓取型 provider")
def _no_scrapers() -> None:
    banned = ("provider_12306", "provider_flights", "provider_hotels", "provider_attractions")
    for name, module in list(sys.modules.items()):
        if not name.startswith("inbound"):
            continue
        for attr in vars(module).values():
            mod_name = getattr(attr, "__name__", "")
            assert not any(b in mod_name for b in banned), f"{name} 引入了 {mod_name}"


@check("分配天数不少于城市数")
def _allocation_floor() -> None:
    ordered = order_cities(["beijing", "shanghai", "xian", "chengdu", "guilin"])
    alloc = allocate_days(ordered, 5)
    assert all(a >= 1 for a in alloc)
    assert sum(alloc) == 5


@check("每个城市都有等长的中英文亮点")
def _bilingual_content() -> None:
    for c in CITIES.values():
        assert len(c.highlights) == len(c.highlights_zh), f"{c.key} 中英文亮点数量不一致"
        assert all(h.strip() for h in c.highlights_zh), f"{c.key} 有空的亮点"
        assert all(ord(h[0]) > 127 for h in c.highlights_zh), f"{c.key} 的亮点看起来不是中文"


@check("中英文词条键位一致")
def _i18n_parity() -> None:
    en = set(STRINGS["en"])
    zh = set(STRINGS["zh"])
    assert not (en - zh), f"中文缺少词条: {sorted(en - zh)}"
    assert not (zh - en), f"英文缺少词条: {sorted(zh - en)}"


@check("中文模式输出中文行程")
def _zh_plan() -> None:
    plan = build_plan(["shanghai", "hangzhou"], "2026-11-02", 5, "balanced", "zh")
    assert plan["lang"] == "zh"
    assert "天中国行" in plan["title"], plan["title"]
    first = plan["days"][0]["items"][0]
    assert "抵达" in first["title"], first["title"]
    assert plan["cities"][0]["name"] == "上海"
    leg = plan["legs"][0]
    assert leg["from"] == "上海" and leg["to"] == "杭州"


@check("中文可打印页用中文标签")
def _zh_render() -> None:
    plan = attach_links(build_plan(["beijing"], "2026-11-02", 3, "balanced", "zh"))
    page = render(plan)
    assert 'lang="zh-CN"' in page
    assert "逐日安排" in page
    assert "护照" in page


@check("语言参数非法时回退英文")
def _lang_fallback() -> None:
    plan = build_plan(["beijing"], "2026-11-02", 3, "balanced", "fr")
    assert plan["lang"] == "en"


def main() -> int:
    failures = 0
    print("inbound selftest")
    print("-" * 60)
    for name, fn in CHECKS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}\n      {exc}")
        else:
            print(f"ok    {name}")
    print("-" * 60)
    if failures:
        print(f"{failures} 项失败")
        return 1
    print(f"{len(CHECKS)} 项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
