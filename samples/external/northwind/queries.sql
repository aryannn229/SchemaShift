-- Workload for Northwind (hand-written, within the supported SQL subset).

-- Orders and customers
SELECT o.order_id AS order_id, c.company_name AS customer, o.order_date AS ordered
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
WHERE o.ship_country = 'Germany'
ORDER BY o.order_date, o.order_id
LIMIT 25;

SELECT c.country AS country, COUNT(*) AS orders
FROM orders o
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.country
ORDER BY orders DESC, country;

SELECT c.company_name AS customer, COUNT(o.order_id) AS orders
FROM customers c
LEFT JOIN orders o ON o.customer_id = c.customer_id
GROUP BY c.company_name
HAVING COUNT(o.order_id) = 0
ORDER BY customer;

-- Order lines
SELECT od.order_id AS order_id, SUM(od.unit_price * od.quantity) AS gross, COUNT(*) AS lines
FROM order_details od
GROUP BY od.order_id
ORDER BY gross DESC
LIMIT 20;

SELECT p.product_name AS product, SUM(od.quantity) AS units
FROM order_details od
JOIN products p ON od.product_id = p.product_id
GROUP BY p.product_name
ORDER BY units DESC
LIMIT 10;

SELECT cat.category_name AS category, COUNT(*) AS products, AVG(p.unit_price) AS avg_price
FROM products p
JOIN categories cat ON p.category_id = cat.category_id
GROUP BY cat.category_name
ORDER BY products DESC;

SELECT s.company_name AS supplier, COUNT(p.product_id) AS products
FROM suppliers s
LEFT JOIN products p ON p.supplier_id = s.supplier_id
GROUP BY s.company_name
ORDER BY products DESC, supplier;

SELECT product_id, product_name, units_in_stock FROM products WHERE discontinued = 0 AND units_in_stock < 10 ORDER BY units_in_stock, product_id;

SELECT product_id, product_name, unit_price FROM products WHERE unit_price BETWEEN 10 AND 30 AND supplier_id IS NOT NULL ORDER BY unit_price DESC, product_id;

-- Employees and territories
SELECT e.last_name AS employee, COUNT(o.order_id) AS orders
FROM employees e
LEFT JOIN orders o ON o.employee_id = e.employee_id
GROUP BY e.last_name
ORDER BY orders DESC;

SELECT et.employee_id AS employee_id, COUNT(*) AS territories
FROM employee_territories et
GROUP BY et.employee_id
HAVING COUNT(*) > 2
ORDER BY employee_id;

SELECT COUNT(*) AS orders, SUM(freight) AS freight, MAX(freight) AS most FROM orders WHERE shipped_date IS NOT NULL;

-- Writes
INSERT INTO shippers (shipper_id, company_name, phone) VALUES (99, 'Express Co', '555-0100');
UPDATE products SET discontinued = 1 WHERE units_in_stock = 0;
DELETE FROM order_details WHERE quantity = 0;
