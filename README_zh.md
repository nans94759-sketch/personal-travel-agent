<div align="center">

# 🧭 Personal Travel Agent (个人行程安排智能体)

**极简纯对话式 AI 出行智能体：实时查询 12306 高铁余票、民航低价机票、全球酒店房价与定制行程。**  
*100% 真实实时数据 · 零第三方 pip 依赖 · 对话流内嵌交互卡片*

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero-10B981.svg)](#快速开始)
[![Model](https://img.shields.io/badge/LLM-DeepSeek%20%2F%20OpenAI-4F46E5.svg)](#快速开始)

[**English**](README.md) | [**简体中文**](README_zh.md)

</div>

---

## ✨ 核心特性

- **🤖 AI 工具规范路由 (`ai_route_tools`)** — 向大模型注入结构化工具规范，由 AI 自主解析自然语言并决定组合调用哪些实时接口。
- **🚄 12306 实时余票与票价 (`provider_12306.py`)** — 覆盖全国 3,300+ 车站（含县级站），单次请求直接从 `infoAll_list` 解码各席别票价，并支持枢纽中转联程计算。
- **✈️ 民航实时低价航班 (`provider_flights.py`)** — 基于 SSR 状态树提取实时最低裸票价、机型、准点率与航站楼，支持空铁门到门耗时/成本比价。
- **🏨 全球及全国市县实时酒店 (`provider_hotels.py`)** — 支持国内市/县与海外城市（如河内、东京、曼谷），精准识别机场、火车站、商圈与景点地标（`zone` / `filterId`）并返回实时 CNY 房价。
- **💬 极简纯对话 UI (`static/index.html`)** — 告别繁琐表单，单栏对话流内按需嵌入车次、航班、酒店、经停表与定制行程单卡片。

## 📸 界面预览

| 空铁门到门比价 + 酒店 + 行程规划 | 国际/国内地标周边酒店查询（如：河内机场旁） |
| :---: | :---: |
| ![航班与酒店卡片](chat_flight_hotel_verified.png) | ![河内机场酒店实时查询](chat_hanoi_deepseek_verified.png) |

## 🛠️ 内置工具矩阵

| 工具名 | 数据源 | 功能说明 |
| :--- | :--- | :--- |
| `query_trains` | 12306 官方接口 | 实时车次余票查询 + 单次请求多席别票价内联解码 |
| `query_stopovers` | 12306 官方接口 | 指定车次（如 `G1632`）全程经停站与到发时刻表 |
| `query_transfers` | 12306 官方接口 | 无直达或余票紧张时，自动计算全国枢纽 1 次中转方案 |
| `query_flights` | 同程机票 SSR | 国内实时航班时刻、最低裸票价与舱位折扣提取 |
| `query_hotels` | 携程 H5 + Trip.com SSR | 全球/全国市县实时酒店房价（支持机场、商圈、地标过滤） |

## 🚀 快速开始

无需安装任何第三方 Python 包或无头浏览器，仅需 **Python 3.9+** 与系统内置 `curl`。

```bash
# 1. 克隆仓库
git clone https://github.com/nans94759-sketch/personal-travel-agent.git
cd personal-travel-agent

# 2. 配置大模型 API Key（支持阿里云百炼 DashScope / DeepSeek / OpenAI 兼容接口）
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY=你的密钥

# 3. 启动服务
python3 server.py
```

浏览器打开 **[http://127.0.0.1:8868](http://127.0.0.1:8868)** 即可直接提问：
- `"明天福州去玉山的高铁有哪些？"` *(全国县级高铁站直查)*
- `"下周一北京去上海，对比高铁和飞机，顺便看外滩附近酒店"` *(空铁比价 + 商圈酒店)*
- `"河内，机场旁边，酒店价格，下周一"` *(海外城市 + 机场地标周边酒店)*

## 🤝 参与共建

欢迎提交 Issue 或 Pull Request！推荐扩展方向：
- [ ] **国际航线机票**：扩展 `provider_flights.py` 支持国际/港澳台航线实时比价。
- [ ] **SSE 流式响应**：卡片优先渲染后，AI 文本总结采用流式逐字输出。
- [ ] **更多出行工具**：在 `ai_route_tools` 中扩展目的地天气、市内打车耗时、景区门票预约等工具。

## 📄 开源协议

基于 [MIT License](LICENSE) 开源。
