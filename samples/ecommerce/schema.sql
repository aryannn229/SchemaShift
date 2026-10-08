CREATE TYPE order_status AS ENUM ('pending', 'paid', 'shipped', 'cancelled');

CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  phone TEXT UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE addresses (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  line1 TEXT NOT NULL,
  city TEXT NOT NULL,
  postal_code VARCHAR(10)
);
CREATE TABLE categories (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  parent_id INT REFERENCES categories(id) ON DELETE SET NULL
);
CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  sku VARCHAR(20) NOT NULL UNIQUE,
  title TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL CONSTRAINT ck_price CHECK (price >= 0),
  stock INT NOT NULL DEFAULT 0 CONSTRAINT ck_stock CHECK (stock >= 0)
);
CREATE TABLE product_categories (
  product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  category_id INT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  PRIMARY KEY (product_id, category_id)
);
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id),
  status order_status NOT NULL DEFAULT 'pending',
  placed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  total NUMERIC(12,2) NOT NULL
);
CREATE TABLE order_items (
  id SERIAL PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  product_id INT NOT NULL REFERENCES products(id),
  quantity INT NOT NULL CONSTRAINT ck_qty CHECK (quantity > 0),
  unit_price NUMERIC(10,2) NOT NULL,
  UNIQUE (order_id, product_id)
);
CREATE TABLE payments (
  id SERIAL PRIMARY KEY,
  order_id INT NOT NULL UNIQUE REFERENCES orders(id),
  amount NUMERIC(12,2) NOT NULL,
  paid_at TIMESTAMPTZ
);
CREATE TABLE reviews (
  id SERIAL PRIMARY KEY,
  product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  stars INT NOT NULL CONSTRAINT ck_stars CHECK (stars BETWEEN 1 AND 5),
  body TEXT
);
