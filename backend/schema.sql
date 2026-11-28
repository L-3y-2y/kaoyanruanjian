-- kaoyanruanjian full schema (stage 1-5)
-- Every statement is idempotent and runs automatically on backend startup.
-- Line comments start with -- ; keep semicolons only at the end of a statement.
-- NEVER drop or truncate a table here: user data must survive every restart.

CREATE TABLE IF NOT EXISTS `user` (
  `id`              INT AUTO_INCREMENT PRIMARY KEY,
  `username`        VARCHAR(64)  NOT NULL,
  `password_hash`   VARCHAR(255) NOT NULL DEFAULT '',
  `current_book_id` INT          NOT NULL DEFAULT 1,
  `created_at`      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `user` (`id`, `username`, `password_hash`) VALUES (1, 'local', '');

-- Multi-book vocabulary shelves. A word may belong to several books (ECDICT
-- `tag` already carries multiple exam tags), so books are a filter over the
-- shared word table, not a copy of the words. Progress is isolated per book.
CREATE TABLE IF NOT EXISTS `book` (
  `id`          INT AUTO_INCREMENT PRIMARY KEY,
  `code`        VARCHAR(32)  NOT NULL,
  `name`        VARCHAR(64)  NOT NULL,
  `exam_type`   VARCHAR(64)  NOT NULL,
  `tag_filter`  VARCHAR(32)  NOT NULL,
  `description` VARCHAR(255) NULL,
  `sort_no`     INT          NOT NULL DEFAULT 0,
  `enabled`     TINYINT      NOT NULL DEFAULT 1,
  `created_at`  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_book_code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `book`
  (`id`, `code`, `name`, `exam_type`, `tag_filter`, `description`, `sort_no`) VALUES
  (1, 'ky',   '考研英语', '考研英语',     'ky',   '考研大纲词汇 · 真题语境', 1),
  (2, 'cet4', '四级词汇', '大学英语四级', 'cet4', '四级高频词 · 基础语境', 2),
  (3, 'cet6', '六级词汇', '大学英语六级', 'cet6', '六级核心词 · 进阶语境', 3),
  (4, 'gk',   '高考词汇', '高考英语',     'gk',   '高考大纲词 · 基础语境', 4);

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
  `book_id`        INT  NOT NULL DEFAULT 1,
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
  UNIQUE KEY `uk_user_book_date` (`user_id`, `book_id`, `study_date`),
  INDEX `idx_study_date` (`study_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `progress` (
  `id`            INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`       INT NOT NULL,
  `book_id`       INT NOT NULL DEFAULT 1,
  `word_id`       INT NOT NULL,
  `daily_plan_id` INT NOT NULL,
  `cycle_no`      INT NOT NULL,
  `round`         TINYINT NOT NULL,
  `status`        ENUM('pending','done') NOT NULL DEFAULT 'pending',
  `stage`         ENUM('none','memorized','mastered') NOT NULL DEFAULT 'none',
  `wrong_count`   INT NOT NULL DEFAULT 0,
  `cloze_wrong`   TINYINT NOT NULL DEFAULT 0,
  `review_stage`  TINYINT NOT NULL DEFAULT 0,
  `review_round`  TINYINT NOT NULL DEFAULT 0,
  `last_review_at` DATETIME NULL,
  `next_due_at`   DATE NULL,
  `updated_at`    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_user_book_word_round` (`user_id`, `book_id`, `word_id`, `round`),
  INDEX `idx_fetch` (`user_id`, `book_id`, `daily_plan_id`, `status`, `cycle_no`, `round`, `id`),
  INDEX `idx_plan_round` (`daily_plan_id`, `round`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `answer_log` (
  `id`              INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`         INT NOT NULL,
  `book_id`         INT NOT NULL DEFAULT 1,
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
  `book_id`       INT NOT NULL DEFAULT 1,
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
  UNIQUE KEY `uk_weekly_article` (`user_id`,`kind`,`week_key`,`seq`,`book_id`),
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
  `book_id`     INT NOT NULL DEFAULT 1,
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

-- Every sentence ever generated for a word, kept only so the next quiz can
-- avoid repeating it. `seq` grows; never writes progress.
CREATE TABLE IF NOT EXISTS `word_example` (
  `id`         INT AUTO_INCREMENT PRIMARY KEY,
  `word_id`    INT NOT NULL,
  `book_id`    INT NOT NULL DEFAULT 1,
  `seq`        TINYINT      NOT NULL DEFAULT 1,
  `sentence`   VARCHAR(512) NOT NULL,
  `translation` VARCHAR(512) NULL,
  `source`     VARCHAR(32)  NOT NULL DEFAULT 'llm',
  `created_at` TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_example_word_book_seq` (`word_id`,`book_id`,`seq`),
  INDEX `idx_example_word` (`word_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Article context quiz: fill-in-the-word questions generated from a daily
-- article PLUS fresh sentences for today's words the article did not cover.
-- Article context quiz: sentences are freshly written for each quiz (never the
-- article's own sentence) and masked before they leave the server. Wrong answers
-- only set progress.cloze_wrong so the next-day review prioritises the word.
CREATE TABLE IF NOT EXISTS `article_cloze` (
  `id`           INT AUTO_INCREMENT PRIMARY KEY,
  `user_id`      INT NOT NULL,
  `book_id`      INT NOT NULL DEFAULT 1,
  `article_id`   INT NOT NULL,
  `word_id`      INT NOT NULL,
  `sentence`     VARCHAR(512) NOT NULL,
  `translation`  VARCHAR(512) NULL,
  `answer`       VARCHAR(128) NOT NULL,
  `source`       ENUM('article','extra') NOT NULL DEFAULT 'article',
  `status`       ENUM('pending','passed') NOT NULL DEFAULT 'pending',
  `attempts`     INT NOT NULL DEFAULT 0,
  `created_at`   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_article_cloze` (`article_id`,`word_id`,`source`),
  INDEX `idx_article_cloze_user` (`user_id`,`book_id`,`article_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Study-time dashboard: one row per (user, day), seconds accumulated via the
-- frontend heartbeat. Never touched by progress/answer_log/chat.
CREATE TABLE IF NOT EXISTS `study_time` (
  `user_id`    INT  NOT NULL,
  `study_date` DATE NOT NULL,
  `seconds`    INT  NOT NULL DEFAULT 0,
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`, `study_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
