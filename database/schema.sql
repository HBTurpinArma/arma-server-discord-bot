CREATE TABLE IF NOT EXISTS `server_status` (
  `guild_id` varchar(20) NOT NULL,
  `channel_id` varchar(20) NOT NULL,
  `message_id` varchar(20) NOT NULL,
  'user_id' varchar(20) NOT NULL,
  `server_id` varchar(20) NOT NULL,
  `server_type` varchar(20) NOT NULL,
  `server_ip` varchar(255) NOT NULL,
  `server_port` varchar(255) NOT NULL,
  `server_name` varchar(255) NOT NULL,
  `server_desc` varchar(255) NOT NULL,
  `server_modpack` varchar(255) NOT NULL
);

CREATE TABLE IF NOT EXISTS `server_authentication` (
  `user_id` varchar(20) NOT NULL,
  `guild_id` varchar(20) NOT NULL,
  `server_type` varchar(20) NOT NULL,
  `server_username` varchar(255) NOT NULL,
  `server_password` varchar(255) NOT NULL
);