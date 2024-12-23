CREATE TABLE IF NOT EXISTS `taw_accounts` (
  `user_id` varchar(20) NOT NULL,
  `taw_id` varchar(20) NOT NULL,
  `auth_token` varchar(20) NOT NULL,
  'auth_signed' varchar(20) NOT NULL,
);
