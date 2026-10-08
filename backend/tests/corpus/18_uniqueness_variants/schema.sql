CREATE TABLE accounts (
  id INT PRIMARY KEY,
  email TEXT UNIQUE,
  ssn TEXT NOT NULL UNIQUE,
  a INT NOT NULL,
  b INT NOT NULL,
  c INT,
  active BOOLEAN NOT NULL,
  code TEXT NOT NULL,
  CONSTRAINT uq_ab UNIQUE (a, b),
  CONSTRAINT uq_ac UNIQUE (a, c)
);
CREATE UNIQUE INDEX ix_code_active ON accounts (code) WHERE active = true;
CREATE UNIQUE INDEX ix_code_lower ON accounts (code) WHERE lower(code) = 'x';
CREATE UNIQUE INDEX ix_c ON accounts (c);
