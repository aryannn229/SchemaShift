CREATE TYPE account_type AS ENUM ('checking', 'savings', 'business');
CREATE TYPE txn_kind AS ENUM ('deposit', 'withdrawal', 'transfer');

CREATE TABLE branches (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  city TEXT NOT NULL
);
CREATE TABLE customers (
  id SERIAL PRIMARY KEY,
  full_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  phone TEXT UNIQUE,
  home_branch_id INT REFERENCES branches(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE kyc_profiles (
  customer_id INT PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE,
  national_id TEXT NOT NULL UNIQUE,
  verified BOOLEAN NOT NULL DEFAULT FALSE,
  verified_at TIMESTAMPTZ
);
CREATE TABLE accounts (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id),
  branch_id INT NOT NULL REFERENCES branches(id),
  kind account_type NOT NULL,
  balance NUMERIC(14,2) NOT NULL DEFAULT 0 CONSTRAINT ck_balance CHECK (balance >= 0),
  opened_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE transactions (
  id BIGSERIAL PRIMARY KEY,
  account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  kind txn_kind NOT NULL,
  amount NUMERIC(14,2) NOT NULL CONSTRAINT ck_amount CHECK (amount > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  memo TEXT
);
CREATE TABLE cards (
  id SERIAL PRIMARY KEY,
  account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  number CHAR(16) NOT NULL UNIQUE,
  expires DATE NOT NULL
);
CREATE TABLE loans (
  id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
  principal NUMERIC(14,2) NOT NULL,
  rate DOUBLE PRECISION NOT NULL CONSTRAINT ck_rate CHECK (rate BETWEEN 0 AND 1),
  term_months INT NOT NULL
);
CREATE TABLE beneficiaries (
  customer_id INT NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  account_id INT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  nickname TEXT,
  PRIMARY KEY (customer_id, account_id)
);
