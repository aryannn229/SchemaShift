CREATE TYPE account_type AS ENUM ('checking', 'savings', 'business');
CREATE TABLE accounts (
  id SERIAL PRIMARY KEY,
  kind account_type NOT NULL DEFAULT 'checking'
);
