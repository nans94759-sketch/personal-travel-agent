-- ============================================================================
-- 远行客 (Personal Travel Agent) - User System, Smart Recommendations & Admin Schema
-- Compatible with Supabase Postgres
-- ============================================================================

create extension if not exists "pgcrypto";

-- 1. Users Table (用户与出行画像表)
create table if not exists public.travel_users (
    id uuid primary key default gen_random_uuid(),
    email text unique not null,
    password_hash text not null,
    nickname text not null default '旅行家',
    avatar_url text default '',
    role text not null default 'user' check (role in ('user', 'vip', 'admin')),
    status text not null default 'active' check (status in ('active', 'suspended')),
    home_city text not null default '杭州',
    preferred_currency text not null default 'CNY',
    preferred_transport text not null default 'all' check (preferred_transport in ('all', 'train', 'flight', 'transfer')),
    preferred_seat text not null default '二等座 / 经济舱',
    hotel_budget text not null default 'comfort' check (hotel_budget in ('economy', 'comfort', 'luxury')),
    travel_tags jsonb not null default '["高铁出行", "高分酒店", "顺路游览"]'::jsonb,
    frequent_destinations jsonb not null default '["上海", "北京", "河内"]'::jsonb,
    search_count integer not null default 0,
    created_at timestamptz not null default now(),
    last_login_at timestamptz not null default now()
);

-- 2. User Sessions Table (登录会话与 Token 表)
create table if not exists public.travel_sessions (
    token text primary key,
    user_id uuid not null references public.travel_users(id) on delete cascade,
    expires_at timestamptz not null,
    ip_address text default '',
    user_agent text default '',
    created_at timestamptz not null default now()
);

-- 3. Search & AI Audit Logs Table (用户搜索与工具调用日志表)
create table if not exists public.travel_search_logs (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.travel_users(id) on delete set null,
    user_email text default 'anonymous',
    query_text text not null,
    from_city text default '',
    to_city text default '',
    travel_date text default '',
    intent_type text default 'general',
    mode text default 'commercial',
    trains_count integer default 0,
    flights_count integer default 0,
    hotels_count integer default 0,
    attractions_count integer default 0,
    latency_ms integer default 0,
    created_at timestamptz not null default now()
);

-- 4. Saved Trips & Itineraries Table (用户收藏行程与意向单表)
create table if not exists public.travel_saved_trips (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references public.travel_users(id) on delete cascade,
    user_email text default '',
    title text not null,
    from_city text default '',
    to_city text not null,
    travel_date text default '',
    days integer default 3,
    summary text default '',
    itinerary_json jsonb not null default '{}'::jsonb,
    status text not null default 'planned' check (status in ('planned', 'booked', 'completed')),
    created_at timestamptz not null default now()
);

-- 5. Smart Prompt Recommendation Templates (后台智能推荐提示词模板表)
create table if not exists public.travel_prompt_templates (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    prompt_text text not null,
    icon text not null default 'sparkles',
    category text not null default 'route',
    target_transport text not null default 'all',
    target_tags jsonb not null default '[]'::jsonb,
    priority integer not null default 100,
    is_active boolean not null default true,
    click_count integer not null default 0,
    created_at timestamptz not null default now()
);

-- 6. System Settings Table (系统与商业联盟配置表)
create table if not exists public.travel_system_settings (
    key text primary key,
    value text not null,
    description text default '',
    updated_at timestamptz not null default now()
);

create index if not exists idx_travel_search_logs_user on public.travel_search_logs(user_id, created_at desc);
create index if not exists idx_travel_search_logs_created on public.travel_search_logs(created_at desc);
create index if not exists idx_travel_saved_trips_user on public.travel_saved_trips(user_id, created_at desc);
