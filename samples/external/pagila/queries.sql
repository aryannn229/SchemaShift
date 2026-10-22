-- Workload for Pagila (hand-written). Only the core tables are queried; the schema itself also contains
-- views, functions, triggers and partitioning that SchemaShift reports as unsupported.

SELECT f.title AS title, l.name AS language, f.rental_rate AS rate
FROM film f
JOIN language l ON f.language_id = l.language_id
WHERE f.rating = 'PG' AND f.length > 100
ORDER BY f.title
LIMIT 25;

SELECT c.name AS category, COUNT(*) AS films
FROM film_category fc
JOIN category c ON fc.category_id = c.category_id
GROUP BY c.name
ORDER BY films DESC, category;

SELECT a.last_name AS last_name, a.first_name AS first_name, COUNT(fa.film_id) AS films
FROM actor a
LEFT JOIN film_actor fa ON fa.actor_id = a.actor_id
GROUP BY a.last_name, a.first_name
ORDER BY films DESC
LIMIT 20;

SELECT co.country AS country, COUNT(*) AS customers
FROM customer cu
JOIN address ad ON cu.address_id = ad.address_id
JOIN city ci ON ad.city_id = ci.city_id
JOIN country co ON ci.country_id = co.country_id
GROUP BY co.country
ORDER BY customers DESC;

SELECT s.store_id AS store_id, COUNT(i.inventory_id) AS copies
FROM store s
LEFT JOIN inventory i ON i.store_id = s.store_id
GROUP BY s.store_id
ORDER BY store_id;

SELECT f.rating AS rating, AVG(f.rental_rate) AS avg_rate, MIN(f.length) AS shortest, MAX(f.length) AS longest
FROM film f
GROUP BY f.rating
ORDER BY rating;

SELECT cu.customer_id AS customer_id, COUNT(r.rental_id) AS rentals
FROM customer cu
LEFT JOIN rental r ON r.customer_id = cu.customer_id
GROUP BY cu.customer_id
HAVING COUNT(r.rental_id) > 30
ORDER BY rentals DESC, customer_id;

SELECT customer_id, first_name, last_name FROM customer WHERE active = 1 AND store_id IN (1, 2) ORDER BY last_name, customer_id;

INSERT INTO language (language_id, name) VALUES (50, 'Klingon');
UPDATE film SET rental_rate = 3.99 WHERE rating = 'NC-17';
DELETE FROM film_actor WHERE actor_id = 1;
