SELECT p.title, u.username FROM posts p JOIN users u ON p.author_id = u.id WHERE p.status = 'published' ORDER BY p.id DESC LIMIT 10;
SELECT p.title, COUNT(*) AS comment_count FROM posts p LEFT JOIN comments c ON c.post_id = p.id GROUP BY p.title;
SELECT t.name, COUNT(*) AS uses FROM tags t JOIN post_tags pt ON pt.tag_id = t.id GROUP BY t.name ORDER BY uses DESC;
INSERT INTO comments (post_id, author_id, body) VALUES (1, 1, 'Nice post');
