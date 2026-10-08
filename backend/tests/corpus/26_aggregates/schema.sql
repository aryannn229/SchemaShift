CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (
  id INT PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id),
  qty INT NOT NULL,
  price DOUBLE PRECISION NOT NULL,
  amount NUMERIC(10,0) NOT NULL,
  note TEXT
);
