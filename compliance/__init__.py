"""commercial 分支的合规与变现基础设施。

两个子模块：
- source_policy：数据源白名单闸门，商业模式下禁止未授权抓取
- affiliate：联盟链接构造，凭据缺失时 fail-closed
"""

from .affiliate import (
    PARTNERS,
    Partner,
    assert_monetizable,
    build_link,
    is_monetizable,
    missing_env,
    partner_configured,
)
from .source_policy import (
    SOURCES,
    ComplianceViolation,
    Mode,
    SourcePolicy,
    UnknownSource,
    blocked_in_commercial,
    check_registry,
    current_mode,
    guard,
    report,
    source_allowed,
)

__all__ = [
    "PARTNERS",
    "SOURCES",
    "ComplianceViolation",
    "Mode",
    "Partner",
    "SourcePolicy",
    "UnknownSource",
    "assert_monetizable",
    "blocked_in_commercial",
    "build_link",
    "check_registry",
    "current_mode",
    "guard",
    "is_monetizable",
    "missing_env",
    "partner_configured",
    "report",
    "source_allowed",
]
