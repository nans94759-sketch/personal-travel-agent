# 合规改造清单（commercial 分支）

目标：把「个人自用的抓取工具」改造成「可以对外收费的产品」，且不踩平台条款、不踩数据合规、不出现零佣金漏损。

执行顺序就是下表顺序 —— 从数据源开始，最后才碰收款。

---

## 一、必须移除或替换的数据源

`compliance/source_policy.py` 是唯一事实来源，下表与代码保持一致。当前商业模式下被拦截的共 7 个。

| 数据源 | 现在怎么拿 | 代码位置 | 换成什么 | 前置条件 |
| :-- | :-- | :-- | :-- | :-- |
| `12306.left_ticket` | 伪造 `Referer` + `_jc_save_*` Cookie 请求 `leftTicket/query` | `provider_12306.py:308,457` | `trip_com.affiliate` | Trip.com 联盟账号 |
| `12306.station_name` | 启动时现拉 `station_name.js` | `provider_12306.py:105,180` | 内置静态站点表 | 无（随仓库分发） |
| `ctrip.h5api_globalsearch` | 内部 H5 接口 | `provider_hotels.py:162`、`provider_attractions.py:152` | `booking.affiliate` / `agoda.affiliate` | 联盟账号 |
| `ctrip.hotel_ssr` | 解析 SSR 页面 | `provider_hotels.py:171` | 同上 | 联盟账号 |
| `ctrip.sight_ssr` | 解析 SSR 页面 | `provider_attractions.py:263,402` | `klook.affiliate` | Klook 联盟账号 |
| `trip_com.ssr_scrape` | 解析 SSR 页面 | `provider_hotels.py:236,267` | `trip_com.affiliate` | 同一家公司，走联盟不抓取 |
| `ly.flight_ssr` | 解析 `window.__NUXT__` + 内部接口 | `provider_flights.py:174,271,342` | `travelpayouts.flights` / `amadeus.flights` | Travelpayouts 账号 |

保留可用的白名单（已登记、无需改造）：`travelpayouts.autocomplete`、`opendata_ch.connections`、`er_api.fx`、`frankfurter.fx`、`osrm.route`、`nominatim.geocode`。

> `osrm.route` 与 `nominatim.geocode` 虽在白名单，但公共实例有限流、Nominatim 使用政策限 1 req/s 且要求署名。有真实流量后必须自建或换 Mapbox（有免费额度）。

---

## 二、代码接入方式

闸门不是装饰，必须在发请求前真的调用：

```python
from compliance import guard

def query_trains(...):
    guard("12306.left_ticket")   # 商业模式下抛 ComplianceViolation
    ...
```

出站预订链接必须经过联盟构造：

```python
from compliance import build_link, assert_monetizable

link = build_link("https://www.trip.com/trains", "trip_com",
                  extra={"trip_sub1": itinerary_id})
if link is None:
    # 凭据缺失：不展示下单按钮，而不是展示裸链接
    ...
assert_monetizable(link, "trip_com")
```

两条铁律：

1. **fail-closed**。没有联盟凭据时宁可不给链接，也不给裸链接。裸链接等于免费给 OTA 导流。
2. **未登记即拒绝**。任何新数据源先写进 `SOURCES`，否则 `guard()` 直接抛 `UnknownSource`。

---

## 三、对外必须有的页面

上线前缺一不可（这也是 Creem / Stripe 审核时会看的）：

- **Terms of Service** —— 明确你只是信息聚合与行程建议，不承担出票与履约责任。
- **Privacy Policy** —— 收集了什么（对话内容、行程参数）、存多久、给谁（联盟方会拿到点击）。
- **Refund Policy** —— 建议：交付物未生成前 24 小时内可全额退；已生成 PDF 行程的按次不退。
- **免责声明** —— 价格与余票实时变动，以出票方为准；签证与入境政策以官方为准。

---

## 四、数据合规

| 事项 | 触发条件 | 处理 |
| :-- | :-- | :-- |
| GDPR | 有欧盟用户 | Cookie 同意、数据删除请求通道、处理者清单 |
| PIPL | 用户数据存于中国境内服务器 | 告知同意、最小必要；面向境外用户时优先把服务部署在境外 |
| 日志 | 记录 IP 与对话 | 设保留期（建议 90 天），不落原始支付信息 |
| 支付 | 收美元 | 用 MoR（Creem / Lemon Squeezy），由对方承担税务与退款合规 |

---

## 五、不能说的话

避免以下表述，它们会把你从「信息聚合」变成「承诺履约」：

- ❌「实时出票」「保证有票」→ ✅「实时余票参考，以出票方为准」
- ❌「最便宜」→ ✅「当前可查询到的方案中较低的一档」
- ❌「官方合作」→ ✅「通过 Trip.com 联盟提供预订入口」（未签约前不要提）

---

## 六、验收标准

```bash
TRAVEL_DATA_MODE=commercial python -m compliance.selftest   # 必须全过
```

再加三条人工检查：

1. 全站搜索 `kyfw.12306.cn`、`m.ctrip.com`、`www.ly.com`，在商业模式下应无任何运行时请求。
2. 每个「去预订」按钮的 URL 都必须带联盟参数（`Allianceid` / `aid` / `cid` / `marker`）。
3. 四个合规页面都能从首页页脚点进去。
