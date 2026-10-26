"""合规闸门自测。纯标准库，不依赖 aiohttp / provider_*，可独立运行。

    python -m compliance.selftest

任何一条失败即返回非零退出码，适合直接挂到 CI 上拦截合并。
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Tuple

from .affiliate import (
    PARTNERS,
    assert_monetizable,
    build_link,
    is_monetizable,
    partner_configured,
)
from .source_policy import (
    ComplianceViolation,
    Mode,
    UnknownSource,
    blocked_in_commercial,
    check_registry,
    guard,
    report,
)

CHECKS: List[Tuple[str, Callable[[], None]]] = []


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def wrap(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((name, fn))
        return fn

    return wrap


def _expect_raise(exc_type: type, fn: Callable[[], None], label: str) -> None:
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(f"{label}：预期抛出 {exc_type.__name__}，实际未抛出")


@check("登记表自洽性")
def _registry() -> None:
    problems = check_registry()
    assert not problems, "；".join(problems)
    assert len(blocked_in_commercial()) >= 5, "未授权数据源数量异常，登记表可能被误改"


@check("个人模式允许抓取")
def _personal_allows_scrape() -> None:
    for sid in ("12306.left_ticket", "ctrip.hotel_ssr", "ly.flight_ssr"):
        guard(sid, mode=Mode.PERSONAL)


@check("商业模式拦截抓取")
def _commercial_blocks_scrape() -> None:
    for sid in ("12306.left_ticket", "ctrip.hotel_ssr", "ly.flight_ssr"):
        _expect_raise(
            ComplianceViolation,
            lambda s=sid: guard(s, mode=Mode.COMMERCIAL),
            f"guard({sid})",
        )


@check("商业模式放行白名单")
def _commercial_allows_whitelist() -> None:
    for sid in ("travelpayouts.autocomplete", "er_api.fx", "trip_com.affiliate"):
        guard(sid, mode=Mode.COMMERCIAL)


@check("未登记数据源一律拒绝")
def _unknown_source() -> None:
    _expect_raise(
        UnknownSource,
        lambda: guard("some.random.scraper", mode=Mode.COMMERCIAL),
        "guard(未登记源)",
    )


@check("联盟凭据缺失时 fail-closed")
def _affiliate_fail_closed() -> None:
    for key in PARTNERS:
        for env in [e for _, e in PARTNERS[key].params]:
            os.environ.pop(env, None)
    assert not partner_configured("trip_com")
    assert build_link("https://www.trip.com/trains", "trip_com") is None


@check("联盟凭据齐全时正确拼接")
def _affiliate_builds() -> None:
    os.environ["TRIP_COM_ALLIANCEID"] = "test-alliance"
    os.environ["TRIP_COM_SID"] = "test-sid"
    url = build_link(
        "https://www.trip.com/trains?from=beijing&to=shanghai",
        "trip_com",
        extra={"trip_sub1": "itinerary-001"},
    )
    assert url is not None
    for token in ("Allianceid=test-alliance", "SID=test-sid", "from=beijing"):
        assert token in url, f"拼接结果缺少 {token}：{url}"
    assert is_monetizable(url)
    assert_monetizable(url, "trip_com")
    for env in ("TRIP_COM_ALLIANCEID", "TRIP_COM_SID"):
        os.environ.pop(env, None)


@check("裸链接在商业模式下被拒绝")
def _bare_link_rejected() -> None:
    bare = "https://www.trip.com/trains"
    assert not is_monetizable(bare)
    _expect_raise(
        ComplianceViolation,
        lambda: assert_monetizable(bare, "trip_com", mode=Mode.COMMERCIAL),
        "assert_monetizable(裸链接)",
    )
    assert assert_monetizable(bare, mode=Mode.PERSONAL) == bare


def main() -> int:
    failures = 0
    os.environ["TRAVEL_DATA_MODE"] = Mode.COMMERCIAL.value
    print("compliance selftest")
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
