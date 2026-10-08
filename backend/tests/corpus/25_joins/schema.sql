CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id), total INT NOT NULL);
CREATE TABLE notes (id INT PRIMARY KEY, order_id INT REFERENCES orders(id), body TEXT);
