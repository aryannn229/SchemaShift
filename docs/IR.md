# Intermediate Representation

`schemashift.ir.build_ir(graph, queries) -> IRProgram` lowers an analysed schema to a flat list of
nodes. Entity nodes describe *what exists*; guarantee nodes describe *what must stay true*.
Every node has `id` (stable, unique), `origin` (SQL construct), `source_span`, and a `node_type`
discriminator. Programs round-trip through JSON. `print_ir()` renders the text dump shown in the UI.

## Entity nodes
| Node | Fields | id |
|---|---|---|
| `EntityNode` | table | `entity:<table>` |
| `AttributeNode` | table, column, type | `attr:<table>.<col>` |
| `RelationshipNode` | parent, child, cardinality (`1:1`/`1:N`/`M:N`), fk_ref, self_reference, junction | `rel:<child>.<cols>-><parent>.<cols>` or `rel:m2n:<a>-<b>-via-<junction>` |

## Guarantee nodes
| Node | Produced by | Meaning | id |
|---|---|---|---|
| `ReferentialIntegrity(child, parent, columns, ref_columns, fk_ref)` | every FK | child cannot reference a missing parent | `fk:<child>.<cols>-><parent>.<cols>` |
| `CascadingDelete(parent, child, fk_ref)` | `ON DELETE CASCADE` | deleting a parent deletes children | `cascade_delete:...` |
| `CascadingUpdate(parent, child, fk_ref, parent_columns)` | `ON UPDATE CASCADE` | key change propagates | `cascade_update:...` |
| `SetNullOnDelete(parent, child, columns, fk_ref)` | `ON DELETE SET NULL` | child FK nulled | `set_null:...` |
| `SetDefaultOnDelete(...)` | `ON DELETE SET DEFAULT` | child FK defaulted | `set_default:...` |
| `RestrictDelete(parent, child, fk_ref, action)` | `RESTRICT` / `NO ACTION` (the default) | parent with children cannot be deleted | `restrict_delete:...` |
| `EntityUniqueness(table, columns)` | PRIMARY KEY | rows uniquely identifiable | `pk:<table>.<cols>` |
| `ValueUniqueness(table, columns, nullable, partial_where)` | UNIQUE, unique index | no duplicate values | `unique:<table>.<cols>` |
| `CrossEntityUniqueness(tables, columns, junction)` | key of a junction table | each combination of two related entities appears once | `cross_unique:<junction>.<cols>` |
| `NotNullGuarantee(table, column)` | NOT NULL, PK columns | value always present | `not_null:<table>.<col>` |
| `DomainConstraint(table, expr_sql, name, columns)` | CHECK | value satisfies predicate | `check:<table>.<name or slug>` |
| `TypeGuarantee(table, column, type)` | every column | exact type/precision | `type:<table>.<col>` |
| `DefaultValue(table, column, expr_sql, default_kind)` | DEFAULT | server fills missing value | `default:<table>.<col>` |
| `AutoIncrement(table, column)` | SERIAL, IDENTITY | server-generated increasing id | `auto_increment:<table>.<col>` |
| `EnumDomain(table, column, enum_name, values)` | ENUM column | value in a fixed set | `enum:<table>.<col>` |
| `MultiEntityAtomicity(tables, txn_id)` | BEGIN..COMMIT touching >= 2 tables | all-or-nothing across tables | `txn:<txn_id>` |
| `JoinSemantics(query_id, kind, tables, on_sql, fk_ref, right_columns_projected)` | each JOIN | join result semantics (NULL handling for LEFT) | `join:<query>#<n>` |
| `AggregateSemantics(query_id, tables, group_by, aggregates, has_having)` | GROUP BY / aggregates | grouping semantics | `aggregate:<query>` |

Repeated ids (e.g. two unique indexes on the same columns) get a `#2`, `#3` suffix.

## Notes
- Foreign keys whose parent table is missing (SEM001) are not lowered.
- `CrossEntityUniqueness` is emitted only for junction tables; whether it is SAFE or BROKEN
  depends on placement (Phase 4): see DECISIONS.md.
