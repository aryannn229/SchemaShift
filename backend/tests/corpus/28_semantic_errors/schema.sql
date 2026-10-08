CREATE TABLE a (id INT PRIMARY KEY, x TEXT);
CREATE TABLE b (
  id INT PRIMARY KEY,
  ghost_id INT REFERENCES ghost(id),
  a_missing INT REFERENCES a(nope),
  a_txt TEXT REFERENCES a(id),
  a_x TEXT REFERENCES a(x)
);
CREATE TABLE nopk (v INT);
