CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_records (
    id BIGSERIAL PRIMARY KEY,
    record_key CHAR(64) NOT NULL UNIQUE,
    source_site VARCHAR(100) NOT NULL,
    source_record_id VARCHAR(255),
    category VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    target_region VARCHAR(50) NOT NULL,
    target_condition TEXT,
    qualification_text TEXT,
    min_age SMALLINT CHECK (min_age IS NULL OR min_age >= 0),
    max_age SMALLINT CHECK (max_age IS NULL OR max_age >= 0),
    residency_condition TEXT,
    period_text TEXT,
    application_start_date DATE,
    application_end_date DATE,
    content TEXT,
    application_method TEXT,
    organization VARCHAR(255),
    attachment_links TEXT,
    attachment_files TEXT,
    attachment_text TEXT,
    attachment_status TEXT,
    review_status VARCHAR(20) NOT NULL DEFAULT 'approved' CHECK (review_status IN ('pending', 'approved', 'rejected')),
    is_public BOOLEAN NOT NULL DEFAULT TRUE,
    reviewed_at TIMESTAMP,
    reviewed_by BIGINT,
    content_hash CHAR(64) NOT NULL,
    original_link VARCHAR(1500),
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_records_region_age ON policy_records (target_region, min_age, max_age);
CREATE INDEX IF NOT EXISTS idx_policy_records_dates ON policy_records (application_end_date);
CREATE INDEX IF NOT EXISTS idx_policy_records_content_hash ON policy_records (content_hash);

CREATE TABLE IF NOT EXISTS policy_change_events (
    id BIGSERIAL PRIMARY KEY,
    policy_id BIGINT NOT NULL REFERENCES policy_records(id) ON DELETE CASCADE,
    change_type VARCHAR(20) NOT NULL CHECK (change_type IN ('new', 'updated', 'deadline')),
    event_key VARCHAR(150) UNIQUE,
    previous_content_hash CHAR(64),
    current_content_hash CHAR(64) NOT NULL,
    detected_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_change_events_detected_at ON policy_change_events (detected_at);

CREATE TABLE IF NOT EXISTS user_profiles (
    id BIGSERIAL PRIMARY KEY,
    kakao_user_id BIGINT UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    legal_name VARCHAR(100),
    phone_number VARCHAR(30),
    postal_code VARCHAR(20),
    address_line1 VARCHAR(255),
    address_line2 VARCHAR(255),
    birth_date DATE,
    residency_city VARCHAR(100) NOT NULL DEFAULT '목포',
    residency_months SMALLINT CHECK (residency_months IS NULL OR residency_months >= 0),
    employment_status VARCHAR(50),
    income_band VARCHAR(50),
    education_level VARCHAR(50),
    household_status VARCHAR(50),
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_profiles_active_city ON user_profiles (is_active, residency_city);

CREATE TABLE IF NOT EXISTS user_interests (
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    interest_tag VARCHAR(50) NOT NULL,
    PRIMARY KEY (user_id, interest_tag)
);

CREATE TABLE IF NOT EXISTS policy_wishlists (
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL REFERENCES policy_records(id) ON DELETE CASCADE,
    notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    PRIMARY KEY (user_id, policy_id)
);
CREATE INDEX IF NOT EXISTS idx_policy_wishlists_policy ON policy_wishlists (policy_id);

CREATE TABLE IF NOT EXISTS policy_chunks (
    id BIGSERIAL PRIMARY KEY,
    policy_id BIGINT NOT NULL REFERENCES policy_records(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    embedding_model VARCHAR(100) NOT NULL,
    vector_dimension SMALLINT NOT NULL,
    embedding vector(384) NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE (policy_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_policy_chunks_hash ON policy_chunks (content_hash);
CREATE INDEX IF NOT EXISTS idx_policy_chunks_embedding_hnsw ON policy_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS policy_chat_messages (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    question VARCHAR(500) NOT NULL,
    answer TEXT NOT NULL,
    sources_json JSONB NOT NULL,
    ai_generated BOOLEAN NOT NULL DEFAULT FALSE,
    model_name VARCHAR(100),
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_chat_messages_user_created ON policy_chat_messages (user_id, created_at);

CREATE TABLE IF NOT EXISTS collection_runs (
    id BIGSERIAL PRIMARY KEY,
    run_type VARCHAR(20) NOT NULL CHECK (run_type IN ('collection', 'postprocess')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    message TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_runs_started ON collection_runs (started_at);

CREATE TABLE IF NOT EXISTS application_preparations (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL REFERENCES policy_records(id) ON DELETE RESTRICT,
    policy_title_snapshot VARCHAR(500) NOT NULL,
    policy_content_hash_snapshot CHAR(64) NOT NULL,
    original_link_snapshot VARCHAR(1500),
    policy_verified_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready')),
    source_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE (user_id, policy_id)
);
CREATE INDEX IF NOT EXISTS idx_application_preparations_user_updated ON application_preparations (user_id, updated_at);

CREATE TABLE IF NOT EXISTS application_requirements (
    id BIGSERIAL PRIMARY KEY,
    preparation_id BIGINT NOT NULL REFERENCES application_preparations(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    is_required BOOLEAN NOT NULL DEFAULT TRUE,
    issuing_organization VARCHAR(255),
    validity_text VARCHAR(255),
    submission_format VARCHAR(100),
    evidence_text TEXT,
    preparation_status VARCHAR(30) NOT NULL DEFAULT 'not_started' CHECK (preparation_status IN ('not_started', 'in_progress', 'completed', 'not_applicable')),
    user_note VARCHAR(1000),
    source_type VARCHAR(20) NOT NULL DEFAULT 'manual' CHECK (source_type IN ('manual', 'checklist', 'extracted')),
    extraction_confidence DECIMAL(3,2),
    user_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_requirements_preparation ON application_requirements (preparation_id, sort_order, id);

CREATE TABLE IF NOT EXISTS application_form_fields (
    id BIGSERIAL PRIMARY KEY,
    preparation_id BIGINT NOT NULL REFERENCES application_preparations(id) ON DELETE CASCADE,
    label VARCHAR(300) NOT NULL,
    field_type VARCHAR(20) NOT NULL DEFAULT 'text' CHECK (field_type IN ('text', 'textarea', 'date')),
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    max_length SMALLINT,
    source_evidence TEXT,
    source_type VARCHAR(20) NOT NULL DEFAULT 'manual' CHECK (source_type IN ('manual', 'extracted')),
    autofill_profile_key VARCHAR(50),
    value_text TEXT,
    auto_filled BOOLEAN NOT NULL DEFAULT FALSE,
    user_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order SMALLINT NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_form_fields_preparation ON application_form_fields (preparation_id, sort_order, id);

CREATE TABLE IF NOT EXISTS application_preparation_versions (
    id BIGSERIAL PRIMARY KEY,
    preparation_id BIGINT NOT NULL REFERENCES application_preparations(id) ON DELETE CASCADE,
    version_label VARCHAR(100) NOT NULL,
    requirements_json JSONB NOT NULL,
    form_fields_json JSONB NOT NULL,
    created_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_application_preparation_versions_preparation ON application_preparation_versions (preparation_id, id DESC);

CREATE TABLE IF NOT EXISTS policy_match_candidates (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES policy_change_events(id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL REFERENCES policy_records(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    match_reason VARCHAR(1000) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'notified', 'dismissed')),
    created_at TIMESTAMP NOT NULL,
    UNIQUE (event_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_policy_match_candidates_user_status ON policy_match_candidates (user_id, status);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL REFERENCES policy_match_candidates(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL CHECK (channel IN ('email', 'web_push')),
    destination VARCHAR(1000) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('sent', 'failed', 'skipped')),
    error_message VARCHAR(1000),
    sent_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    UNIQUE (candidate_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status ON notification_deliveries (status, created_at);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
    endpoint VARCHAR(700) NOT NULL UNIQUE,
    p256dh VARCHAR(255) NOT NULL,
    auth_secret VARCHAR(255) NOT NULL,
    content_encoding VARCHAR(30) NOT NULL DEFAULT 'aes128gcm',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    UNIQUE (user_id)
);
