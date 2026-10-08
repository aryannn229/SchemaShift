CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (id INT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE reviews (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id),
  product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  stars INT NOT NULL,
  CONSTRAINT ck_stars CHECK (stars BETWEEN 1 AND 5)
);
