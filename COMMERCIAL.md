# commercial 分支：合规与收费

`main` 是个人自用（可抓取，中文，不对外）。`commercial` 是对外收费（只许白名单数据源，英文，收美元）。

## 商业模式下必须替换的 7 个抓取源

| 现在 | 换成 |
| :-- | :-- |
| 12306 余票、站点表 | Trip.com 联盟 / 内置静态站点表 |
| 携程酒店、景点、全局搜索 | Booking / Agoda / Klook 联盟 |
| 同程机票 SSR | Travelpayouts / Amadeus |
| Trip.com 酒店 SSR | Trip.com 联盟（同一家公司，别抓） |

白名单可直接用：`travelpayouts`、`er_api`、`frankfurter`、`opendata_ch`、`osrm`、`nominatim`（后两个商用要自建或换 Mapbox）。

## 用法

```python
guard("12306.left_ticket")        # 商业模式下抛 ComplianceViolation
if source_allowed("ly.flight_ssr"): ...   # 有白名单兜底时用这个
link = build_link(url, "trip_com")        # 无凭据返回 None，绝不给裸链接
```

环境变量：`TRAVEL_DATA_MODE=commercial` 开闸；`TRIP_COM_ALLIANCEID` / `TRIP_COM_SID` 等填联盟凭据。

合并前必过：`python -m compliance.selftest`（9 项）。

## 定价

入境旅客，收美元。主力 `$39` 单次行程包（PDF + `.ics` + 1 次修订）；`$19/月` 自助；`$149` 顾问单。
月入 `$2,000` ≈ 40 单行程包 + 3 单顾问。毛利率 >90%，不做纯订阅（旅行低频，留存撑不住）。

## 上线还缺

1. 7 个抓取源换成联盟接口（现在商业模式下会返回 `503 compliance_blocked`）。
2. 英文界面（当前 `lang="zh-CN"`）。
3. 付费墙、四个合规页（ToS / Privacy / Refund / 免责）、Creem 收款。
