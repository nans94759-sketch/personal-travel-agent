"""
User System, Personalized Recommendation Engine & Admin Backend
---------------------------------------------------------------
Supports dual persistence:
1. Supabase Cloud (PostgreSQL via PostgREST API)
2. Local SQLite (`data/travel_app.db` or `/tmp/travel_app.db` on serverless)
"""

import datetime
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Tuple

from aiohttp import web

logger = logging.getLogger("travel_agent.backend")

# Default Supabase Cloud configuration (provisioned via Supabase CLI)
DEFAULT_SUPABASE_URL = "https://zxbajfsblpgtyljoxggv.supabase.co"
DEFAULT_SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp4YmFqZnNibHBndHlsam94Z2d2Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTMzNDAxODcsImV4cCI6MjEwODkxNjE4N30."
    "DM2VI7TqwWgpnrMgdxKw4unv2Xs7LiW6hDr1UQNBx_8"
)
DEFAULT_SUPABASE_SERVICE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp4YmFqZnNibHBndHlsam94Z2d2Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MzM0MDE4NywiZXhwIjoyMTA4OTE2MTg3fQ."
    "muGOPftWyTCgrjIrRK_8JTWbdBJNUnlSvBvb2pgfA3o"
)

PASSWORD_SALT = os.environ.get("AUTH_PASSWORD_SALT", "travel_agent_salt_2026")


def hash_password(raw_password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        raw_password.encode("utf-8"),
        PASSWORD_SALT.encode("utf-8"),
        60000,
    ).hex()


def verify_password(raw_password: str, stored_hash: str) -> bool:
    computed = hash_password(raw_password)
    return hmac.compare_digest(computed, stored_hash)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class UserAndAdminStore:
    """Thread-safe SQLite + Supabase Cloud synchronized store."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self._lock = threading.RLock()
        self.supabase_url = (
            os.environ.get("SUPABASE_URL") or DEFAULT_SUPABASE_URL
        ).rstrip("/")
        self.supabase_anon_key = (
            os.environ.get("SUPABASE_ANON_KEY") or DEFAULT_SUPABASE_ANON_KEY
        )
        self.supabase_service_key = (
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or DEFAULT_SUPABASE_SERVICE_KEY
        )

        # Choose writable SQLite location (local `data/` or `/tmp` on Vercel serverless)
        data_dir = os.path.join(base_dir, "data")
        try:
            os.makedirs(data_dir, exist_ok=True)
            test_path = os.path.join(data_dir, ".write_test")
            with open(test_path, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(test_path)
            self.db_path = os.path.join(data_dir, "travel_app.db")
        except Exception:
            self.db_path = "/tmp/travel_app.db"

        self._init_sqlite()
        self._seed_defaults()
        # Async background sync with Supabase Cloud on startup
        threading.Thread(target=self._initial_supabase_sync, daemon=True).start()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS travel_users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    nickname TEXT NOT NULL DEFAULT '旅行家',
                    avatar_url TEXT DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'user',
                    status TEXT NOT NULL DEFAULT 'active',
                    home_city TEXT NOT NULL DEFAULT '杭州',
                    preferred_currency TEXT NOT NULL DEFAULT 'CNY',
                    preferred_transport TEXT NOT NULL DEFAULT 'all',
                    preferred_seat TEXT NOT NULL DEFAULT '二等座 / 经济舱',
                    hotel_budget TEXT NOT NULL DEFAULT 'comfort',
                    travel_tags TEXT NOT NULL DEFAULT '["高铁出行","高分酒店","顺路游览"]',
                    frequent_destinations TEXT NOT NULL DEFAULT '["上海","北京","河内"]',
                    search_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    ip_address TEXT DEFAULT '',
                    user_agent TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_search_logs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    user_email TEXT DEFAULT 'anonymous',
                    query_text TEXT NOT NULL,
                    from_city TEXT DEFAULT '',
                    to_city TEXT DEFAULT '',
                    travel_date TEXT DEFAULT '',
                    intent_type TEXT DEFAULT 'general',
                    mode TEXT DEFAULT 'commercial',
                    trains_count INTEGER DEFAULT 0,
                    flights_count INTEGER DEFAULT 0,
                    hotels_count INTEGER DEFAULT 0,
                    attractions_count INTEGER DEFAULT 0,
                    latency_ms INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_saved_trips (
                    id TEXT PRIMARY KEY,
                    user_id TEXT,
                    user_email TEXT DEFAULT '',
                    title TEXT NOT NULL,
                    from_city TEXT DEFAULT '',
                    to_city TEXT NOT NULL,
                    travel_date TEXT DEFAULT '',
                    days INTEGER DEFAULT 3,
                    summary TEXT DEFAULT '',
                    itinerary_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_prompt_templates (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    icon TEXT NOT NULL DEFAULT 'sparkles',
                    category TEXT NOT NULL DEFAULT 'route',
                    target_transport TEXT NOT NULL DEFAULT 'all',
                    target_tags TEXT NOT NULL DEFAULT '[]',
                    priority INTEGER NOT NULL DEFAULT 100,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS travel_system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            conn.commit()

    def _seed_defaults(self) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) AS cnt FROM travel_users")
            if cur.fetchone()["cnt"] == 0:
                default_users = [
                    (
                        "00000000-0000-4000-8000-000000000001",
                        "admin@travel.ai",
                        hash_password("admin123456"),
                        "总控管理员",
                        "",
                        "admin",
                        "active",
                        "杭州",
                        "CNY",
                        "all",
                        "一等座 / 公务舱",
                        "comfort",
                        json.dumps(["全域监控", "空铁联运", "跨境差旅", "高分酒店"], ensure_ascii=False),
                        json.dumps(["上海", "河内", "东京", "新加坡"], ensure_ascii=False),
                        18,
                        now,
                        now,
                    ),
                    (
                        "00000000-0000-4000-8000-000000000002",
                        "vip@travel.ai",
                        hash_password("vip123456"),
                        "林远行 · 跨境差旅",
                        "",
                        "vip",
                        "active",
                        "上海",
                        "USD",
                        "flight",
                        "公务舱 / 一等座",
                        "luxury",
                        json.dumps(["东南亚出差", "机场旁五星酒店", "直飞优先", "跨时区换算"], ensure_ascii=False),
                        json.dumps(["河内", "曼谷", "东京", "新加坡"], ensure_ascii=False),
                        12,
                        now,
                        now,
                    ),
                    (
                        "00000000-0000-4000-8000-000000000003",
                        "demo@travel.ai",
                        hash_password("demo123456"),
                        "苏小游 · 高铁特种兵",
                        "",
                        "user",
                        "active",
                        "北京",
                        "CNY",
                        "train",
                        "二等座",
                        "economy",
                        json.dumps(["高铁控", "周末特种兵", "必去景点顺路游", "高性价比住宿"], ensure_ascii=False),
                        json.dumps(["上海", "杭州", "南京", "成都"], ensure_ascii=False),
                        9,
                        now,
                        now,
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO travel_users (
                        id, email, password_hash, nickname, avatar_url, role, status,
                        home_city, preferred_currency, preferred_transport, preferred_seat,
                        hotel_budget, travel_tags, frequent_destinations, search_count,
                        created_at, last_login_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    default_users,
                )

            cur_tpl = conn.execute("SELECT COUNT(*) AS cnt FROM travel_prompt_templates")
            if cur_tpl.fetchone()["cnt"] == 0:
                templates = [
                    (
                        "10000000-0000-4000-8000-000000000001",
                        "{day_label}{home_city}去{dest_city}上午高铁余票",
                        "{day_label}上午从{home_city}到{dest_city}有哪些高铁？优先看二等座余票",
                        "train-front",
                        "train",
                        "train",
                        json.dumps(["高铁出行", "高铁控", "周末特种兵"], ensure_ascii=False),
                        120,
                        1,
                        42,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000002",
                        "{day_label}{home_city}去{intl_city}（含空铁中转）",
                        "{day_label}{home_city}去{intl_city}有什么航班或空铁中转方案？",
                        "git-branch",
                        "transfer",
                        "all",
                        json.dumps(["空铁联运", "东南亚出差", "跨境差旅"], ensure_ascii=False),
                        115,
                        1,
                        38,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000003",
                        "明天{home_city}到{dest_city} 高铁 vs 机票比价",
                        "明天{home_city}到{dest_city}对比下高铁和机票哪个更划算、更快？",
                        "scale",
                        "compare",
                        "all",
                        json.dumps(["性价比", "差旅"], ensure_ascii=False),
                        110,
                        1,
                        31,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000004",
                        "{day_label}{intl_city}{hotel_style}（实时汇率）",
                        "{day_label}去{intl_city}，推荐{hotel_style}并换算当地货币价格",
                        "building-2",
                        "hotel",
                        "all",
                        json.dumps(["高分酒店", "机场旁五星酒店", "高性价比住宿"], ensure_ascii=False),
                        105,
                        1,
                        29,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000005",
                        "{dest_city}酒店+必去景点智能顺路画路线",
                        "{day_label}去{dest_city}，推荐高分酒店和必去景点门票，并规划顺路游览路线",
                        "compass",
                        "itinerary",
                        "all",
                        json.dumps(["顺路游览", "必去景点顺路游"], ensure_ascii=False),
                        100,
                        1,
                        35,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000006",
                        "{home_city}出发周末2日特种兵顺路游",
                        "本周六从{home_city}出发去{dest_city}玩2天，帮我排一份高铁往返+景点顺路行程",
                        "map-pin",
                        "itinerary",
                        "train",
                        json.dumps(["周末特种兵", "高铁控"], ensure_ascii=False),
                        95,
                        1,
                        24,
                        now,
                    ),
                    (
                        "10000000-0000-4000-8000-000000000007",
                        "{intl_city}3日入境/出海行程+必买交通卡",
                        "下周从{home_city}去{intl_city}玩3天，查询航班、必买交通卡与顺路打卡路线",
                        "globe",
                        "itinerary",
                        "flight",
                        json.dumps(["跨境差旅", "直飞优先"], ensure_ascii=False),
                        90,
                        1,
                        19,
                        now,
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO travel_prompt_templates (
                        id, title, prompt_text, icon, category, target_transport,
                        target_tags, priority, is_active, click_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    templates,
                )

            cur_set = conn.execute("SELECT COUNT(*) AS cnt FROM travel_system_settings")
            if cur_set.fetchone()["cnt"] == 0:
                settings = [
                    ("custom_domain", "chinatripcopilot.com", "海外主品牌域名 (Cloudflare + Vercel)", now),
                    ("concierge_price_usd", "29", "海外付费定制行程包价格 (USD $)", now),
                    ("concierge_checkout_url", "", "LemonSqueezy / Creem / Stripe $29 收款链接 (留空则走内置意向单留资)", now),
                    ("marquee_auto_scroll", "true", "底部/顶部推荐提示词是否开启自动平滑滚动", now),
                    ("marquee_speed_sec", "32", "推荐提示词滚动一轮秒数（越小越快）", now),
                    ("recommendation_count", "8", "单次向前端下发的智能推荐提示词数量", now),
                    ("default_model", "deepseek-v4.1-flash", "默认对话与意图路由模型", now),
                    ("trip_alliance_id", os.environ.get("TRIP_COM_ALLIANCEID", ""), "Trip.com 联盟 AllianceID (填入后自动激活全站分佣链接)", now),
                    ("trip_sid", os.environ.get("TRIP_COM_SID", ""), "Trip.com 联盟 SID (填入后自动激活全站分佣链接)", now),
                    ("supabase_project_ref", "zxbajfsblpgtyljoxggv", "绑定的 Supabase 云项目 Ref", now),
                ]
                conn.executemany(
                    """
                    INSERT INTO travel_system_settings (key, value, description, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    settings,
                )
            else:
                for extra_k, extra_v, extra_desc in [
                    ("custom_domain", "chinatripcopilot.com", "海外主品牌域名 (Cloudflare + Vercel)"),
                    ("concierge_price_usd", "29", "海外付费定制行程包价格 (USD $)"),
                    ("concierge_checkout_url", "", "LemonSqueezy / Creem / Stripe $29 收款链接 (留空则走内置意向单留资)"),
                ]:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO travel_system_settings (key, value, description, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (extra_k, extra_v, extra_desc, now),
                    )

            cur_logs = conn.execute("SELECT COUNT(*) AS cnt FROM travel_search_logs")
            if cur_logs.fetchone()["cnt"] == 0:
                sample_logs = [
                    (
                        str(uuid.uuid4()),
                        "00000000-0000-4000-8000-000000000001",
                        "admin@travel.ai",
                        "周日杭州去河内有什么航班？（含空铁中转）",
                        "杭州",
                        "河内",
                        "2026-11-01",
                        "flight_transfer",
                        "commercial",
                        6,
                        8,
                        0,
                        0,
                        420,
                        now,
                    ),
                    (
                        str(uuid.uuid4()),
                        "00000000-0000-4000-8000-000000000002",
                        "vip@travel.ai",
                        "下周一去河内，推荐机场旁五星酒店并换算美元价格",
                        "上海",
                        "河内",
                        "2026-11-02",
                        "hotel",
                        "commercial",
                        0,
                        0,
                        6,
                        4,
                        310,
                        now,
                    ),
                    (
                        str(uuid.uuid4()),
                        "00000000-0000-4000-8000-000000000003",
                        "demo@travel.ai",
                        "明天北京到上海上午高铁二等座余票",
                        "北京",
                        "上海",
                        "2026-10-31",
                        "train",
                        "personal",
                        24,
                        10,
                        0,
                        0,
                        285,
                        now,
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO travel_search_logs (
                        id, user_id, user_email, query_text, from_city, to_city,
                        travel_date, intent_type, mode, trains_count, flights_count,
                        hotels_count, attractions_count, latency_ms, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    sample_logs,
                )

            cur_trips = conn.execute("SELECT COUNT(*) AS cnt FROM travel_saved_trips")
            if cur_trips.fetchone()["cnt"] == 0:
                sample_trips = [
                    (
                        str(uuid.uuid4()),
                        "00000000-0000-4000-8000-000000000001",
                        "admin@travel.ai",
                        "杭州 ➔ 河内 空铁联运 + 还剑湖3日顺路行程",
                        "杭州",
                        "河内",
                        "2026-11-01",
                        3,
                        "广州白云/南宁东中转飞河内内排，入住还剑湖法式精品酒店，串联三十六行街-巴亭广场-西湖TSP最优顺路线。",
                        json.dumps({"cities": ["杭州", "河内"], "saved_km": 6.4}, ensure_ascii=False),
                        "planned",
                        now,
                    ),
                    (
                        str(uuid.uuid4()),
                        "00000000-0000-4000-8000-000000000002",
                        "vip@travel.ai",
                        "上海 ➔ 东京 商务直飞 + 银座奢华酒店行程",
                        "上海",
                        "东京",
                        "2026-11-05",
                        4,
                        "虹桥/浦东直飞羽田，含 Suica 交通卡、银座五星酒店与浅草-涩谷顺路动线。",
                        json.dumps({"cities": ["上海", "东京"], "saved_km": 8.1}, ensure_ascii=False),
                        "booked",
                        now,
                    ),
                ]
                conn.executemany(
                    """
                    INSERT INTO travel_saved_trips (
                        id, user_id, user_email, title, from_city, to_city,
                        travel_date, days, summary, itinerary_json, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    sample_trips,
                )
            conn.commit()

    # ------------------------------------------------------------------
    # Supabase Cloud REST Sync Helpers
    # ------------------------------------------------------------------
    def _supabase_request(
        self,
        method: str,
        table_or_path: str,
        data: Any = None,
        query: str = "",
        prefer: str = "return=representation,resolution=merge-duplicates",
    ) -> Tuple[bool, Any]:
        if not self.supabase_url or not self.supabase_service_key:
            return False, "supabase_not_configured"
        url = f"{self.supabase_url}/rest/v1/{table_or_path}"
        if query:
            url = f"{url}?{query}"
        headers = {
            "apikey": self.supabase_service_key,
            "Authorization": f"Bearer {self.supabase_service_key}",
            "Content-Type": "application/json",
            "Prefer": prefer,
        }
        body_bytes = (
            json.dumps(data, ensure_ascii=False).encode("utf-8")
            if data is not None
            else None
        )
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read().decode("utf-8")
                return True, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return False, f"HTTP {e.code}: {err_body[:200]}"
        except Exception as e:
            return False, str(e)

    def check_supabase_status(self) -> Dict[str, Any]:
        ok, res = self._supabase_request("GET", "travel_users", query="select=id&limit=1")
        return {
            "connected": ok,
            "url": self.supabase_url,
            "project_ref": "zxbajfsblpgtyljoxggv",
            "detail": "Supabase Cloud PostgreSQL 实时同步正常" if ok else str(res),
        }

    def _initial_supabase_sync(self) -> None:
        """Push initial seeded rows to Supabase Cloud if tables exist."""
        try:
            ok, existing = self._supabase_request("GET", "travel_users", query="select=id&limit=1")
            if not ok:
                return
            if isinstance(existing, list) and len(existing) == 0:
                # Seed remote Supabase tables from local SQLite
                users = self.list_users(include_hash=True)
                for u in users:
                    self._supabase_request("POST", "travel_users", u)
                tpls = self.list_prompt_templates()
                for t in tpls:
                    t_copy = dict(t)
                    t_copy["is_active"] = bool(t_copy.get("is_active"))
                    self._supabase_request("POST", "travel_prompt_templates", t_copy)
        except Exception as exc:
            logger.debug("Initial Supabase sync skipped: %s", exc)

    def _async_sync_to_supabase(self, method: str, table: str, payload: Any, query: str = "") -> None:
        def _job() -> None:
            self._supabase_request(method, table, payload, query=query)

        threading.Thread(target=_job, daemon=True).start()

    # ------------------------------------------------------------------
    # User Serialization & Auth
    # ------------------------------------------------------------------
    def _row_to_user(self, row: sqlite3.Row, include_hash: bool = False) -> Dict[str, Any]:
        d = dict(row)
        for json_col, default_val in [
            ("travel_tags", []),
            ("frequent_destinations", []),
        ]:
            raw = d.get(json_col)
            if isinstance(raw, str):
                try:
                    d[json_col] = json.loads(raw)
                except Exception:
                    d[json_col] = default_val
            elif not isinstance(raw, list):
                d[json_col] = default_val
        if not include_hash:
            d.pop("password_hash", None)
        return d

    def register_user(
        self,
        email: str,
        password: str,
        nickname: str = "",
        home_city: str = "杭州",
        preferred_transport: str = "all",
        hotel_budget: str = "comfort",
        travel_tags: Optional[List[str]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        email_clean = email.strip().lower()
        if not email_clean or "@" not in email_clean:
            return None, "请输入有效的邮箱地址"
        if len(password) < 6:
            return None, "密码长度至少为 6 位"
        now = _now_iso()
        user_id = str(uuid.uuid4())
        nick = (nickname or email_clean.split("@")[0]).strip()
        tags = travel_tags if isinstance(travel_tags, list) and travel_tags else ["高铁出行", "高分酒店", "顺路游览"]
        dests = ["上海", "北京", "河内"] if home_city != "上海" else ["杭州", "北京", "东京"]

        with self._lock, self._connect() as conn:
            exists = conn.execute(
                "SELECT id FROM travel_users WHERE lower(email) = ?", (email_clean,)
            ).fetchone()
            if exists:
                return None, "该邮箱已注册，请直接登录"

            pwd_hash = hash_password(password)
            conn.execute(
                """
                INSERT INTO travel_users (
                    id, email, password_hash, nickname, avatar_url, role, status,
                    home_city, preferred_currency, preferred_transport, preferred_seat,
                    hotel_budget, travel_tags, frequent_destinations, search_count,
                    created_at, last_login_at
                ) VALUES (?, ?, ?, ?, '', 'user', 'active', ?, 'CNY', ?, '二等座 / 经济舱', ?, ?, ?, 0, ?, ?)
                """,
                (
                    user_id,
                    email_clean,
                    pwd_hash,
                    nick,
                    home_city or "杭州",
                    preferred_transport or "all",
                    hotel_budget or "comfort",
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(dests, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM travel_users WHERE id = ?", (user_id,)).fetchone()
            user_dict = self._row_to_user(row, include_hash=True)

        self._async_sync_to_supabase("POST", "travel_users", user_dict)
        user_dict.pop("password_hash", None)
        return user_dict, None

    def authenticate_user(
        self, email: str, password: str, ip: str = "", ua: str = ""
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
        email_clean = email.strip().lower()
        now = _now_iso()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM travel_users WHERE lower(email) = ?", (email_clean,)
            ).fetchone()
            if not row:
                return None, None, "账号不存在，请检查邮箱或先注册"
            if row["status"] != "active":
                return None, None, "该账号已被停用，请联系管理员"
            if not verify_password(password, row["password_hash"]):
                return None, None, "密码不正确，请重试"

            conn.execute(
                "UPDATE travel_users SET last_login_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            token = f"tk_{secrets.token_urlsafe(32)}"
            expires_at = (
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=14)
            ).isoformat()
            conn.execute(
                """
                INSERT INTO travel_sessions (token, user_id, expires_at, ip_address, user_agent, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token, row["id"], expires_at, ip[:64], ua[:200], now),
            )
            conn.commit()
            updated_row = conn.execute(
                "SELECT * FROM travel_users WHERE id = ?", (row["id"],)
            ).fetchone()
            user_dict = self._row_to_user(updated_row)

        self._async_sync_to_supabase(
            "POST",
            "travel_sessions",
            {
                "token": token,
                "user_id": user_dict["id"],
                "expires_at": expires_at,
                "ip_address": ip[:64],
                "user_agent": ua[:200],
                "created_at": now,
            },
        )
        return user_dict, token, None

    def get_user_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock, self._connect() as conn:
            sess = conn.execute(
                "SELECT * FROM travel_sessions WHERE token = ?", (token,)
            ).fetchone()
            if not sess:
                return None
            row = conn.execute(
                "SELECT * FROM travel_users WHERE id = ? AND status = 'active'",
                (sess["user_id"],),
            ).fetchone()
            return self._row_to_user(row) if row else None

    def logout_token(self, token: str) -> None:
        if not token:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM travel_sessions WHERE token = ?", (token,))
            conn.commit()
        self._async_sync_to_supabase("DELETE", "travel_sessions", None, query=f"token=eq.{token}")

    def update_user_profile(
        self, user_id: str, updates: Dict[str, Any], allow_admin_fields: bool = False
    ) -> Optional[Dict[str, Any]]:
        allowed_cols = {
            "nickname",
            "avatar_url",
            "home_city",
            "preferred_currency",
            "preferred_transport",
            "preferred_seat",
            "hotel_budget",
            "travel_tags",
            "frequent_destinations",
        }
        if allow_admin_fields:
            allowed_cols.update({"role", "status", "email"})

        set_parts: List[str] = []
        params: List[Any] = []
        sync_payload: Dict[str, Any] = {}

        for k, v in updates.items():
            if k not in allowed_cols or v is None:
                continue
            if k in ("travel_tags", "frequent_destinations"):
                if isinstance(v, str):
                    v_list = [x.strip() for x in v.split(",") if x.strip()]
                elif isinstance(v, list):
                    v_list = [str(x).strip() for x in v if str(x).strip()]
                else:
                    v_list = []
                set_parts.append(f"{k} = ?")
                params.append(json.dumps(v_list, ensure_ascii=False))
                sync_payload[k] = v_list
            else:
                val_str = str(v).strip()
                set_parts.append(f"{k} = ?")
                params.append(val_str)
                sync_payload[k] = val_str

        if "password" in updates and updates["password"]:
            pwd_raw = str(updates["password"]).strip()
            if len(pwd_raw) >= 6:
                pwd_h = hash_password(pwd_raw)
                set_parts.append("password_hash = ?")
                params.append(pwd_h)
                sync_payload["password_hash"] = pwd_h

        if not set_parts:
            with self._lock, self._connect() as conn:
                row = conn.execute("SELECT * FROM travel_users WHERE id = ?", (user_id,)).fetchone()
                return self._row_to_user(row) if row else None

        params.append(user_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE travel_users SET {', '.join(set_parts)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM travel_users WHERE id = ?", (user_id,)).fetchone()
            user_dict = self._row_to_user(row) if row else None

        if user_dict and sync_payload:
            self._async_sync_to_supabase("PATCH", "travel_users", sync_payload, query=f"id=eq.{user_id}")
        return user_dict

    # ------------------------------------------------------------------
    # Search Logging & Automatic User Profile Enrichment
    # ------------------------------------------------------------------
    def record_search_log(
        self,
        user: Optional[Dict[str, Any]],
        query_text: str,
        from_city: str,
        to_city: str,
        travel_date: str,
        intent_type: str,
        mode: str,
        trains_count: int = 0,
        flights_count: int = 0,
        hotels_count: int = 0,
        attractions_count: int = 0,
        latency_ms: int = 0,
    ) -> None:
        log_id = str(uuid.uuid4())
        now = _now_iso()
        user_id = user["id"] if user else None
        user_email = user["email"] if user else "anonymous"

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO travel_search_logs (
                    id, user_id, user_email, query_text, from_city, to_city,
                    travel_date, intent_type, mode, trains_count, flights_count,
                    hotels_count, attractions_count, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    user_id,
                    user_email,
                    query_text[:300],
                    from_city or "",
                    to_city or "",
                    travel_date or "",
                    intent_type or "general",
                    mode or "commercial",
                    int(trains_count),
                    int(flights_count),
                    int(hotels_count),
                    int(attractions_count),
                    int(latency_ms),
                    now,
                ),
            )
            # Also enrich user's frequent_destinations & search_count!
            if user_id:
                row = conn.execute(
                    "SELECT frequent_destinations, search_count, home_city FROM travel_users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if row:
                    try:
                        dests = json.loads(row["frequent_destinations"] or "[]")
                    except Exception:
                        dests = []
                    if to_city and to_city != row["home_city"]:
                        dests = [to_city] + [d for d in dests if d != to_city]
                        dests = dests[:6]
                    new_count = int(row["search_count"] or 0) + 1
                    conn.execute(
                        "UPDATE travel_users SET frequent_destinations = ?, search_count = ? WHERE id = ?",
                        (json.dumps(dests, ensure_ascii=False), new_count, user_id),
                    )
            conn.commit()

        self._async_sync_to_supabase(
            "POST",
            "travel_search_logs",
            {
                "id": log_id,
                "user_id": user_id,
                "user_email": user_email,
                "query_text": query_text[:300],
                "from_city": from_city or "",
                "to_city": to_city or "",
                "travel_date": travel_date or "",
                "intent_type": intent_type or "general",
                "mode": mode or "commercial",
                "trains_count": int(trains_count),
                "flights_count": int(flights_count),
                "hotels_count": int(hotels_count),
                "attractions_count": int(attractions_count),
                "latency_ms": int(latency_ms),
                "created_at": now,
            },
        )

    # ------------------------------------------------------------------
    # Dynamic Smart Prompt Recommendation Engine
    # ------------------------------------------------------------------
    def generate_personalized_recommendations(
        self,
        user: Optional[Dict[str, Any]],
        context_from: str = "",
        context_to: str = "",
        context_date: str = "",
        currency: str = "CNY",
        shuffle_seed: int = 0,
    ) -> Dict[str, Any]:
        """
        Generates dynamic, personalized prompt recommendations based on:
        1. Logged-in User Profile (home_city, preferred_transport, hotel_budget, frequent_destinations, travel_tags)
        2. User's recent search history & saved trips
        3. Active session conversation context (context_from, context_to, context_date)
        4. Time of day & day of week awareness
        5. Admin-configured prompt templates
        """
        now_local = datetime.datetime.now()
        weekday = now_local.weekday()  # 0=Mon .. 6=Sun
        hour = now_local.hour

        if weekday in (3, 4):  # Thu/Fri -> Weekend planning
            day_label = "本周六"
        elif weekday == 5:
            day_label = "周日"
        elif hour >= 18:
            day_label = "明天"
        else:
            day_label = "明天"

        home_city = (
            (user.get("home_city") if user else None)
            or context_from
            or "杭州"
        )
        pref_transport = (user.get("preferred_transport") if user else None) or "all"
        hotel_budget = (user.get("hotel_budget") if user else None) or "comfort"
        user_tags = (user.get("travel_tags") if user else None) or []
        freq_dests = (user.get("frequent_destinations") if user else None) or ["上海", "北京", "河内"]

        budget_label_map = {
            "economy": "地铁旁高性价比酒店",
            "comfort": "核心商圈高分舒适酒店",
            "luxury": "五星豪华/地标景观酒店",
        }
        hotel_style = budget_label_map.get(hotel_budget, "高分舒适酒店")

        # Pick domestic & international target cities based on user's frequent_destinations + context
        intl_candidates = ["河内", "东京", "曼谷", "新加坡", "首尔", "香港"]
        dom_candidates = ["上海", "北京", "成都", "杭州", "深圳", "广州", "南京", "厦门", "西安"]
        dom_candidates = [c for c in dom_candidates if c != home_city]

        user_dom = [c for c in freq_dests if c not in intl_candidates and c != home_city]
        user_intl = [c for c in freq_dests if c in intl_candidates]

        dest_city = context_to if (context_to and context_to != home_city) else (
            user_dom[0] if user_dom else dom_candidates[0]
        )
        alt_dest_city = user_dom[1] if len(user_dom) > 1 else dom_candidates[1]
        intl_city = (
            context_to
            if context_to in intl_candidates
            else (user_intl[0] if user_intl else intl_candidates[shuffle_seed % len(intl_candidates)])
        )

        # Rotate slightly if user clicked "换一批" (shuffle_seed > 0)
        if shuffle_seed > 0:
            dest_city = dom_candidates[(shuffle_seed) % len(dom_candidates)]
            alt_dest_city = dom_candidates[(shuffle_seed + 2) % len(dom_candidates)]
            intl_city = intl_candidates[(shuffle_seed) % len(intl_candidates)]

        chips: List[Dict[str, Any]] = []

        # 1. Context-driven follow-up recommendations (if user just searched a city!)
        if context_to:
            target_date_lbl = context_date or day_label
            chips.append(
                {
                    "id": f"ctx_hotel_{context_to}",
                    "title": f"{context_to}{hotel_style}",
                    "prompt": f"{target_date_lbl}去{context_to}，推荐{hotel_style}并换算{currency}价格",
                    "icon": "building-2",
                    "badge": "当前行程关联",
                    "personalized": True,
                }
            )
            chips.append(
                {
                    "id": f"ctx_route_{context_to}",
                    "title": f"{context_to}酒店+必去景点顺路画路线",
                    "prompt": f"{target_date_lbl}去{context_to}，推荐高分酒店和必去景点门票，并规划顺路游览路线",
                    "icon": "compass",
                    "badge": "智能动线",
                    "personalized": True,
                }
            )
            if context_from and context_from != context_to:
                chips.append(
                    {
                        "id": f"ctx_return_{context_to}_{context_from}",
                        "title": f"返程 {context_to}➔{context_from} 车票/航班",
                        "prompt": f"大后天从{context_to}回{context_from}有哪些高铁或航班？",
                        "icon": "repeat",
                        "badge": "返程推荐",
                        "personalized": True,
                    }
                )

        # 2. Recent user history recommendations (from DB logs)
        if user:
            recent_logs = self.list_user_history(user["id"], limit=3)
            for log in recent_logs:
                r_to = log.get("to_city")
                r_from = log.get("from_city") or home_city
                if r_to and r_to != context_to and len(chips) < 4:
                    chips.append(
                        {
                            "id": f"hist_{r_from}_{r_to}",
                            "title": f"常查：{day_label}{r_from}去{r_to}余票与酒店",
                            "prompt": f"{day_label}从{r_from}去{r_to}，查一下最新车票/机票和{hotel_style}",
                            "icon": "history",
                            "badge": "历史常查",
                            "personalized": True,
                        }
                    )
                    break

        # 3. Admin-configured templates interpolated with user profile
        templates = self.list_prompt_templates(only_active=True)
        # Score templates by user preference match
        scored_templates: List[Tuple[int, Dict[str, Any]]] = []
        for tpl in templates:
            score = int(tpl.get("priority") or 100)
            if pref_transport != "all" and tpl.get("target_transport") == pref_transport:
                score += 45
            tpl_tags = tpl.get("target_tags") or []
            overlap = len(set(user_tags).intersection(set(tpl_tags)))
            score += overlap * 25
            scored_templates.append((score, tpl))

        scored_templates.sort(key=lambda x: x[0], reverse=True)

        for idx, (_, tpl) in enumerate(scored_templates):
            c_dest = dest_city if idx % 2 == 0 else alt_dest_city
            fmt_vars = {
                "home_city": home_city,
                "dest_city": c_dest,
                "intl_city": intl_city,
                "day_label": day_label,
                "hotel_style": hotel_style,
            }
            try:
                title_rendered = tpl["title"].format(**fmt_vars)
                prompt_rendered = tpl["prompt_text"].format(**fmt_vars)
            except Exception:
                title_rendered = tpl["title"]
                prompt_rendered = tpl["prompt_text"]

            chips.append(
                {
                    "id": tpl["id"],
                    "title": title_rendered,
                    "prompt": prompt_rendered,
                    "icon": tpl.get("icon") or "sparkles",
                    "badge": "画像定制" if user else "智能推荐",
                    "personalized": bool(user),
                }
            )

        # Deduplicate by title and cap at configured count
        settings = self.get_settings_dict()
        max_count = int(settings.get("recommendation_count", "8") or 8)
        seen_titles = set()
        unique_chips = []
        for c in chips:
            if c["title"] not in seen_titles:
                seen_titles.add(c["title"])
                unique_chips.append(c)
            if len(unique_chips) >= max_count:
                break

        return {
            "ok": True,
            "user_bound": bool(user),
            "profile_summary": {
                "nickname": user["nickname"] if user else "访客模式",
                "home_city": home_city,
                "preferred_transport": pref_transport,
                "hotel_budget": hotel_budget,
                "frequent_destinations": freq_dests,
                "travel_tags": user_tags,
            },
            "marquee": {
                "auto_scroll": settings.get("marquee_auto_scroll", "true").lower() == "true",
                "speed_sec": int(settings.get("marquee_speed_sec", "32") or 32),
            },
            "recommendations": unique_chips,
        }

    def increment_prompt_click(self, prompt_id: str) -> None:
        if not prompt_id:
            return
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE travel_prompt_templates SET click_count = click_count + 1 WHERE id = ?",
                (prompt_id,),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # User Trips & History Queries
    # ------------------------------------------------------------------
    def list_user_history(self, user_id: str, limit: int = 15) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM travel_search_logs WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_saved_trips(self, user_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if user_id:
                rows = conn.execute(
                    "SELECT * FROM travel_saved_trips WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM travel_saved_trips ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["itinerary_json"] = json.loads(d.get("itinerary_json") or "{}")
                except Exception:
                    d["itinerary_json"] = {}
                out.append(d)
            return out

    def save_trip(
        self,
        user: Optional[Dict[str, Any]],
        title: str,
        from_city: str,
        to_city: str,
        travel_date: str,
        days: int = 3,
        summary: str = "",
        itinerary_json: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        trip_id = str(uuid.uuid4())
        now = _now_iso()
        user_id = user["id"] if user else None
        user_email = user["email"] if user else "guest@travel.ai"
        itin_obj = itinerary_json or {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO travel_saved_trips (
                    id, user_id, user_email, title, from_city, to_city,
                    travel_date, days, summary, itinerary_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                """,
                (
                    trip_id,
                    user_id,
                    user_email,
                    title[:120],
                    from_city or "",
                    to_city or "目的地",
                    travel_date or "",
                    int(days or 3),
                    summary[:500],
                    json.dumps(itin_obj, ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
        trip_dict = {
            "id": trip_id,
            "user_id": user_id,
            "user_email": user_email,
            "title": title[:120],
            "from_city": from_city or "",
            "to_city": to_city or "目的地",
            "travel_date": travel_date or "",
            "days": int(days or 3),
            "summary": summary[:500],
            "itinerary_json": itin_obj,
            "status": "planned",
            "created_at": now,
        }
        self._async_sync_to_supabase("POST", "travel_saved_trips", trip_dict)
        return trip_dict

    def delete_trip(self, trip_id: str, user_id: Optional[str] = None) -> bool:
        with self._lock, self._connect() as conn:
            if user_id:
                cur = conn.execute(
                    "DELETE FROM travel_saved_trips WHERE id = ? AND user_id = ?",
                    (trip_id, user_id),
                )
            else:
                cur = conn.execute("DELETE FROM travel_saved_trips WHERE id = ?", (trip_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        if deleted:
            self._async_sync_to_supabase("DELETE", "travel_saved_trips", None, query=f"id=eq.{trip_id}")
        return deleted

    def update_trip_status(self, trip_id: str, status: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE travel_saved_trips SET status = ? WHERE id = ?",
                (status, trip_id),
            )
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            self._async_sync_to_supabase("PATCH", "travel_saved_trips", {"status": status}, query=f"id=eq.{trip_id}")
        return ok

    # ------------------------------------------------------------------
    # Admin CRUD & Analytics
    # ------------------------------------------------------------------
    def list_users(self, include_hash: bool = False) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM travel_users ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_user(r, include_hash=include_hash) for r in rows]

    def delete_user(self, user_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM travel_users WHERE id = ?", (user_id,))
            conn.execute("DELETE FROM travel_sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            self._async_sync_to_supabase("DELETE", "travel_users", None, query=f"id=eq.{user_id}")
        return ok

    def list_prompt_templates(self, only_active: bool = False) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            sql = "SELECT * FROM travel_prompt_templates"
            if only_active:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY priority DESC, click_count DESC"
            rows = conn.execute(sql).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["is_active"] = bool(d.get("is_active"))
                try:
                    d["target_tags"] = json.loads(d.get("target_tags") or "[]")
                except Exception:
                    d["target_tags"] = []
                out.append(d)
            return out

    def upsert_prompt_template(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        tpl_id = (payload.get("id") or "").strip() or str(uuid.uuid4())
        title = (payload.get("title") or "").strip() or "未命名推荐词"
        prompt_text = (payload.get("prompt_text") or "").strip() or title
        icon = (payload.get("icon") or "sparkles").strip()
        category = (payload.get("category") or "route").strip()
        target_transport = (payload.get("target_transport") or "all").strip()
        raw_tags = payload.get("target_tags") or []
        if isinstance(raw_tags, str):
            tags_list = [x.strip() for x in raw_tags.split(",") if x.strip()]
        elif isinstance(raw_tags, list):
            tags_list = [str(x).strip() for x in raw_tags if str(x).strip()]
        else:
            tags_list = []
        priority = int(payload.get("priority", 100))
        is_active = 1 if payload.get("is_active", True) else 0
        now = _now_iso()

        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT click_count, created_at FROM travel_prompt_templates WHERE id = ?",
                (tpl_id,),
            ).fetchone()
            click_count = int(existing["click_count"]) if existing else int(payload.get("click_count", 0))
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT OR REPLACE INTO travel_prompt_templates (
                    id, title, prompt_text, icon, category, target_transport,
                    target_tags, priority, is_active, click_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tpl_id,
                    title,
                    prompt_text,
                    icon,
                    category,
                    target_transport,
                    json.dumps(tags_list, ensure_ascii=False),
                    priority,
                    is_active,
                    click_count,
                    created_at,
                ),
            )
            conn.commit()

        item = {
            "id": tpl_id,
            "title": title,
            "prompt_text": prompt_text,
            "icon": icon,
            "category": category,
            "target_transport": target_transport,
            "target_tags": tags_list,
            "priority": priority,
            "is_active": bool(is_active),
            "click_count": click_count,
            "created_at": created_at,
        }
        self._async_sync_to_supabase("POST", "travel_prompt_templates", item)
        return item

    def delete_prompt_template(self, tpl_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM travel_prompt_templates WHERE id = ?", (tpl_id,))
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            self._async_sync_to_supabase("DELETE", "travel_prompt_templates", None, query=f"id=eq.{tpl_id}")
        return ok

    def list_search_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM travel_search_logs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_settings_dict(self) -> Dict[str, str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM travel_system_settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    def list_settings(self) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM travel_system_settings ORDER BY key ASC").fetchall()
            return [dict(r) for r in rows]

    def update_settings(self, updates: Dict[str, Any]) -> List[Dict[str, Any]]:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            for k, v in updates.items():
                conn.execute(
                    """
                    INSERT INTO travel_system_settings (key, value, description, updated_at)
                    VALUES (?, ?, '', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (str(k), str(v), now),
                )
                if k == "trip_alliance_id" and str(v).strip():
                    os.environ["TRIP_COM_ALLIANCEID"] = str(v).strip()
                elif k == "trip_sid" and str(v).strip():
                    os.environ["TRIP_COM_SID"] = str(v).strip()
                self._async_sync_to_supabase(
                    "POST",
                    "travel_system_settings",
                    {"key": str(k), "value": str(v), "updated_at": now},
                )
            conn.commit()
        return self.list_settings()

    def get_admin_overview(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) AS c FROM travel_users").fetchone()["c"]
            vip_users = conn.execute(
                "SELECT COUNT(*) AS c FROM travel_users WHERE role IN ('vip', 'admin')"
            ).fetchone()["c"]
            total_searches = conn.execute("SELECT COUNT(*) AS c FROM travel_search_logs").fetchone()["c"]
            total_trips = conn.execute("SELECT COUNT(*) AS c FROM travel_saved_trips").fetchone()["c"]
            avg_lat_row = conn.execute(
                "SELECT AVG(latency_ms) AS avg_ms FROM travel_search_logs WHERE latency_ms > 0"
            ).fetchone()
            avg_latency_ms = int(avg_lat_row["avg_ms"] or 340)

            top_dest_rows = conn.execute(
                """
                SELECT to_city AS city, COUNT(*) AS count
                FROM travel_search_logs
                WHERE to_city != ''
                GROUP BY to_city
                ORDER BY count DESC
                LIMIT 6
                """
            ).fetchall()

            top_home_rows = conn.execute(
                """
                SELECT home_city AS city, COUNT(*) AS count
                FROM travel_users
                WHERE home_city != ''
                GROUP BY home_city
                ORDER BY count DESC
                LIMIT 6
                """
            ).fetchall()

            total_clicks_row = conn.execute(
                "SELECT SUM(click_count) AS s FROM travel_prompt_templates"
            ).fetchone()
            total_prompt_clicks = int(total_clicks_row["s"] or 0)

        return {
            "ok": True,
            "kpis": {
                "total_users": total_users,
                "vip_users": vip_users,
                "total_searches": total_searches,
                "total_saved_trips": total_trips,
                "avg_latency_ms": avg_latency_ms,
                "total_prompt_clicks": total_prompt_clicks,
            },
            "top_destinations": [dict(r) for r in top_dest_rows],
            "top_home_cities": [dict(r) for r in top_home_rows],
            "recent_logs": self.list_search_logs(limit=12),
            "supabase": self.check_supabase_status(),
        }


# ----------------------------------------------------------------------
# aiohttp Request Helpers & Route Handlers
# ----------------------------------------------------------------------
def extract_token_from_request(request: web.Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    cookie_token = request.cookies.get("travel_auth_token", "")
    if cookie_token:
        return cookie_token.strip()
    return request.query.get("token", "").strip()


def get_current_user(request: web.Request, store: UserAndAdminStore) -> Optional[Dict[str, Any]]:
    token = extract_token_from_request(request)
    return store.get_user_by_token(token) if token else None


def setup_user_and_admin_routes(app: web.Application, store: UserAndAdminStore, static_dir: str) -> None:
    """Attach `/admin`, `/api/auth/*`, `/api/user/*`, `/api/recommendations`, `/api/admin/*` routes."""

    async def handle_admin_page(request: web.Request) -> web.FileResponse:
        return web.FileResponse(os.path.join(static_dir, "admin.html"))

    async def handle_auth_register(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        user, err = store.register_user(
            email=body.get("email", ""),
            password=body.get("password", ""),
            nickname=body.get("nickname", ""),
            home_city=body.get("home_city", "杭州"),
            preferred_transport=body.get("preferred_transport", "all"),
            hotel_budget=body.get("hotel_budget", "comfort"),
            travel_tags=body.get("travel_tags"),
        )
        if err or not user:
            return web.json_response({"ok": False, "error": err or "注册失败"}, status=400)
        _, token, _ = store.authenticate_user(
            body.get("email", ""),
            body.get("password", ""),
            ip=request.remote or "",
            ua=request.headers.get("User-Agent", ""),
        )
        resp = web.json_response({"ok": True, "user": user, "token": token})
        if token:
            resp.set_cookie("travel_auth_token", token, max_age=14 * 86400, path="/", samesite="Lax")
        return resp

    async def handle_auth_login(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        user, token, err = store.authenticate_user(
            email=body.get("email", ""),
            password=body.get("password", ""),
            ip=request.remote or "",
            ua=request.headers.get("User-Agent", ""),
        )
        if err or not user or not token:
            return web.json_response({"ok": False, "error": err or "登录失败"}, status=401)
        resp = web.json_response({"ok": True, "user": user, "token": token})
        resp.set_cookie("travel_auth_token", token, max_age=14 * 86400, path="/", samesite="Lax")
        return resp

    async def handle_auth_logout(request: web.Request) -> web.Response:
        token = extract_token_from_request(request)
        store.logout_token(token)
        resp = web.json_response({"ok": True})
        resp.del_cookie("travel_auth_token", path="/")
        return resp

    async def handle_auth_me(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        if not user:
            return web.json_response(
                {
                    "ok": True,
                    "authenticated": False,
                    "user": None,
                    "demo_accounts": [
                        {"email": "admin@travel.ai", "password": "admin123456", "label": "管理员 (杭州 · 全域监控)"},
                        {"email": "vip@travel.ai", "password": "vip123456", "label": "林远行 VIP (上海 · 跨境差旅)"},
                        {"email": "demo@travel.ai", "password": "demo123456", "label": "苏小游 (北京 · 高铁特种兵)"},
                    ],
                }
            )
        return web.json_response(
            {
                "ok": True,
                "authenticated": True,
                "user": user,
                "history": store.list_user_history(user["id"], limit=10),
                "saved_trips": store.list_saved_trips(user["id"], limit=10),
            }
        )

    async def handle_user_profile_update(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        if not user:
            return web.json_response({"ok": False, "error": "请先登录"}, status=401)
        try:
            body = await request.json()
        except Exception:
            body = {}
        updated = store.update_user_profile(user["id"], body, allow_admin_fields=False)
        return web.json_response({"ok": True, "user": updated})

    async def handle_user_trips_get(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        if not user:
            return web.json_response({"ok": True, "trips": []})
        return web.json_response({"ok": True, "trips": store.list_saved_trips(user["id"])})

    async def handle_user_trips_post(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        try:
            body = await request.json()
        except Exception:
            body = {}
        trip = store.save_trip(
            user=user,
            title=body.get("title") or "收藏行程",
            from_city=body.get("from_city") or "",
            to_city=body.get("to_city") or "",
            travel_date=body.get("travel_date") or "",
            days=int(body.get("days") or 3),
            summary=body.get("summary") or "",
            itinerary_json=body.get("itinerary_json"),
        )
        return web.json_response({"ok": True, "trip": trip})

    async def handle_user_trips_delete(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        trip_id = request.match_info.get("trip_id", "")
        ok = store.delete_trip(trip_id, user_id=user["id"] if user else None)
        return web.json_response({"ok": ok})

    async def handle_recommendations(request: web.Request) -> web.Response:
        user = get_current_user(request, store)
        ctx_from = request.query.get("from_city", "").strip()
        ctx_to = request.query.get("to_city", "").strip()
        ctx_date = request.query.get("date", "").strip()
        currency = request.query.get("currency", "CNY").strip()
        try:
            seed = int(request.query.get("seed", "0"))
        except ValueError:
            seed = 0
        res = store.generate_personalized_recommendations(
            user=user,
            context_from=ctx_from,
            context_to=ctx_to,
            context_date=ctx_date,
            currency=currency,
            shuffle_seed=seed,
        )
        return web.json_response(res)

    async def handle_recommendation_click(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        store.increment_prompt_click(body.get("id", ""))
        return web.json_response({"ok": True})

    # Admin APIs
    async def handle_admin_overview(request: web.Request) -> web.Response:
        return web.json_response(store.get_admin_overview())

    async def handle_admin_users_list(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "users": store.list_users()})

    async def handle_admin_users_create(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        user, err = store.register_user(
            email=body.get("email", ""),
            password=body.get("password", "123456"),
            nickname=body.get("nickname", ""),
            home_city=body.get("home_city", "杭州"),
            preferred_transport=body.get("preferred_transport", "all"),
            hotel_budget=body.get("hotel_budget", "comfort"),
            travel_tags=body.get("travel_tags"),
        )
        if err or not user:
            return web.json_response({"ok": False, "error": err or "创建失败"}, status=400)
        if body.get("role") and body.get("role") != "user":
            user = store.update_user_profile(user["id"], {"role": body["role"]}, allow_admin_fields=True)
        return web.json_response({"ok": True, "user": user})

    async def handle_admin_users_update(request: web.Request) -> web.Response:
        uid = request.match_info.get("user_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        updated = store.update_user_profile(uid, body, allow_admin_fields=True)
        return web.json_response({"ok": bool(updated), "user": updated})

    async def handle_admin_users_delete(request: web.Request) -> web.Response:
        uid = request.match_info.get("user_id", "")
        return web.json_response({"ok": store.delete_user(uid)})

    async def handle_admin_prompts_list(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "prompts": store.list_prompt_templates(only_active=False)})

    async def handle_admin_prompts_upsert(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        tpl = store.upsert_prompt_template(body)
        return web.json_response({"ok": True, "prompt": tpl})

    async def handle_admin_prompts_delete(request: web.Request) -> web.Response:
        pid = request.match_info.get("prompt_id", "")
        return web.json_response({"ok": store.delete_prompt_template(pid)})

    async def handle_admin_logs_list(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "logs": store.list_search_logs(limit=150)})

    async def handle_admin_trips_list(request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "trips": store.list_saved_trips(limit=100)})

    async def handle_admin_trips_update(request: web.Request) -> web.Response:
        tid = request.match_info.get("trip_id", "")
        try:
            body = await request.json()
        except Exception:
            body = {}
        ok = store.update_trip_status(tid, body.get("status", "planned"))
        return web.json_response({"ok": ok})

    async def handle_admin_trips_delete(request: web.Request) -> web.Response:
        tid = request.match_info.get("trip_id", "")
        return web.json_response({"ok": store.delete_trip(tid)})

    async def handle_admin_settings_list(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "settings": store.list_settings(),
                "supabase": store.check_supabase_status(),
            }
        )

    async def handle_admin_settings_update(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        updated = store.update_settings(body)
        return web.json_response({"ok": True, "settings": updated})

    app.router.add_get("/admin", handle_admin_page)
    app.router.add_post("/api/auth/register", handle_auth_register)
    app.router.add_post("/api/auth/login", handle_auth_login)
    app.router.add_post("/api/auth/logout", handle_auth_logout)
    app.router.add_get("/api/auth/me", handle_auth_me)
    app.router.add_put("/api/user/profile", handle_user_profile_update)
    app.router.add_get("/api/user/trips", handle_user_trips_get)
    app.router.add_post("/api/user/trips", handle_user_trips_post)
    app.router.add_delete("/api/user/trips/{trip_id}", handle_user_trips_delete)
    app.router.add_get("/api/recommendations", handle_recommendations)
    app.router.add_post("/api/recommendations/click", handle_recommendation_click)

    app.router.add_get("/api/admin/overview", handle_admin_overview)
    app.router.add_get("/api/admin/users", handle_admin_users_list)
    app.router.add_post("/api/admin/users", handle_admin_users_create)
    app.router.add_put("/api/admin/users/{user_id}", handle_admin_users_update)
    app.router.add_delete("/api/admin/users/{user_id}", handle_admin_users_delete)
    app.router.add_get("/api/admin/prompts", handle_admin_prompts_list)
    app.router.add_post("/api/admin/prompts", handle_admin_prompts_upsert)
    app.router.add_put("/api/admin/prompts/{prompt_id}", handle_admin_prompts_upsert)
    app.router.add_delete("/api/admin/prompts/{prompt_id}", handle_admin_prompts_delete)
    app.router.add_get("/api/admin/logs", handle_admin_logs_list)
    app.router.add_get("/api/admin/trips", handle_admin_trips_list)
    app.router.add_put("/api/admin/trips/{trip_id}", handle_admin_trips_update)
    app.router.add_delete("/api/admin/trips/{trip_id}", handle_admin_trips_delete)
    app.router.add_get("/api/admin/settings", handle_admin_settings_list)
    app.router.add_put("/api/admin/settings", handle_admin_settings_update)
