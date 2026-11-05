-- Database schema of the Comments app (application tables only).
-- Matches what the Django migrations create in MySQL. To open in MySQL Workbench:
-- File -> Import -> Reverse Engineer MySQL Create Script... -> select this file.

CREATE SCHEMA IF NOT EXISTS `comments` DEFAULT CHARACTER SET utf8mb4;
USE `comments`;

CREATE TABLE `comments_author` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(64) NOT NULL,
  `email` VARCHAR(254) NOT NULL,
  `home_page` VARCHAR(200) NOT NULL DEFAULT '',
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_author` (`username`, `email`),
  KEY `comments_au_email_idx` (`email`)
) ENGINE=InnoDB;

CREATE TABLE `comments_comment` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `author_id` BIGINT NOT NULL,
  `parent_id` BIGINT NULL COMMENT 'Comment this one replies to; NULL for top-level comments',
  `root_id` BIGINT NULL COMMENT 'Top-level comment of the thread; NULL for top-level comments',
  `text` LONGTEXT NOT NULL COMMENT 'Sanitized XHTML: only a, code, i, strong',
  `attachment` VARCHAR(100) NOT NULL DEFAULT '',
  `attachment_type` VARCHAR(10) NOT NULL DEFAULT '' COMMENT '"", image, text',
  `attachment_status` VARCHAR(10) NOT NULL DEFAULT 'ready' COMMENT 'ready, pending (image waits for the worker), failed',
  `ip_address` CHAR(39) NULL,
  `user_agent` VARCHAR(255) NOT NULL DEFAULT '',
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `comments_comment_created_at_idx` (`created_at`),
  KEY `comments_co_parent_created_idx` (`parent_id`, `created_at` DESC),
  KEY `comments_co_root_created_idx` (`root_id`, `created_at`),
  KEY `comments_co_status_created_idx` (`attachment_status`, `created_at`),
  CONSTRAINT `fk_comment_author` FOREIGN KEY (`author_id`) REFERENCES `comments_author` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_comment_parent` FOREIGN KEY (`parent_id`) REFERENCES `comments_comment` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_comment_root` FOREIGN KEY (`root_id`) REFERENCES `comments_comment` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE `comments_captcha` (
  `key` CHAR(32) NOT NULL,
  `answer` VARCHAR(16) NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`key`),
  KEY `comments_captcha_created_at_idx` (`created_at`)
) ENGINE=InnoDB;
