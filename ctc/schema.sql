-- Combat Training Centre badge requests.
--
-- One row per badge, never per submission and never per level.
--
-- A member asking for three badges creates three rows sharing a group_id,
-- because each is claimed by a different instructor and finishes on a
-- different day. But the levels *within* one badge stay on a single row:
-- Grenadier Basic + Advanced + Expert is one test session with one instructor,
-- so splitting it would mean three tickets that can only ever be worked
-- together.
--
-- `levels`          the ordered set the member needs run, stored as "B,A,E".
-- `levels_achieved` what they actually passed. Usually the same, but a member
--                   can clear Basic and Advanced and fail Expert.
-- `variant`         which form of the badge was run, for badges like Gun Range
--                   that are run as Rifle, Pistol, SMG, Shotgun or HMG.

CREATE TABLE IF NOT EXISTS `ctc_requests` (
  `id`               INTEGER PRIMARY KEY AUTOINCREMENT,
  `group_id`         TEXT    NOT NULL,
  `guild_id`         TEXT    NOT NULL,
  `member_id`        TEXT    NOT NULL,
  `member_name`      TEXT    NOT NULL,
  `badge_key`        TEXT    NOT NULL,
  `levels`           TEXT,
  `levels_achieved`  TEXT,
  `variant`          TEXT,
  `notes`            TEXT,
  `status`           TEXT    NOT NULL DEFAULT 'requested',
  `instructor_id`    TEXT,
  `instructor_name`  TEXT,
  `queue_channel_id` TEXT,
  `queue_message_id` TEXT,
  `thread_id`        TEXT,
  `created_at`       TEXT    NOT NULL DEFAULT (datetime('now')),
  `claimed_at`       TEXT,
  `completed_at`     TEXT,
  `awarded_at`       TEXT,
  `cancelled_at`     TEXT,
  `last_nudged_at`   TEXT,
  `amended_at`       TEXT,
  `amended_by`       TEXT,
  `amended_by_name`  TEXT
);

CREATE INDEX IF NOT EXISTS `idx_ctc_requests_status`  ON `ctc_requests` (`status`);
CREATE INDEX IF NOT EXISTS `idx_ctc_requests_member`  ON `ctc_requests` (`member_id`);
CREATE INDEX IF NOT EXISTS `idx_ctc_requests_group`   ON `ctc_requests` (`group_id`);
CREATE INDEX IF NOT EXISTS `idx_ctc_requests_thread`  ON `ctc_requests` (`thread_id`);
