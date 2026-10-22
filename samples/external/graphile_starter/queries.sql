-- Workload for the Graphile Starter schema (hand-written). Most of the file is functions, triggers and
-- policies, which SchemaShift reports as unsupported; this workload covers the plain tables.

SELECT u.id AS user_id, u.username AS username, ue.email AS email
FROM app_public.users u
JOIN app_public.user_emails ue ON ue.user_id = u.id
WHERE ue.is_verified = true
ORDER BY u.username;

SELECT o.name AS organization, COUNT(om.id) AS members
FROM app_public.organizations o
LEFT JOIN app_public.organization_memberships om ON om.organization_id = o.id
GROUP BY o.name
ORDER BY members DESC;

SELECT u.username AS username, COUNT(ua.id) AS authentications
FROM app_public.users u
LEFT JOIN app_public.user_authentications ua ON ua.user_id = u.id
GROUP BY u.username
HAVING COUNT(ua.id) > 0;
