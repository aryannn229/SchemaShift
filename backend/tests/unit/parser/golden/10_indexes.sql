CREATE TABLE items (id INT PRIMARY KEY, sku TEXT, price NUMERIC(8,2), deleted BOOLEAN);
CREATE INDEX idx_items_price ON items (price DESC);
CREATE UNIQUE INDEX uq_items_sku ON items (sku) WHERE deleted = false;
CREATE INDEX ON items (sku, price);
