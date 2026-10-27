import datetime
import json
import os
import subprocess
import unittest
from unittest.mock import patch

# Enforce offline/zero-paid-API mode during all tests
os.environ["SKIP_LLM"] = "1"

import planner_agent
import provider_flights
import provider_12306
import provider_map
import server


class TestProductionReadiness(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["SKIP_LLM"] = "1"

    def test_chinese_date_parsing_avoids_time_and_star_ranges(self) -> None:
        today = datetime.date.today()
        tomorrow = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        day_after = (today + datetime.timedelta(days=2)).strftime("%Y-%m-%d")

        # "9-12点" or "4-5星" or "200-300元" must NOT override "明天"/"后天"
        self.assertEqual(
            planner_agent.parse_chinese_date("明天北京去上海，9-12点出发"),
            (tomorrow, True),
        )
        self.assertEqual(
            planner_agent.parse_chinese_date("后天去杭州住4-5星酒店，预算3-5百"),
            (day_after, True),
        )

        # Explicit MM-DD with hyphen and no range suffix works and rolls over if past
        explicit, is_exp = planner_agent.parse_chinese_date("12月25日从北京去三亚")
        self.assertTrue(is_exp)
        dt_parsed = datetime.date.fromisoformat(explicit)
        self.assertEqual(dt_parsed.month, 12)
        self.assertEqual(dt_parsed.day, 25)
        self.assertGreaterEqual(dt_parsed, today)

        # Cross-year rollover check: a date earlier than today rolls to next year
        past_day = today - datetime.timedelta(days=15)
        rolled, is_exp2 = planner_agent.parse_chinese_date(f"{past_day.month}月{past_day.day}日去成都")
        self.assertTrue(is_exp2)
        rolled_dt = datetime.date.fromisoformat(rolled)
        self.assertEqual(rolled_dt.year, today.year + 1)

    def test_parse_user_intent_route_and_flags(self) -> None:
        today = datetime.date.today()
        tomorrow = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        intent = planner_agent.parse_user_intent(
            "明天北京到上海高铁，9-12点出发，顺便推荐外滩4-5星酒店和门票，规划3天行程"
        )
        self.assertEqual(intent["from_city"], "北京")
        self.assertEqual(intent["to_city"], "上海")
        self.assertEqual(intent["date"], tomorrow)
        self.assertTrue(intent["only_train"])
        self.assertTrue(intent["asking_hotels"])
        self.assertTrue(intent["asking_attractions"])
        self.assertTrue(intent["needs_itinerary"])
        self.assertEqual(intent["days"], 3)

    def test_export_itinerary_to_ics_multi_day_and_escaping(self) -> None:
        itinerary = {
            "title": "北京 -> 上海 2日深度游; 含外滩,迪士尼",
            "days": [
                {
                    "day": 1,
                    "date": "2026-11-01",
                    "theme": "抵达与外滩夜景",
                    "items": [
                        {
                            "time": "09:00",
                            "title": "G1 高铁出发; 商务座",
                            "location": "北京南站, 2F候车室",
                            "detail": "提前25分钟检票\n带好身份证",
                        }
                    ],
                },
                {
                    "day": 2,
                    "date": "Day 2",  # Non-ISO date from LLM should auto-resolve to 2026-11-02
                    "theme": "迪士尼畅玩",
                    "items": [
                        {
                            "time": "08:30",
                            "title": "上海迪士尼乐园",
                            "location": "浦东新区川沙新镇",
                            "detail": "早享卡入园",
                        }
                    ],
                },
            ],
        }
        ics = planner_agent.export_itinerary_to_ics(itinerary)
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 2)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20261101T090000", ics)
        self.assertIn("DTSTART;TZID=Asia/Shanghai:20261102T083000", ics)
        # Check RFC 5545 escaping of semicolons/commas/newlines
        self.assertIn(r"LOCATION:北京南站\, 2F候车室", ics)
        self.assertIn(r"提前25分钟检票\n带好身份证", ics)

    def test_fallback_itinerary_and_attraction_fields(self) -> None:
        intent = {
            "from_city": "北京",
            "to_city": "上海",
            "hotel_city": "上海",
            "attraction_city": "上海",
            "date": "2026-11-10",
            "days": 3,
            "needs_itinerary": True,
            "asking_hotels": True,
            "asking_attractions": True,
        }
        trains = [
            {
                "train_code": "G1",
                "from_station": "北京南",
                "to_station": "上海虹桥",
                "dep_time": "07:00",
                "arr_time": "11:29",
                "duration_text": "4小时29分",
                "lowest_price": 662,
                "can_buy": True,
                "seats": [{"name": "二等座", "price": 662, "available": "有"}],
            }
        ]
        attractions = [
            {
                "name": "外滩",
                "score": 4.8,
                "price": 0,
                "is_free": True,
                "rank_badge": "上海必打卡 No.1",
                "review_count": "12.5万+条点评",
                "district_name": "黄浦区",
                "open_time": "全天开放",
                "ticket_name": "免费免预约",
                "features": ["万国建筑博览群"],
            },
            {
                "name": "上海迪士尼度假区",
                "score": 4.7,
                "price": 475,
                "is_free": False,
                "rank_badge": "主题乐园榜 No.1",
                "review_count": "28万+条点评",
                "district_name": "浦东新区",
                "open_time": "08:30-21:30",
                "ticket_name": "标准一日票",
                "features": ["烟花秀"],
            },
        ]
        res = planner_agent.generate_chat_response(
            user_message="明天北京去上海玩3天",
            intent=intent,
            trains=trains,
            flights=[],
            hotels=[],
            stopovers=[],
            transfers=[],
            comparison=None,
            history=[],
            attractions=attractions,
        )
        self.assertTrue(res["ok"])
        itinerary = res["itinerary"]
        self.assertIsNotNone(itinerary)
        self.assertEqual(len(itinerary["days"]), 3)
        self.assertEqual(itinerary["days"][0]["date"], "2026-11-10")
        self.assertEqual(itinerary["days"][1]["date"], "2026-11-11")
        self.assertEqual(itinerary["days"][2]["date"], "2026-11-12")

        # Verify exported ICS has events for all 3 days
        ics = planner_agent.export_itinerary_to_ics(itinerary)
        self.assertIn("20261110T", ics)
        self.assertIn("20261111T", ics)
        self.assertIn("20261112T", ics)

    def test_node_vm_sandbox_blocks_rce(self) -> None:
        # Ensure that if a malicious payload is injected into window.__NUXT__, vm.runInNewContext cannot access process/require
        malicious_html = (
            "<script>window.__NUXT__=(function(){ "
            "if (typeof process !== 'undefined' || typeof require !== 'undefined') { throw new Error('RCE_LEAK'); } "
            "return {state:{book1:{flightLists:[{flightNo:'MU5101',airCompanyName:'东方航空',depTime:'2026-11-10 08:00',arrTime:'2026-11-10 10:15',orgAirportShortName:'首都',dstAirportShortName:'虹桥',flightTime:'2h15m',cabins:[{PublishPrice:580}]}]}}}; "
            "})();</script>"
        )
        client = provider_flights.FlightScraperClient()

        class _DummyResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self) -> bytes:
                return malicious_html.encode("utf-8")

        with patch("urllib.request.urlopen", return_value=_DummyResp()):
            res = client.query_flights("北京", "上海", "2026-11-10")
            self.assertTrue(res["ok"])
            self.assertEqual(len(res["flights"]), 1)
            self.assertEqual(res["flights"][0]["flight_no"], "MU5101")

    def test_server_endpoints_and_validation(self) -> None:
        async def _run_checks() -> None:
            app = server.create_app()
            for startup_cb in app.on_startup:
                await startup_cb(app)
            try:
                # 1. Health endpoint
                req_health = unittest.mock.MagicMock()
                resp_health = await server.handle_health(req_health)
                self.assertEqual(resp_health.status, 200)
                health_data = json.loads(resp_health.text)
                self.assertTrue(health_data["ok"])
                self.assertGreater(health_data["stations_loaded"], 1000)

                # 2. Input length validation on /api/chat/tools
                req_long = unittest.mock.MagicMock()
                async def _long_json():
                    return {"message": "A" * 600}
                req_long.json = _long_json
                resp_long = await server.handle_chat_tools(req_long)
                self.assertEqual(resp_long.status, 400)

                # 3. ICS export endpoint
                req_ics = unittest.mock.MagicMock()
                async def _ics_json():
                    return {
                        "itinerary": {
                            "title": "测试行程",
                            "days": [
                                {
                                    "day": 1,
                                    "date": "2026-11-10",
                                    "theme": "第一天",
                                    "items": [{"time": "10:00", "title": "故宫", "location": "北京", "detail": "游览"}],
                                }
                            ],
                        }
                    }
                req_ics.json = _ics_json
                resp_ics = await server.handle_export_ics(req_ics)
                self.assertEqual(resp_ics.status, 200)
                self.assertIn("BEGIN:VEVENT", resp_ics.text)
            finally:
                for cleanup_cb in app.on_cleanup:
                    await cleanup_cb(app)

        import asyncio
        asyncio.run(_run_checks())


if __name__ == "__main__":
    unittest.main()
