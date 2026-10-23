# 🚄✈️🏨 个人行程安排智能体 (Travel & Ticket Conversational Agent)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![Zero Mock Data](https://img.shields.io/badge/Data-100%25%20Live%20Real--Time-10B981.svg)](#核心特性)

一个**纯对话式、100% 真实实时数据驱动（Zero Mock）**的个人出行与住宿智能体。无需申请昂贵的商业 OTA 企业接口、无需挂载笨重的无头浏览器（Playwright/Selenium），仅凭 **Python 标准库 + 系统 `curl`** 即可实时调取 **12306 高铁余票与票价、国内民航低价机票、全球/全国市县实时酒店房价（含商圈/机场周边过滤）**，并在极简对话流中自动内嵌高颜值交互卡片。

欢迎大家一起 Issue / PR 共建优化！

---

## 📸 界面预览

| 空铁联运 + 目的地酒店 + 行程规划 | 国际/国内地标周边酒店精准查询（如：河内机场旁） |
| :---: | :---: |
| ![航班与酒店实时卡片](chat_flight_hotel_verified.png) | ![河内内排机场酒店实时查询](chat_hanoi_deepseek_verified.png) |

---

## ✨ 核心特性

1. **🤖 AI 规范化工具自主路由 (`ai_route_tools`)**
   - 摒弃死板的纯正则匹配，向大模型（默认支持 **DeepSeek V4.1 / V4**）注入结构化工具调用规范，由 AI 自主理解用户自然语言意图并决定组合调用哪些实时接口：
     - `query_trains`：查询两地 12306 实时火车/高铁余票与各席别票价
     - `query_stopovers`：查询指定车次（如 `G1632`）全程经停站与到发时刻表
     - `query_transfers`：无直达或余票紧张时，自动计算全国枢纽 1 次中转联程方案
     - `query_flights`：查询两地实时民航低价航班及空铁门到门耗时/成本比价
     - `query_hotels`：查询全球城市及全国市/县实时酒店房价（支持如 `河内，机场旁边`、`上海外滩附近`、`玉山` 等地标/县城级粒度）
2. **🚄 12306 全国 3,300+ 车站实时余票与单次请求票价解码 (`provider_12306.py`)**
   - 启动时自动同步官方 `station_name.js`（覆盖全国 3,300+ 车站，含昆山、义乌、玉山、婺源等县级站）。
   - 直接从 `leftTicket/queryG` 响应的 `field[39]`（`infoAll_list` 10 字节定长块）解码二等座/一等座/商务座/硬卧/软卧实时票价，无需二次请求 `queryTicketPrice` 接口，杜绝被 12306 限流。
3. **✈️ 国内民航实时低价航班直连 (`provider_flights.py`)**
   - 解析同程旅行机票频道 (`ly.com/flights/itinerary/oneway/...`) 的 Nuxt.js SSR 预取状态树 (`window.__NUXT__.state.book1.flightLists`)，毫秒级提取航司、航班号、机型、起降航站楼、准点率及实时经济舱/公务舱最低裸票价。
4. **🏨 免 Key 免滑块全球/全国市县酒店实时房价 (`provider_hotels.py`)**
   - **三级地理与地标解析**：结合携程 H5 开放检索 (`m.ctrip.com/restapi/h5api/globalsearch/search`) 自动区分城市 ID (`cityId`)、行政区/商圈 (`zone`)、机场/火车站 (`filterId=10|<id>`) 与景点地标 (`filterId=13|<id>`)。
   - **Next.js SSR 流式数据解包**：直连 Trip.com 国际站 (`sg.trip.com/hotels/list?...&locale=zh-SG&curr=CNY`) 提取 `initData.firstPageList.hotelList`，返回人民币实时含税/未税房价、星级、评分、真实住客点评摘要及地标直线距离。
5. **💬 极简纯对话 UI + 6 大固定内嵌卡片 (`static/index.html`)**
   - 无繁琐表单，单栏对话流内按需动态渲染：**空铁门到门比价卡**、**12306 实时车次表**、**民航低价航班表**、**携程/Trip.com 实时酒店网格**、**列车经停时刻表**、**分日定制行程单**。

---

## 📁 项目结构

```text
.
├── server.py              # 零依赖轻量 HTTP 服务端 (ThreadingHTTPServer)
├── planner_agent.py       # AI 工具规范路由器 (ai_route_tools) + 多数据源并发编排 + DeepSeek 总结
├── provider_12306.py      # 12306 实时余票、嵌入式票价解码、经停时刻表、中转联程引擎
├── provider_flights.py    # 民航机场三字码解析 + 同程 SSR 实时航班抓取引擎
├── provider_hotels.py     # 携程 H5 地理/机场/商圈检索 + Trip.com SSR 实时酒店房价引擎
├── static/
│   └── index.html         # 极简对话式前端界面与 6 大内嵌可视化组件
├── .env.example           # 环境变量配置模板
└── LICENSE                # MIT 开源协议
```

---

## 🚀 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/nans94759-sketch/personal-travel-agent.git
cd personal-travel-agent
```

*(注：本项目完全基于 Python 3.9+ 标准库与系统内置 `curl` 构建，**无需安装任何第三方 pip 依赖包**！)*

### 2. 配置大模型 API Key

复制 `.env.example` 为 `.env`，并填入你的 OpenAI 兼容接口密钥（推荐使用阿里云百炼 DashScope 或 DeepSeek 官方 API）：

```bash
cp .env.example .env
```

编辑 `.env`：

```ini
LLM_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
LLM_API_KEY=sk-你的API密钥
LLM_DEFAULT_MODEL=deepseek-v4.1-flash
PORT=8868
```

### 3. 启动服务

```bash
python3 server.py
```

启动后在浏览器打开：**[http://127.0.0.1:8868](http://127.0.0.1:8868)** 即可开始对话！

---

## 💡 对话测试示例

你可以在输入框中直接尝试以下自然语言提问：

- **查高铁（含县级站）**：`明天福州去玉山的高铁有哪些？`
- **查空铁比价 + 住宿**：`下周一北京去上海，帮我对比坐高铁还是坐飞机划算，顺便看看外滩附近的酒店`
- **查海外/机场周边酒店**：`河内，机场旁边，酒店价格，下周一`
- **查列车经停站**：`G1632 次列车沿途停靠哪些站？`
- **定制完整行程单**：`本周六从上海去杭州玩3天，帮我查好高铁票、西湖附近酒店并安排一份三天行程`

---

## 🤝 参与共建 (Contributing)

非常欢迎大家提交 Pull Request 或 Issue 一起优化这个智能体！目前几个非常适合扩展的方向：

1. **国际航线机票接入**：目前 `provider_flights.py` 主要覆盖国内民航机场，欢迎补充国际/港澳台航线实时低价接口。
2. **流式输出 (SSE Streaming)**：将 `server.py` 与前端升级为工具卡片先行渲染 + AI 文本总结逐字流式输出。
3. **更多地面交通/门票/美食工具**：如接入高德/百度地图市内打车耗时估算、景区门票预约状态查询等，只需在 `planner_agent.py` 的 `ai_route_tools` 规范中注册新工具即可。

---

## 📄 开源协议

本项目基于 [MIT License](LICENSE) 开源。所有票务与酒店实时数据均来自公开网页直连查询，仅供个人学习与技术研究使用。
