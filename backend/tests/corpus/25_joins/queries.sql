SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id;
SELECT o.total, n.body FROM orders o JOIN notes n ON n.order_id = o.id;
SELECT c.name, o.total FROM customers c LEFT JOIN orders o ON o.customer_id = c.id;
SELECT c.name FROM customers c LEFT JOIN orders o ON o.customer_id = c.id;
