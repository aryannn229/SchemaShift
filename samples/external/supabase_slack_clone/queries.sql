-- Workload for the Supabase Slack clone (hand-written). The schema references auth.users, a table that is not
-- part of the file, so SchemaShift reports it as an unresolved reference.

SELECT m.id AS message_id, m.message AS message, u.username AS author
FROM messages m
JOIN users u ON m.user_id = u.id
WHERE m.channel_id = 1
ORDER BY m.inserted_at DESC, m.id
LIMIT 50;

SELECT c.slug AS channel, COUNT(m.id) AS messages
FROM channels c
LEFT JOIN messages m ON m.channel_id = c.id
GROUP BY c.slug
ORDER BY messages DESC, channel;

SELECT u.username AS username, COUNT(m.id) AS messages
FROM users u
JOIN messages m ON m.user_id = u.id
GROUP BY u.username
HAVING COUNT(m.id) > 5
ORDER BY messages DESC;

SELECT ur.role AS role, COUNT(*) AS members
FROM user_roles ur
GROUP BY ur.role
ORDER BY role;

SELECT rp.role AS role, rp.permission AS permission FROM role_permissions rp WHERE rp.role = 'admin' ORDER BY permission;

SELECT id, username FROM users WHERE status = 'ONLINE' ORDER BY username;

INSERT INTO channels (slug, created_by) VALUES ('random', '00000000-0000-0000-0000-000000000001');
DELETE FROM messages WHERE channel_id = 2;
