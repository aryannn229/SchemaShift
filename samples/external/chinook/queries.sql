-- Workload for the Chinook music store (hand-written, within the supported SQL subset).

-- Catalogue browsing
SELECT t.name AS track, a.title AS album, ar.name AS artist
FROM track t
JOIN album a ON t.album_id = a.album_id
JOIN artist ar ON a.artist_id = ar.artist_id
WHERE t.unit_price > 0.99
ORDER BY ar.name, a.title, t.name
LIMIT 50;

SELECT g.name AS genre, COUNT(*) AS tracks
FROM track t
JOIN genre g ON t.genre_id = g.genre_id
GROUP BY g.name
ORDER BY tracks DESC;

SELECT a.title AS album, COUNT(t.track_id) AS tracks, SUM(t.milliseconds) AS total_ms
FROM album a
LEFT JOIN track t ON t.album_id = a.album_id
GROUP BY a.title
ORDER BY total_ms DESC
LIMIT 20;

SELECT ar.name AS artist, COUNT(a.album_id) AS albums
FROM artist ar
LEFT JOIN album a ON a.artist_id = ar.artist_id
GROUP BY ar.name
HAVING COUNT(a.album_id) > 1
ORDER BY albums DESC;

-- Sales
SELECT c.country AS country, SUM(i.total) AS revenue, COUNT(*) AS invoices
FROM invoice i
JOIN customer c ON i.customer_id = c.customer_id
GROUP BY c.country
ORDER BY revenue DESC;

SELECT c.last_name AS last_name, c.first_name AS first_name, SUM(i.total) AS spent
FROM customer c
JOIN invoice i ON i.customer_id = c.customer_id
WHERE i.invoice_date >= '2010-01-01'
GROUP BY c.last_name, c.first_name
ORDER BY spent DESC
LIMIT 10;

SELECT i.invoice_id AS invoice_id, COUNT(*) AS lines, SUM(il.unit_price * il.quantity) AS amount
FROM invoice i
JOIN invoice_line il ON il.invoice_id = i.invoice_id
GROUP BY i.invoice_id
HAVING SUM(il.quantity) > 3
ORDER BY amount DESC;

SELECT t.name AS track, SUM(il.quantity) AS sold
FROM invoice_line il
JOIN track t ON il.track_id = t.track_id
GROUP BY t.name
ORDER BY sold DESC
LIMIT 10;

SELECT e.last_name AS rep, COUNT(c.customer_id) AS customers
FROM employee e
LEFT JOIN customer c ON c.support_rep_id = e.employee_id
GROUP BY e.last_name
ORDER BY customers DESC;

-- Customers and filters
SELECT customer_id, first_name, last_name, email FROM customer WHERE country = 'USA' AND company IS NOT NULL ORDER BY last_name;

SELECT customer_id, email FROM customer WHERE country IN ('Brazil', 'Canada', 'France') ORDER BY customer_id;

SELECT COUNT(*) AS invoices, AVG(total) AS avg_total, MIN(total) AS lowest, MAX(total) AS highest FROM invoice WHERE total BETWEEN 1 AND 20;

-- Playlists (many-to-many through playlist_track)
SELECT p.name AS playlist, COUNT(pt.track_id) AS tracks
FROM playlist p
LEFT JOIN playlist_track pt ON pt.playlist_id = p.playlist_id
GROUP BY p.name
ORDER BY tracks DESC;

-- Writes
INSERT INTO genre (genre_id, name) VALUES (100, 'Soundtrack');
UPDATE track SET unit_price = 1.29 WHERE genre_id = 100;
DELETE FROM playlist_track WHERE playlist_id = 1 AND track_id = 1;
