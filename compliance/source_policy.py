"""数据源合规闸门（commercial 分支专用）。

main 分支是个人自用工具，允许直连 12306 / 携程 / 同程；
commercial 分支要对外收费，只允许白名单内的官方接口或联盟接口。

用法：在 provider 真正发起网络请求之前调用 guard()。

    from compliance import guard
    guard("12306.left_ticket")   # 商业模式下会抛 ComplianceViolation

模式由环境变量 TRAVEL_DATA_MODE 决定，默认 personal。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class Mode(str, Enum):
    PERSONAL = "personal"
    COMMERCIAL = "commercial"


class ComplianceViolation(RuntimeError):
    """在商业模式下使用了未授权的数据源。"""


class UnknownSource(KeyError):
    """数据源未登记，无法判断是否合规 —— 一律视为不合规。"""


@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    display_name: str
    commercial_ok: bool
    basis: str
    replacement: str = ""
    code_ref: str = ""


SOURCES: Dict[str, SourcePolicy] = {
    p.source_id: p
    for p in (
        # ---------- 未授权抓取：仅 main 分支可用 ----------
        SourcePolicy(
            source_id="12306.left_ticket",
            display_name="12306 余票查询",
            commercial_ok=False,
            basis="非公开接口，需伪造 Referer 与 _jc_save_* Cookie，无任何授权",
            replacement="trip_com.affiliate（对外籍旅客正规售卖中国铁路客票）",
            code_ref="provider_12306.py:308,457",
        ),
        SourcePolicy(
            source_id="12306.station_name",
            display_name="12306 车站字典",
            commercial_ok=False,
            basis="随余票接口一并从 12306 拉取，未获授权且无本地兜底",
            replacement="内置静态站点表（随仓库分发，标注数据来源与日期）",
            code_ref="provider_12306.py:105,180",
        ),
        SourcePolicy(
            source_id="ctrip.h5api_globalsearch",
            display_name="携程 H5 全局搜索",
            commercial_ok=False,
            basis="内部接口，无公开文档与授权，商用违反平台条款",
            replacement="booking.affiliate / agoda.affiliate",
            code_ref="provider_hotels.py:162 / provider_attractions.py:152",
        ),
        SourcePolicy(
            source_id="ctrip.hotel_ssr",
            display_name="携程酒店页面 SSR",
            commercial_ok=False,
            basis="解析服务端渲染页面，属页面抓取，商用违反平台条款",
            replacement="booking.affiliate / agoda.affiliate",
            code_ref="provider_hotels.py:171",
        ),
        SourcePolicy(
            source_id="ctrip.sight_ssr",
            display_name="携程景点门票 SSR",
            commercial_ok=False,
            basis="解析服务端渲染页面，属页面抓取，商用违反平台条款",
            replacement="klook.affiliate / getyourguide.affiliate",
            code_ref="provider_attractions.py:263,402",
        ),
        SourcePolicy(
            source_id="trip_com.ssr_scrape",
            display_name="Trip.com 酒店页面 SSR",
            commercial_ok=False,
            basis="页面抓取。Trip.com 有正规联盟计划，抓取不是唯一路径",
            replacement="trip_com.affiliate（同一家公司，走联盟而非抓取）",
            code_ref="provider_hotels.py:236,267",
        ),
        SourcePolicy(
            source_id="ly.flight_ssr",
            display_name="同程机票 SSR",
            commercial_ok=False,
            basis="解析 window.__NUXT__ 与内部接口，需伪造 Referer，无授权",
            replacement="travelpayouts.flights / amadeus.flights",
            code_ref="provider_flights.py:174,271,342",
        ),
        # ---------- 白名单：商业模式可用 ----------
        SourcePolicy(
            source_id="travelpayouts.autocomplete",
            display_name="Travelpayouts 城市补全",
            commercial_ok=True,
            basis="联盟网络官方接口，本身即为变现渠道",
            code_ref="provider_flights.py:218",
        ),
        SourcePolicy(
            source_id="trip_com.affiliate",
            display_name="Trip.com 联盟",
            commercial_ok=True,
            basis="官方联盟计划，需申请 Allianceid / SID",
            code_ref="待接入",
        ),
        SourcePolicy(
            source_id="booking.affiliate",
            display_name="Booking.com 联盟",
            commercial_ok=True,
            basis="官方联盟计划，需申请 aid；有站点流量门槛",
            code_ref="待接入",
        ),
        SourcePolicy(
            source_id="agoda.affiliate",
            display_name="Agoda 联盟",
            commercial_ok=True,
            basis="官方联盟计划，需申请 cid",
            code_ref="待接入",
        ),
        SourcePolicy(
            source_id="klook.affiliate",
            display_name="Klook 联盟",
            commercial_ok=True,
            basis="官方联盟计划，需申请 aid",
            code_ref="待接入",
        ),
        SourcePolicy(
            source_id="amadeus.flights",
            display_name="Amadeus Self-Service",
            commercial_ok=True,
            basis="官方开发者 API，有免费额度与明码标价",
            code_ref="待接入",
        ),
        SourcePolicy(
            source_id="opendata_ch.connections",
            display_name="瑞士铁路开放数据",
            commercial_ok=True,
            basis="瑞士联邦官方开放数据接口，可商用",
            code_ref="provider_global_transit.py:184",
        ),
        SourcePolicy(
            source_id="er_api.fx",
            display_name="open.er-api.com 汇率",
            commercial_ok=True,
            basis="公开免费汇率接口，条款允许商用",
            code_ref="provider_global_transit.py:446",
        ),
        SourcePolicy(
            source_id="frankfurter.fx",
            display_name="Frankfurter (ECB) 汇率",
            commercial_ok=True,
            basis="基于欧洲央行公开数据，开源可商用",
            code_ref="provider_global_transit.py",
        ),
        SourcePolicy(
            source_id="osrm.route",
            display_name="OSRM 路径规划",
            commercial_ok=True,
            basis="开源路由引擎。公共实例有限流，商用应自建或换 Mapbox",
            code_ref="provider_map.py（该文件尚未提交）",
        ),
        SourcePolicy(
            source_id="nominatim.geocode",
            display_name="Nominatim 地理编码",
            commercial_ok=True,
            basis="开源服务，使用政策限 1 req/s 且需标注来源，商用建议自建",
            code_ref="provider_map.py（该文件尚未提交）",
        ),
    )
}


def current_mode() -> Mode:
    raw = (os.environ.get("TRAVEL_DATA_MODE") or Mode.PERSONAL.value).strip().lower()
    try:
        return Mode(raw)
    except ValueError:
        return Mode.PERSONAL


def guard(source_id: str, mode: Optional[Mode] = None) -> SourcePolicy:
    """在发起请求前校验数据源。商业模式下违规直接抛异常。"""
    policy = SOURCES.get(source_id)
    if policy is None:
        raise UnknownSource(
            f"数据源 {source_id!r} 未登记，无法判断合规性；"
            f"请先在 compliance/source_policy.py 的 SOURCES 中登记。"
        )
    effective = mode or current_mode()
    if effective is Mode.COMMERCIAL and not policy.commercial_ok:
        raise ComplianceViolation(
            f"[{source_id}] 禁止在商业模式使用：{policy.basis}。"
            f"应改用：{policy.replacement or '白名单数据源'}。"
        )
    return policy


def blocked_in_commercial() -> List[SourcePolicy]:
    return [p for p in SOURCES.values() if not p.commercial_ok]


def source_allowed(source_id: str, mode: Optional[Mode] = None) -> bool:
    """不抛异常地判断数据源在当前模式下是否可用。

    适合「有白名单兜底方案」的场景：商业模式下跳过抓取，直接走允许的路径。
    """
    try:
        guard(source_id, mode=mode)
    except (ComplianceViolation, UnknownSource):
        return False
    return True


def check_registry() -> List[str]:
    """登记表自洽性检查。返回问题列表，空列表表示通过。"""
    problems: List[str] = []
    for p in SOURCES.values():
        if not p.commercial_ok and not p.replacement:
            problems.append(f"{p.source_id}: 未授权数据源必须给出 replacement")
        if "." not in p.source_id:
            problems.append(f"{p.source_id}: source_id 应形如 'domain.action'")
        if not p.basis:
            problems.append(f"{p.source_id}: 缺少合规依据说明")
    return problems


def report() -> str:
    lines = [
        f"数据源登记表（当前模式：{current_mode().value}）",
        "",
        f"{'数据源':<28}{'商业模式':<10}依据 / 替代方案",
        "-" * 96,
    ]
    for p in sorted(SOURCES.values(), key=lambda x: (x.commercial_ok, x.source_id)):
        flag = "允许" if p.commercial_ok else "禁止"
        detail = p.basis if p.commercial_ok else f"{p.basis} → {p.replacement}"
        lines.append(f"{p.source_id:<28}{flag:<10}{detail}")
    blocked = blocked_in_commercial()
    lines += ["", f"商业模式下必须移除/替换的数据源：{len(blocked)} 个"]
    return "\n".join(lines)
