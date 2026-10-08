SELECT c.name, COUNT(*) AS n, SUM(o.total) AS spent FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY spent DESC LIMIT 10;
SELECT p.title, AVG(r.stars) AS avg_stars FROM products p LEFT JOIN reviews r ON r.product_id = p.id GROUP BY p.title;
SELECT o.id, oi.quantity FROM orders o JOIN order_items oi ON oi.order_id = o.id WHERE o.status = 'paid';
BEGIN;
INSERT INTO orders (customer_id, total) VALUES (1, 10);
UPDATE products SET stock = stock - 1 WHERE id = 1;
COMMIT;
