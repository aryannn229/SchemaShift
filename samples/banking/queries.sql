SELECT a.id, COUNT(*) AS n, SUM(t.amount) AS total FROM accounts a JOIN transactions t ON t.account_id = a.id GROUP BY a.id;
SELECT c.full_name, k.national_id FROM customers c JOIN kyc_profiles k ON k.customer_id = c.id;
SELECT c.full_name, a.balance FROM customers c LEFT JOIN accounts a ON a.customer_id = c.id;
SELECT SUM(balance) FROM accounts;
BEGIN;
UPDATE accounts SET balance = balance - 100 WHERE id = 1;
UPDATE accounts SET balance = balance + 100 WHERE id = 2;
INSERT INTO transactions (account_id, kind, amount) VALUES (1, 'transfer', 100);
COMMIT;
