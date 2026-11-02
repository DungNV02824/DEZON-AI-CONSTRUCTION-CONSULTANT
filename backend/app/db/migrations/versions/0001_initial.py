"""initial schema — DDL theo đặc tả mục 4.2

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = r"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TYPE verification_status AS ENUM ('verified','on_dezon','reference','unavailable');
CREATE TYPE source_type AS ENUM ('dezon_api','contractor_verified','crawl_public','dezon_editorial','csv_import');
CREATE TYPE building_type AS ENUM ('nha_pho','biet_thu','can_ho','van_phong','khach_san','khac');
CREATE TYPE work_scope AS ENUM ('xay_tho','hoan_thien','tron_goi','noi_that');
CREATE TYPE quality_tier AS ENUM ('tiet_kiem','trung_binh','kha','cao_cap');

CREATE TABLE data_sources (
  id            BIGSERIAL PRIMARY KEY,
  source_type   source_type NOT NULL,
  url           TEXT,
  label         TEXT NOT NULL,
  status        verification_status NOT NULL DEFAULT 'on_dezon',
  fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_updated_at TIMESTAMPTZ,
  verified_by   TEXT,
  verified_at   TIMESTAMPTZ,
  raw_hash      TEXT
);
CREATE UNIQUE INDEX data_sources_url_uq ON data_sources (url) WHERE url IS NOT NULL;

CREATE TABLE contractors (
  id              BIGSERIAL PRIMARY KEY,
  external_id     TEXT UNIQUE,
  slug            TEXT UNIQUE NOT NULL,
  name            TEXT NOT NULL,
  badge           TEXT,
  province        TEXT,
  founded_year    INT,
  years_experience INT,
  staff_workers   INT,
  staff_office    INT,
  rough_price_min NUMERIC(12,0),
  rough_price_max NUMERIC(12,0),
  finish_price_min NUMERIC(12,0),
  finish_price_max NUMERIC(12,0),
  typical_project_value NUMERIC(16,0),
  dezon_project_count INT,
  has_warranty_info BOOLEAN,
  warranty_text   TEXT,
  description     TEXT,
  avatar_url      TEXT,
  profile_url     TEXT NOT NULL,
  profile_completeness NUMERIC(4,3),
  is_active       BOOLEAN NOT NULL DEFAULT true,
  source_id       BIGINT REFERENCES data_sources(id),
  field_sources   JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON contractors USING gin (name gin_trgm_ops);

CREATE TABLE contractor_capabilities (
  id             BIGSERIAL PRIMARY KEY,
  contractor_id  BIGINT NOT NULL REFERENCES contractors(id) ON DELETE CASCADE,
  building_type  building_type,
  work_scope     work_scope,
  province       TEXT,
  district       TEXT,
  source_id      BIGINT REFERENCES data_sources(id)
);
CREATE INDEX ON contractor_capabilities (province, building_type);

CREATE TABLE projects (
  id             BIGSERIAL PRIMARY KEY,
  external_id    TEXT UNIQUE,
  slug           TEXT UNIQUE NOT NULL,
  name           TEXT NOT NULL,
  building_type  building_type,
  province       TEXT,
  district       TEXT,
  floors         INT,
  land_area_m2   NUMERIC(10,2),
  floor_area_m2  NUMERIC(10,2),
  start_date     DATE,
  end_date       DATE,
  status         TEXT,
  description    TEXT,
  project_url    TEXT NOT NULL,
  source_id      BIGINT REFERENCES data_sources(id),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE project_contractors (
  project_id     BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  contractor_id  BIGINT REFERENCES contractors(id) ON DELETE CASCADE,
  role           TEXT,
  PRIMARY KEY (project_id, contractor_id, role)
);

CREATE TABLE project_images (
  id          BIGSERIAL PRIMARY KEY,
  project_id  BIGINT REFERENCES projects(id) ON DELETE CASCADE,
  url         TEXT NOT NULL,
  caption     TEXT,
  sort_order  INT DEFAULT 0
);

CREATE TABLE construction_rates (
  id             BIGSERIAL PRIMARY KEY,
  building_type  building_type NOT NULL,
  province       TEXT NOT NULL,
  work_scope     work_scope NOT NULL,
  quality_tier   quality_tier NOT NULL DEFAULT 'trung_binh',
  price_min      NUMERIC(12,0) NOT NULL,
  price_max      NUMERIC(12,0) NOT NULL,
  effective_from DATE NOT NULL,
  effective_to   DATE,
  source_id      BIGINT NOT NULL REFERENCES data_sources(id),
  UNIQUE (building_type, province, work_scope, quality_tier, effective_from)
);

CREATE TABLE estimate_rules (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL,
  group_name  TEXT NOT NULL,
  label_vi    TEXT NOT NULL,
  coefficient NUMERIC(5,3) NOT NULL,
  base        TEXT NOT NULL,
  is_default  BOOLEAN NOT NULL DEFAULT false,
  version     INT NOT NULL DEFAULT 1,
  approved    BOOLEAN NOT NULL DEFAULT false,
  source_id   BIGINT REFERENCES data_sources(id),
  UNIQUE (code, version)
);

CREATE TABLE chat_sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     TEXT,
  slots       JSONB NOT NULL DEFAULT '{}'::jsonb,
  stage       TEXT NOT NULL DEFAULT 'collect',
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE chat_messages (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role        TEXT NOT NULL,
  content     TEXT,
  tool_name   TEXT,
  tool_payload JSONB,
  ui_blocks   JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON chat_messages (session_id, id);

CREATE TABLE estimate_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  chat_session_id UUID REFERENCES chat_sessions(id),
  input           JSONB NOT NULL,
  rules_version   INT NOT NULL,
  rate_id         BIGINT REFERENCES construction_rates(id),
  converted_area_m2 NUMERIC(10,2) NOT NULL,
  cost_min        NUMERIC(16,0),
  cost_max        NUMERIC(16,0),
  assumptions     JSONB NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE estimate_items (
  id                 BIGSERIAL PRIMARY KEY,
  estimate_session_id UUID REFERENCES estimate_sessions(id) ON DELETE CASCADE,
  rule_code          TEXT NOT NULL,
  label_vi           TEXT NOT NULL,
  area_m2            NUMERIC(10,2) NOT NULL,
  coefficient        NUMERIC(5,3) NOT NULL,
  converted_m2       NUMERIC(10,2) NOT NULL
);

CREATE TABLE quote_requests (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  estimate_session_id UUID REFERENCES estimate_sessions(id),
  contractor_id       BIGINT REFERENCES contractors(id),
  customer_name       TEXT, customer_phone TEXT, customer_email TEXT,
  note                TEXT,
  status              TEXT NOT NULL DEFAULT 'new',
  created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE contractor_quotes (
  id               BIGSERIAL PRIMARY KEY,
  quote_request_id UUID REFERENCES quote_requests(id),
  contractor_id    BIGINT REFERENCES contractors(id),
  total_amount     NUMERIC(16,0),
  price_per_m2     NUMERIC(12,0),
  scope_text       TEXT,
  file_url         TEXT,
  received_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE matching_weights (
  version     INT PRIMARY KEY,
  weights     JSONB NOT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT false,
  note        TEXT,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE recommendation_logs (
  id                  BIGSERIAL PRIMARY KEY,
  chat_session_id     UUID REFERENCES chat_sessions(id),
  estimate_session_id UUID REFERENCES estimate_sessions(id),
  weights_version     INT REFERENCES matching_weights(version),
  criteria            JSONB NOT NULL,
  results             JSONB NOT NULL,
  clicked_contractor_ids BIGINT[] DEFAULT '{}',
  created_at          TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE documents (
  id          BIGSERIAL PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_id   BIGINT NOT NULL,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(1536),
  source_id   BIGINT REFERENCES data_sources(id),
  updated_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE (entity_type, entity_id, chunk_index)
);
CREATE INDEX ON documents USING hnsw (embedding vector_cosine_ops);

CREATE TABLE guardrail_violations (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  rule        TEXT NOT NULL,
  detail      JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);

-- Bổ sung (mục 7.2): cấu hình tỉnh lân cận cho nới lỏng bộ lọc
CREATE TABLE province_neighbors (
  province    TEXT NOT NULL,
  neighbor    TEXT NOT NULL,
  PRIMARY KEY (province, neighbor)
);

-- Bổ sung (mục 12): sự kiện đo lường
CREATE TABLE analytics_events (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID,
  event       TEXT NOT NULL,
  props       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON analytics_events (event, created_at);
"""

DROP = r"""
DROP TABLE IF EXISTS analytics_events, province_neighbors, guardrail_violations, documents,
  recommendation_logs, matching_weights, contractor_quotes, quote_requests, estimate_items,
  estimate_sessions, chat_messages, chat_sessions, estimate_rules, construction_rates,
  project_images, project_contractors, projects, contractor_capabilities, contractors,
  data_sources CASCADE;
DROP TYPE IF EXISTS quality_tier, work_scope, building_type, source_type, verification_status;
"""


def upgrade() -> None:
    op.execute(DDL)


def downgrade() -> None:
    op.execute(DROP)
