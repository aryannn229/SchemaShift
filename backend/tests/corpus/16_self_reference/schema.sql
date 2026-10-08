CREATE TABLE employees (
  id INT PRIMARY KEY,
  name TEXT NOT NULL,
  manager_id INT REFERENCES employees(id) ON DELETE SET NULL
);
