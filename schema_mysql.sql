CREATE TABLE IF NOT EXISTS policy_records (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    record_key CHAR(64) NOT NULL,
    source_site VARCHAR(100) NOT NULL,
    source_record_id VARCHAR(255) NULL,
    category VARCHAR(100) NOT NULL,
    title VARCHAR(500) NOT NULL,
    target_region VARCHAR(50) NOT NULL,
    target_condition TEXT NULL,
    qualification_text TEXT NULL,
    min_age TINYINT UNSIGNED NULL,
    max_age TINYINT UNSIGNED NULL,
    residency_condition TEXT NULL,
    period_text TEXT NULL,
    application_start_date DATE NULL,
    application_end_date DATE NULL,
    content LONGTEXT NULL,
    application_method TEXT NULL,
    organization VARCHAR(255) NULL,
    attachment_links TEXT NULL,
    attachment_files TEXT NULL,
    attachment_text LONGTEXT NULL,
    attachment_status TEXT NULL,
    content_hash CHAR(64) NOT NULL,
    original_link VARCHAR(1500) NULL,
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_policy_records_record_key (record_key),
    KEY idx_policy_records_region_age (target_region, min_age, max_age),
    KEY idx_policy_records_dates (application_end_date),
    KEY idx_policy_records_content_hash (content_hash)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS policy_change_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    policy_id BIGINT UNSIGNED NOT NULL,
    change_type ENUM('new', 'updated') NOT NULL,
    previous_content_hash CHAR(64) NULL,
    current_content_hash CHAR(64) NOT NULL,
    detected_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    KEY idx_policy_change_events_detected_at (detected_at),
    CONSTRAINT fk_policy_change_events_policy
      FOREIGN KEY (policy_id) REFERENCES policy_records(id)
      ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_profiles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    kakao_user_id BIGINT UNSIGNED NULL,
    display_name VARCHAR(100) NOT NULL,
    email VARCHAR(255) NULL,
    birth_date DATE NULL,
    residency_city VARCHAR(100) NOT NULL DEFAULT '목포',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_profiles_kakao_user_id (kakao_user_id),
    KEY idx_user_profiles_active_city (is_active, residency_city)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS user_interests (
    user_id BIGINT UNSIGNED NOT NULL,
    interest_tag VARCHAR(50) NOT NULL,
    PRIMARY KEY (user_id, interest_tag),
    CONSTRAINT fk_user_interests_user
      FOREIGN KEY (user_id) REFERENCES user_profiles(id)
      ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS policy_match_candidates (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_id BIGINT UNSIGNED NOT NULL,
    policy_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    match_reason VARCHAR(1000) NOT NULL,
    status ENUM('pending', 'notified', 'dismissed') NOT NULL DEFAULT 'pending',
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_policy_match_candidates_event_user (event_id, user_id),
    KEY idx_policy_match_candidates_user_status (user_id, status),
    CONSTRAINT fk_policy_match_candidates_event
      FOREIGN KEY (event_id) REFERENCES policy_change_events(id)
      ON DELETE CASCADE,
    CONSTRAINT fk_policy_match_candidates_policy
      FOREIGN KEY (policy_id) REFERENCES policy_records(id)
      ON DELETE CASCADE,
    CONSTRAINT fk_policy_match_candidates_user
      FOREIGN KEY (user_id) REFERENCES user_profiles(id)
      ON DELETE CASCADE
) ENGINE=InnoDB;
