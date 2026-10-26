<div align="center">

<img src="static/logo.png" alt="远行客 Logo" width="78" style="border-radius: 18px;" />

# 远行客 (Personal Travel Agent)

**纯对话式出行助手：支持全国高铁余票、全球与国内机票、智能中转（空铁联运/同站换乘）、全球酒店房价、实时汇率时差换算与定制行程。**

[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/Dependencies-aiohttp-10B981.svg)](#快速开始)

[**English**](README.md) | [**简体中文**](README_zh.md)

</div>

---

## ✨ 核心功能

- **🚄 火车与高铁实时查票** — 覆盖全国 3,300+ 车站（含县城车站），实时查询余票、各席别票价及沿途经停站时刻表。
- **✈️ 国内外机票与空铁比价** — 实时查询国内航班时刻、舱位折扣及国际航线含税底价，自动对比高铁与飞机的全程耗时与总花费。
- **🔀 智能中转方案（空铁联运 / 同站换乘 / 联程转机）** — 无直达车次或跨国出行时，自动组合「高铁 + 国际/国内航班」、「国内航班 + 国际航班」及「同站/同城高铁换乘」，直观展示中转枢纽、换乘预留时间与两程合计总价。
- **🏨 全球及全国市县酒店** — 支持国内市县与海外城市（如河内、东京、曼谷等），精准识别机场、火车站、商圈、景点周边并返回实时房价。
- **💱 货币汇率与时差换算** — 出境目的地自动匹配当地时间与实时汇率，支持一键切换多币种计价、手动自定义汇率及双时区时刻换算。
- **🗓️ 行程规划与日历导出** — 基于真实车次、航班与酒店价格生成按天行程单，支持一键导出 `.ics` 加入手机日历。

## 📸 界面预览

| 对话首屏与汇率/时区工具栏 | 跨国航班与智能中转（空铁联运 + 联程转机） |
| :---: | :---: |
| ![首屏预览](preview_home.png) | ![智能中转与车票预览](preview_tickets.png) |

| 高铁 vs 飞机对比 + 酒店 + 行程安排 | 海外/国内地标周边酒店查询（含外币自动换算） |
| :---: | :---: |
| ![航班与酒店卡片](chat_flight_hotel_verified.png) | ![河内机场酒店实时查询](chat_hanoi_deepseek_verified.png) |

## 🚀 快速开始

仅需 **Python 3.9+** 即可运行：

```bash
# 1. 克隆仓库并安装依赖
git clone https://github.com/nans94759-sketch/personal-travel-agent.git
cd personal-travel-agent
pip install -r requirements.txt

# 2. 配置大模型密钥
cp .env.example .env
# 编辑 .env 填入 LLM_API_KEY=你的密钥

# 3. 启动服务
python3 server.py
```

浏览器打开 **[http://127.0.0.1:8868](http://127.0.0.1:8868)** 即可直接提问：
- `"周天杭州去河内有什么航班？"` *（国际航线含税底价 + 自动生成上海空铁联运、广深转机方案）*
- `"明天福州去玉山的高铁有哪些？"` *（全国县城高铁站直查）*
- `"下周一北京去上海，对比高铁和飞机，顺便看外滩附近酒店"` *（全程耗时花费对比 + 商圈酒店）*
- `"河内，机场旁边，酒店价格，下周一"` *（海外机场周边酒店 + 越南盾汇率与时差自动换算）*

## 📄 开源协议

基于 [MIT License](LICENSE) 开源。
