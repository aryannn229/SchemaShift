CREATE TABLE parent (id INT);
CREATE TABLE child (id INT, parent_id INT, code TEXT, qty INT);
ALTER TABLE parent ADD PRIMARY KEY (id);
ALTER TABLE child ADD CONSTRAINT child_pk PRIMARY KEY (id);
ALTER TABLE child ADD CONSTRAINT child_parent_fk FOREIGN KEY (parent_id) REFERENCES parent(id) ON DELETE CASCADE;
ALTER TABLE child ADD CONSTRAINT child_code_uq UNIQUE (code), ADD CONSTRAINT child_qty_ck CHECK (qty > 0);
