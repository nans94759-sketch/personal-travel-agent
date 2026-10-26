# 双分支模型

本仓库从 2026-10-26 起分成两条互不干扰的产品线。

| | `main` | `commercial` |
| :-- | :-- | :-- |
| **定位** | 个人自用工具 | 合规 + 收费产品 |
| **使用者** | 你自己 | 免签入境中国的外国旅客 |
| **数据源** | 直连 12306 / 携程 / 同程（抓取） | 仅 `compliance` 白名单（官方接口 / 联盟接口） |
| **界面语言** | 中文 | 英文优先 |
| **是否收费** | 否 | 是 |
| **能否公开部署** | 否 | 是 |
| **结算货币** | — | USD |

## 为什么要分叉

抓取 12306、携程、同程的数据用来自己查票，属于个人使用；一旦对外收费，就同时踩上三件事：平台条款、反不正当竞争、以及"查完票用户去别处下单、你零佣金"的漏损。

所以两条线必须物理隔离：**抓取代码只允许留在 `main`，永远不进 `commercial`。**

## 合并规则

1. **通用 bugfix**：先修 `main`，再 `cherry-pick` 到 `commercial`。方向只能是 main → commercial。
2. **抓取类代码**：只允许存在于 `main`。任何把 `provider_*.py` 的抓取逻辑带进 `commercial` 的合并请求直接拒绝。
3. **商业功能**（付费墙、i18n、联盟链接、合规页）：只存在于 `commercial`，不回合并 `main`。
4. **合并到 `commercial` 前必须通过**：

   ```bash
   python -m compliance.selftest
   ```

   它会在商业模式下拦截所有未授权数据源，并检查出站预订链接是否都带上了联盟参数。建议直接挂 CI。

## 分支命名

| 前缀 | 基于 | 用途 |
| :-- | :-- | :-- |
| `personal/xxx` | `main` | 个人功能、抓取适配、本地体验 |
| `feat/xxx` | `commercial` | 商业功能 |
| `fix/xxx` | `main` | 通用 bug，修完 cherry-pick 过去 |

## 环境变量

`commercial` 分支必须设置：

```bash
TRAVEL_DATA_MODE=commercial        # 打开合规闸门
TRIP_COM_ALLIANCEID=xxx            # Trip.com 联盟
TRIP_COM_SID=xxx
BOOKING_AID=xxx                    # 可选
AGODA_CID=xxx                      # 可选
KLOOK_AID=xxx                      # 可选
TRAVELPAYOUTS_MARKER=xxx           # 可选
```

`main` 分支不需要设置，默认 `TRAVEL_DATA_MODE=personal`，行为与分叉前完全一致。

## 当前状态

**已完成**

- 合规闸门（`compliance/source_policy.py`）：18 个数据源登记，商业模式拦截 7 个未授权抓取源。
- 联盟链接构造（`compliance/affiliate.py`）：fail-closed，无凭据不给链接。
- 自测 9 项全过：`python -m compliance.selftest`
- **闸门已接入 provider**：`provider_12306` / `provider_hotels` / `provider_attractions` / `provider_flights` 的请求入口均已埋 `guard()`；`server.py` 加了中间件，违规统一返回 `503 compliance_blocked`，不会伪装成「没有数据」。
- 站点表在商业模式下不再实时抓取，只用内置 `data/12306_stations.json`（实测 3,404 个车站，个人/商业两模式一致）。

**实测结果**

| 模式 | 首页 | `/api/transfers` |
| :-- | :-- | :-- |
| `personal` | 200 | 200，返回真实中转方案 |
| `commercial` | 200 | **503 `compliance_blocked`**，附替代方案提示 |

**未完成（商业上线前必须做）**

1. 用 Trip.com / Booking / Agoda / Klook / Travelpayouts 联盟接口替换 7 个抓取源，替换后 `503` 才会变成正常数据。
2. 英文界面与 i18n（当前 `lang="zh-CN"`）。
3. 付费墙、四个合规页面、Creem 收款。

