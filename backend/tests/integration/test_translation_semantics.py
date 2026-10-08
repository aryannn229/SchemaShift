"""The same SQL on PostgreSQL and the translated pipeline on MongoDB must agree, for every layout."""

from __future__ import annotations

from typing import Any

import pytest

from schemashift.models import PlacementDecision, PlacementPlan
from tests.integration.harness import run_select, setup_and_migrate

pytestmark = pytest.mark.integration

SCHEMA = """
CREATE TABLE customers (id INT PRIMARY KEY, name TEXT NOT NULL, tier TEXT);
CREATE TABLE orders (
  id INT PRIMARY KEY, customer_id INT NOT NULL REFERENCES customers(id),
  total NUMERIC(10,2) NOT NULL, note TEXT, placed TIMESTAMPTZ, status TEXT DEFAULT 'new'
);
CREATE TABLE items (id INT PRIMARY KEY, order_id INT NOT NULL REFERENCES orders(id), sku TEXT NOT NULL, qty INT NOT NULL);
CREATE TABLE profiles (customer_id INT PRIMARY KEY REFERENCES customers(id), bio TEXT);
CREATE TABLE tags (id INT PRIMARY KEY, label TEXT NOT NULL);
CREATE TABLE customer_tags (
  customer_id INT NOT NULL REFERENCES customers(id), tag_id INT NOT NULL REFERENCES tags(id),
  PRIMARY KEY (customer_id, tag_id)
);
"""
DATA = """
INSERT INTO customers VALUES (1,'ann','gold'),(2,'bob',NULL),(3,'cy','gold'),(4,'dee','silver'),(5,'eve',NULL);
INSERT INTO orders (id, customer_id, total, note, placed) VALUES
  (10,1,50.00,'rush','2024-01-15 10:00:00+00'),(11,1,20.50,NULL,'2024-02-20 09:30:00+02'),
  (12,2,5.00,'gift','2024-03-01 00:00:00+00'),(13,3,100.00,NULL,NULL),(14,3,0.00,'x','2024-02-01 00:00:00+00');
INSERT INTO items VALUES (100,10,'a',2),(101,10,'b',1),(102,12,'a',5),(103,13,'c',1);
INSERT INTO profiles VALUES (1,'hello'),(3,NULL);
INSERT INTO tags VALUES (1,'vip'),(2,'new');
INSERT INTO customer_tags VALUES (1,1),(1,2),(3,1);
"""
QUERIES = """
SELECT id, name FROM customers ORDER BY id;
SELECT name FROM customers WHERE tier = 'gold' ORDER BY name;
SELECT name FROM customers WHERE tier <> 'gold' ORDER BY name;
SELECT name FROM customers WHERE NOT (tier = 'gold') ORDER BY name;
SELECT name FROM customers WHERE tier IS NULL ORDER BY name;
SELECT name FROM customers WHERE tier IS NOT NULL AND id > 1 ORDER BY name;
SELECT name FROM customers WHERE tier NOT IN ('gold') ORDER BY name;
SELECT name FROM customers WHERE tier IN ('gold', 'silver') OR id = 2 ORDER BY name;
SELECT name FROM customers WHERE name LIKE '%e%' ORDER BY name;
SELECT name FROM customers WHERE name NOT LIKE '%e%' ORDER BY name;
SELECT c.name, o.total FROM customers c JOIN orders o ON o.customer_id = c.id ORDER BY o.id;
SELECT c.name, o.total FROM customers c LEFT JOIN orders o ON o.customer_id = c.id ORDER BY c.id, o.id;
SELECT c.name, COUNT(*) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY c.name;
SELECT c.name, COUNT(o.id) AS n FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY c.name;
SELECT customer_id, SUM(total) AS s, AVG(total) AS a, MIN(total), MAX(total) FROM orders GROUP BY customer_id ORDER BY customer_id;
SELECT SUM(total) FROM orders WHERE total > 10;
SELECT c.name, SUM(o.total) AS s FROM customers c LEFT JOIN orders o ON o.customer_id = c.id GROUP BY c.name ORDER BY c.name;
SELECT id, note FROM orders ORDER BY note, id;
SELECT id, note FROM orders ORDER BY note DESC, id;
SELECT id, note FROM orders ORDER BY note NULLS FIRST, id;
SELECT DISTINCT tier FROM customers ORDER BY tier;
SELECT id FROM orders ORDER BY total DESC, id LIMIT 2 OFFSET 1;
SELECT o.id, i.sku FROM orders o JOIN items i ON i.order_id = o.id ORDER BY i.id;
SELECT o.id, i.sku FROM orders o LEFT JOIN items i ON i.order_id = o.id ORDER BY o.id, i.id;
SELECT sku, SUM(qty) AS q FROM items GROUP BY sku ORDER BY sku;
SELECT c.name, p.bio FROM customers c LEFT JOIN profiles p ON p.customer_id = c.id ORDER BY c.id;
SELECT c.name, p.bio FROM customers c JOIN profiles p ON p.customer_id = c.id ORDER BY c.id;
SELECT c.name, t.label FROM customers c JOIN customer_tags ct ON ct.customer_id = c.id JOIN tags t ON t.id = ct.tag_id ORDER BY c.id, t.id;
SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id HAVING COUNT(*) > 1 ORDER BY customer_id;
SELECT id FROM orders WHERE placed >= '2024-02-01' ORDER BY id;
SELECT id, total * 2 AS t2 FROM orders WHERE total BETWEEN 5 AND 60 ORDER BY id;
SELECT id FROM orders WHERE id IN (10, 12, 99) ORDER BY id;
SELECT COUNT(*) FROM orders;
SELECT COUNT(DISTINCT customer_id) AS c FROM orders;
SELECT id FROM orders WHERE total > 1 AND (note = 'rush' OR note IS NULL) ORDER BY id;
SELECT o.id, c.name FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.tier = 'gold' ORDER BY o.id;
SELECT sku, qty FROM items WHERE qty > 1 ORDER BY id;
SELECT i.sku, o.status FROM items i JOIN orders o ON i.order_id = o.id ORDER BY i.id;
"""


def folded_customer_tags(plan_obj: PlacementPlan) -> PlacementPlan:
    decisions = dict(plan_obj.decisions)
    decisions["m2n:customer_tags"] = PlacementDecision(
        relationship_id="m2n:customer_tags", decision="REF_ARRAY", host="customers"
    )
    return PlacementPlan(decisions=decisions)


PLANS: dict[str, PlacementPlan] = {
    "all-referenced": PlacementPlan(),
    "items+profiles-embedded": PlacementPlan.of(
        {"fk:items.order_id->orders.id": "EMBED", "fk:profiles.customer_id->customers.id": "EMBED"}
    ),
    "nested": PlacementPlan.of(
        {
            "fk:orders.customer_id->customers.id": "EMBED",
            "fk:items.order_id->orders.id": "EMBED",
            "fk:profiles.customer_id->customers.id": "EMBED",
        }
    ),
    "junction-folded": folded_customer_tags(
        PlacementPlan.of({"fk:orders.customer_id->customers.id": "EMBED"})
    ),
}


@pytest.mark.parametrize("plan_name", list(PLANS))
def test_every_select_matches_postgres(plan_name: str, pg_schema: Any, mongo_db: Any) -> None:
    conn, schema_name = pg_schema
    _, translated = setup_and_migrate(
        SCHEMA, DATA, QUERIES, PLANS[plan_name], conn, schema_name, mongo_db
    )
    failures: list[str] = []
    checked = 0
    for tq in translated:
        if tq.kind != "SELECT":
            continue
        if tq.error:
            failures.append(f"{tq.query_id}: not translated: {tq.error}  [{tq.sql}]")
            continue
        outcome = run_select(conn, mongo_db, tq)
        checked += 1
        if not outcome.ok:
            failures.append(
                f"{tq.query_id} {tq.sql}\n   PG   : {outcome.pg}\n   MONGO: {outcome.mongo}"
            )
    assert checked >= 30
    assert not failures, "\n".join(failures)


def test_known_gap_global_aggregate_over_an_empty_set(pg_schema: Any, mongo_db: Any) -> None:
    """PostgreSQL returns one row (NULL); MongoDB's $group over nothing returns none."""
    conn, schema_name = pg_schema
    _, translated = setup_and_migrate(
        SCHEMA,
        DATA,
        "SELECT SUM(total) FROM orders WHERE total > 1000;",
        PLANS["all-referenced"],
        conn,
        schema_name,
        mongo_db,
    )
    outcome = run_select(conn, mongo_db, translated[0])
    assert outcome.pg == [(None,)] and outcome.mongo == [] and not outcome.ok
