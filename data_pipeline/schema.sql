-- PropVenture Supabase schema
-- Run in the Supabase SQL editor. Enables RLS on every user-facing table.

create table builders (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    years_active integer,
    total_projects integer,
    created_at timestamptz default now()
);

create table projects (
    id uuid primary key default gen_random_uuid(),
    builder_id uuid references builders(id) not null,
    rera_certificate_no text unique,
    project_name text not null,
    locality text not null,
    district text,
    bhk_config text,
    carpet_area_sqft numeric,
    price_inr numeric,
    construction_stage text, -- 'under_construction' | 'ready_to_move'
    possession_date date,
    rera_registration_date date,
    amenities jsonb default '[]',
    source text default 'manual_rera_export', -- documents provenance
    created_at timestamptz default now()
);

create table builder_track_record (
    id uuid primary key default gen_random_uuid(),
    builder_id uuid references builders(id) not null,
    project_id uuid references projects(id),
    possession_delay_months integer default 0,
    complaint_count integer default 0,
    complaint_summary text,
    created_at timestamptz default now()
);

create table price_deviation_flags (
    id uuid primary key default gen_random_uuid(),
    project_id uuid references projects(id) not null,
    expected_price_low numeric,
    expected_price_mid numeric,
    expected_price_high numeric,
    actual_price numeric,
    confidence numeric, -- 0-1, based on builder's historical data volume
    flagged boolean default false,
    reasoning text, -- plain-language explanation, never an accusation
    model_version text,
    created_at timestamptz default now()
);

create table review_sentiment (
    id uuid primary key default gen_random_uuid(),
    project_id uuid references projects(id) not null,
    source text, -- 'google_reviews' | 'news' | etc.
    sentiment_score numeric, -- -1 to 1
    quality_mentioned boolean default false,
    excerpt_summary text, -- paraphrased, never verbatim per copyright rules
    created_at timestamptz default now()
);

-- User-owned tables: RLS is mandatory here, this is the actual privacy boundary

create table wishlists (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users(id) not null,
    project_id uuid references projects(id) not null,
    note text,
    created_at timestamptz default now(),
    unique(user_id, project_id)
);

create table saved_searches (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users(id) not null,
    locality text,
    bhk_config text,
    budget_min numeric,
    budget_max numeric,
    created_at timestamptz default now()
);

-- Enable RLS on every table
alter table builders enable row level security;
alter table projects enable row level security;
alter table builder_track_record enable row level security;
alter table price_deviation_flags enable row level security;
alter table review_sentiment enable row level security;
alter table wishlists enable row level security;
alter table saved_searches enable row level security;

-- Public read-only data: anyone (including anon) can read, nobody but the
-- service role can write. Writes happen only via the ingestion pipeline.
create policy "public read builders" on builders for select using (true);
create policy "public read projects" on projects for select using (true);
create policy "public read track record" on builder_track_record for select using (true);
create policy "public read price flags" on price_deviation_flags for select using (true);
create policy "public read review sentiment" on review_sentiment for select using (true);

-- User-owned data: strictly scoped to the owning user, both read and write
create policy "users read own wishlist" on wishlists for select using (auth.uid() = user_id);
create policy "users write own wishlist" on wishlists for insert with check (auth.uid() = user_id);
create policy "users delete own wishlist" on wishlists for delete using (auth.uid() = user_id);

create policy "users read own searches" on saved_searches for select using (auth.uid() = user_id);
create policy "users write own searches" on saved_searches for insert with check (auth.uid() = user_id);
create policy "users delete own searches" on saved_searches for delete using (auth.uid() = user_id);