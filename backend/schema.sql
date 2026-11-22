-- kaoyanruanjian full schema (stage 1-5)
-- Every statement is idempotent and runs automatically on backend startup.
-- Line comments start with -- ; keep semicolons only at the end of a statement.
-- NEVER drop or truncate a table here: user data must survive every restart.

CREATE TABLE IF NOT EXISTS `user` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `username`      VARCHAR(64)  NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL DEFAULT '',
  `created_at`    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `user` (`id`, `username`, `password_hash`) VALUES (1, 'local', '');

CREATE TABLE IF NOT EXISTS `admin` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `username`      VARCHAR(64)  NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `created_at`    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_admin_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `user_session` (
  `token`       CHAR(64)  NOT NULL PRIMARY KEY,
  `user_id`     INT       NOT NULL,
  `is_admin`    TINYINT   NOT NULL DEFAULT 0,
  `created_at`  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `expires_at`  TIMESTAMP NOT NULL,
  INDEX `idx_user` (`user_id`),
  INDEX `idx_expires` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `word` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `word`       VARCHAR(128) NOT NULL,
  `phonetic`   VARCHAR(128) NULL,
  `translation` TEXT        NULL,
  `pos`        VARCHAR(64)  NULL,
  `tag`        VARCHAR(128) NULL,
  `exchange`   TEXT         NULL,
  `source`     VARCHAR(128) NULL,
  `verified`   TINYINT      NOT NULL DEFAULT 0,
  `created_at` TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_word` (`word`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `daily_plan` (
  `id`             INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`        INT  NOT NULL,
  `study_date`     DATE NOT NULL,
  `word_count`     INT  NOT NULL,
  `cycle_count`    INT  NOT NULL,
  `stage`          ENUM('learning','memorized','mastered') NOT NULL DEFAULT 'learning',
  `review_status`  ENUM('not_started','in_progress','done') NOT NULL DEFAULT 'not_started',
  `memorized_at`   TIMESTAMP NULL,
  `mastered_at`    TIMESTAMP NULL,
  `last_review_at` TIMESTAMP NULL,
  `created_at`     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_user_date` (`user_id`, `study_date`),
  INDEX `idx_study_date` (`study_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `progress` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`       INT NOT NULL,
  `word_id`       INT NOT NULL,
  `daily_plan_id` INT NOT NULL,
  `cycle_no`      INT NOT NULL,
  `round`         TINYINT NOT NULL,
  `status`        ENUM('pending','done') NOT NULL DEFAULT 'pending',
  `stage`         ENUM('none','memorized','mastered') NOT NULL DEFAULT 'none',
  `wrong_count`   INT NOT NULL DEFAULT 0,
  `review_stage`  TINYINT NOT NULL DEFAULT 0,
  `review_round`  TINYINT NOT NULL DEFAULT 0,
  `last_review_at` DATETIME NULL,
  `next_due_at`   DATE NULL,
  `updated_at`    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_user_word_round` (`user_id`, `word_id`, `round`),
  INDEX `idx_fetch` (`user_id`, `daily_plan_id`, `status`, `cycle_no`, `round`, `id`),
  INDEX `idx_plan_round` (`daily_plan_id`, `round`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `answer_log` (
  `id`              INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`         INT NOT NULL,
  `word_id`         INT NULL,
  `daily_plan_id`   INT NULL,
  `progress_id`     INT NULL,
  `round`           TINYINT NULL,
  `action`          VARCHAR(32) NOT NULL,
  `user_answer`     TEXT NULL,
  `correct`         TINYINT NULL,
  `self_assessment` VARCHAR(16) NULL,
  `ai_result`       TEXT NULL,
  `created_at`      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_plan` (`user_id`, `daily_plan_id`),
  INDEX `idx_word` (`user_id`, `word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `article` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`       INT NOT NULL,
  `kind`          ENUM('daily','weekly') NOT NULL,
  `daily_plan_id` INT NULL,
  `week_key`      VARCHAR(16) NULL,
  `seq`           TINYINT NOT NULL DEFAULT 1,
  `title`         VARCHAR(255) NULL,
  `body`          MEDIUMTEXT NOT NULL,
  `mode`          ENUM('full','sample') NOT NULL DEFAULT 'full',
  `covered_words` INT NOT NULL DEFAULT 0,
  `total_words`   INT NOT NULL DEFAULT 0,
  `uncovered`     MEDIUMTEXT NULL,
  `revealed`      TINYINT NOT NULL DEFAULT 0,
  `publish_at`    TIMESTAMP NULL,
  `deadline`      TIMESTAMP NULL,
  `generator`     VARCHAR(64) NULL,
  `created_at`    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_daily_article` (`user_id`,`kind`,`daily_plan_id`,`seq`),
  UNIQUE KEY `uk_weekly_article` (`user_id`,`kind`,`week_key`,`seq`),
  INDEX `idx_user_kind` (`user_id`,`kind`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `article_word` (
  `id`                    INT AUTO_INCREMENT PRIMARY KEY,
  `article_id`            INT NOT NULL,
  `user_id`               INT NOT NULL,
  `word_id`               INT NOT NULL,
  `surface`               VARCHAR(128) NOT NULL,
  `translation_snapshot`  TEXT NULL,
  `phonetic_snapshot`     VARCHAR(128) NULL,
  `pos_snapshot`          VARCHAR(64) NULL,
  `position`              INT NOT NULL DEFAULT 0,
  UNIQUE KEY `uk_article_surface` (`article_id`,`surface`),
  INDEX `idx_article` (`article_id`),
  INDEX `idx_word` (`word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `question` (
  `id`               INT AUTO_INCREMENT PRIMARY KEY,
  `article_id`       INT NOT NULL,
  `user_id`          INT NOT NULL,
  `qtype`            ENUM('main_idea','detail','vocabulary','inference','subjective') NOT NULL,
  `stem`             TEXT NOT NULL,
  `options`          MEDIUMTEXT NULL,
  `correct_option`   VARCHAR(8) NULL,
  `reference_answer` MEDIUMTEXT NULL,
  `word_id`          INT NULL,
  `ai_graded`        TINYINT NOT NULL DEFAULT 0,
  `sort_no`          TINYINT NOT NULL DEFAULT 1,
  UNIQUE KEY `uk_question_sort` (`article_id`,`sort_no`),
  INDEX `idx_article` (`article_id`),
  INDEX `idx_user_article` (`user_id`,`article_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- AI English tutor: a SIDE channel. Rows here never touch progress/answer_log,
-- never affect the five-round state machine or the two-level mastery states.
CREATE TABLE IF NOT EXISTS `chat_session` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`     INT NOT NULL,
  `title`       VARCHAR(128) NOT NULL DEFAULT '通用英语',
  `source_type` VARCHAR(16)  NULL,   -- 'article' | 'question' | 'general'
  `source_id`   INT          NULL,   -- article.id or question.id
  `context`     MEDIUMTEXT   NULL,   -- frozen snapshot: article body / question stem etc.
  `turn_count`  INT          NOT NULL DEFAULT 0,
  `created_at`  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `idx_user_updated` (`user_id`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `chat_message` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `session_id` INT NOT NULL,
  `user_id`    INT NOT NULL,
  `role`       ENUM('user','assistant') NOT NULL,
  `content`    MEDIUMTEXT NOT NULL,
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_session` (`session_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
