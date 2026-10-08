CREATE TYPE status AS ENUM ('a', 'b', 'c');
CREATE TABLE tickets (
  id INT PRIMARY KEY,
  st status NOT NULL,
  prev status,
  st2 status NOT NULL DEFAULT 'a'
);
