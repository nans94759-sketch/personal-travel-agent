"""联盟链接构造（commercial 分支专用）。

原则：fail-closed。
没有配置 partner 凭据时返回 None，绝不把裸链接当成变现链接返回。
这样宁可少一个链接，也不会出现「用户点了、你收不到钱」的静默漏损。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .source_policy import ComplianceViolation, Mode, current_mode


@dataclass(frozen=True)
class Partner:
    key: str
    label: str
    params: Tuple[Tuple[str, str], ...]
    payout_note: str


PARTNERS: Dict[str, Partner] = {
    p.key: p
    for p in (
        Partner(
            key="trip_com",
            label="Trip.com 联盟",
            params=(("Allianceid", "TRIP_COM_ALLIANCEID"), ("SID", "TRIP_COM_SID")),
            payout_note="火车票/机票/酒店，适合外籍旅客预订中国铁路",
        ),
        Partner(
            key="booking",
            label="Booking.com 联盟",
            params=(("aid", "BOOKING_AID"),),
            payout_note="酒店，通常需站点有一定流量才能通过审核",
        ),
        Partner(
            key="agoda",
            label="Agoda 联盟",
            params=(("cid", "AGODA_CID"),),
            payout_note="亚太区酒店覆盖较好",
        ),
        Partner(
            key="klook",
            label="Klook 联盟",
            params=(("aid", "KLOOK_AID"),),
            payout_note="景点门票与一日游，客单低但转化高",
        ),
        Partner(
            key="travelpayouts",
            label="Travelpayouts",
            params=(("marker", "TRAVELPAYOUTS_MARKER"),),
            payout_note="聚合多个 OTA 的联盟网络，代码已在用其补全接口",
        ),
    )
}


def partner_configured(key: str) -> bool:
    partner = PARTNERS.get(key)
    if partner is None:
        return False
    return all(os.environ.get(env) for _, env in partner.params)


def missing_env(key: str) -> List[str]:
    partner = PARTNERS.get(key)
    if partner is None:
        return [f"未知 partner: {key}"]
    return [env for _, env in partner.params if not os.environ.get(env)]


def build_link(
    base_url: str,
    partner_key: str,
    extra: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """在 base_url 上附加联盟参数。凭据缺失时返回 None（fail-closed）。"""
    partner = PARTNERS.get(partner_key)
    if partner is None or not base_url:
        return None

    values = {}
    for param_name, env_name in partner.params:
        val = os.environ.get(env_name)
        if not val:
            return None
        values[param_name] = val

    if extra:
        values.update({k: v for k, v in extra.items() if v})

    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(values)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def is_monetizable(url: str) -> bool:
    """判断链接是否已带上任一联盟参数。"""
    if not url:
        return False
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
    for partner in PARTNERS.values():
        for param_name, _ in partner.params:
            if query.get(param_name):
                return True
    return False


def assert_monetizable(
    url: str, partner_key: str = "", mode: Optional[Mode] = None
) -> str:
    """商业模式下强制要求出站预订链接可结算。"""
    effective = mode or current_mode()
    if effective is Mode.COMMERCIAL and not is_monetizable(url):
        hint = ""
        if partner_key and missing_env(partner_key):
            hint = f"缺少环境变量：{', '.join(missing_env(partner_key))}"
        raise ComplianceViolation(
            f"商业模式下出站预订链接未携带联盟参数：{url!r}。{hint}"
        )
    return url
