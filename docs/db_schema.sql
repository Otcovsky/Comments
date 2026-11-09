-- Database schema of the Comments app (application tables only).
-- Matches what the Django migrations create in MySQL. To open in MySQL Workbench:
-- File -> Import -> Reverse Engineer MySQL Create Script... -> select this file.
-- Foreign keys have no ON DELETE action: Django removes dependent rows itself.
-- Captchas live in Redis for minutes, so they have no table.

CREATE SCHEMA IF NOT EXISTS `comments` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `comments`;

CREATE TABLE `comments_author` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(64) NOT NULL,
  `email` VARCHAR(254) NOT NULL,
  `home_page` VARCHAR(200) NOT NULL COMMENT 'From the first comment of the author; empty if not given',
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_author` (`username`, `email`),
  KEY `comments_au_email_56fa4a_idx` (`email`)
) ENGINE=InnoDB;

CREATE TABLE `comments_comment` (
  `id` BIGINT NOT NULL AUTO_INCREMENT,
  `author_id` BIGINT NOT NULL,
  `username` VARCHAR(64) NOT NULL COMMENT 'Copy of comments_author.username, for sorting with an index',
  `email` VARCHAR(254) NOT NULL COMMENT 'Copy of comments_author.email, for sorting with an index',
  `parent_id` BIGINT NULL COMMENT 'Comment this one replies to; NULL for top-level comments',
  `root_id` BIGINT NULL COMMENT 'Top-level comment of the thread; NULL for top-level comments',
  `text` LONGTEXT NOT NULL COMMENT 'Sanitized XHTML: only a, code, i, strong',
  `attachment` VARCHAR(100) NOT NULL COMMENT 'Object key in the bucket; empty if there is no file',
  `attachment_name` VARCHAR(255) NOT NULL COMMENT 'Original file name, cleaned, for display; empty if there is no file',
  `attachment_type` VARCHAR(10) NOT NULL COMMENT '"", image, text',
  `ip_address` CHAR(39) NULL,
  `user_agent` VARCHAR(255) NOT NULL,
  `created_at` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `comments_comment_author_id_334ce9e2_fk_comments_author_id` (`author_id`),
  KEY `comments_comment_created_at_c684ddb5` (`created_at`),
  KEY `comments_top_created_desc` (`parent_id`, `created_at` DESC, `id` DESC),
  KEY `comments_top_created_asc` (`parent_id`, `created_at`, `id` DESC),
  KEY `comments_co_root_id_45b474_idx` (`root_id`, `created_at`),
  KEY `comments_top_username_asc` (`parent_id`, `username`, `id` DESC),
  KEY `comments_top_username_desc` (`parent_id`, `username` DESC, `id` DESC),
  KEY `comments_top_email_asc` (`parent_id`, `email`, `id` DESC),
  KEY `comments_top_email_desc` (`parent_id`, `email` DESC, `id` DESC),
  CONSTRAINT `comments_comment_author_id_334ce9e2_fk_comments_author_id` FOREIGN KEY (`author_id`) REFERENCES `comments_author` (`id`),
  CONSTRAINT `comments_comment_parent_id_3e0802fb_fk_comments_comment_id` FOREIGN KEY (`parent_id`) REFERENCES `comments_comment` (`id`),
  CONSTRAINT `comments_comment_root_id_23b4c218_fk_comments_comment_id` FOREIGN KEY (`root_id`) REFERENCES `comments_comment` (`id`)
) ENGINE=InnoDB;
