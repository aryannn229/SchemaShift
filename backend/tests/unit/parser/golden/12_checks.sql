CREATE TABLE products (
  id INT PRIMARY KEY,
  price NUMERIC(8,2) CHECK (price >= 0),
  status TEXT CHECK (status IN ('new', 'used')),
  lo INT, hi INT,
  name VARCHAR(20) CONSTRAINT name_len CHECK (LENGTH(name) > 2),
  CONSTRAINT lo_hi CHECK (lo <= hi)
);
