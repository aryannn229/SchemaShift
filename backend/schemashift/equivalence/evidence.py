"""Evidence per rule outcome, as data: level, the argument behind it, and structured conditions.

This table is the single place that decides how a verdict is justified. ``PROVEN`` entries must state
the argument; ``CONDITIONAL`` entries must list at least one condition; ``UNVERIFIED`` entries carry
neither (a verification run can upgrade them to ``TESTED``). A test checks that every outcome of every
registered rule has an entry and that no entry is orphaned.

PROVEN here means "established by an argument stated in the entry", not "formally verified": the
arguments are platform facts (MongoDB has no cascade, single-document writes are atomic, ...) and
structural facts about the chosen layout (an embedded child cannot be orphaned). Constraint
equivalence of validators is left ``CONDITIONAL``/``UNVERIFIED`` until the SMT phase can prove it.
"""

from __future__ import annotations

from dataclasses import dataclass

from schemashift.models.evidence import EvidenceCondition, EvidenceLevel


@dataclass(frozen=True)
class EvidenceSpec:
    level: EvidenceLevel
    argument: str = ""
    conditions: tuple[EvidenceCondition, ...] = ()


def _c(kind: str, text: str, **parameters: str | int | float | bool) -> EvidenceCondition:
    return EvidenceCondition(kind=kind, text=text, parameters=dict(parameters))


VALIDATORS = _c(
    "validators_installed",
    "the generated $jsonSchema validators are installed (validationLevel strict, validationAction "
    "error) and no writer uses bypassDocumentValidation",
)
INDEXES = _c(
    "indexes_created", "the generated indexes (02_indexes) are created before the first write"
)
HELPERS = _c(
    "app_enforcement",
    "every write goes through the generated enforcement helpers (helpers.py), never around them",
)
TRANSACTIONS = _c(
    "replica_set",
    "MongoDB runs as a replica set (or Atlas) so multi-document transactions are available, and the "
    "application retries TransientTransactionError and UnknownTransactionCommitResult",
)
IMMUTABLE_KEYS = _c(
    "immutable_keys", "the application never updates the parent key (the _id is immutable)"
)

PLATFORM = "MongoDB has no feature that provides this guarantee natively"

EVIDENCE: dict[tuple[str, str], EvidenceSpec] = {
    # ------------------------------------------------------------- aggregates and joins (query level)
    ("EQ-AGG", "AGG_SAFE"): EvidenceSpec("UNVERIFIED"),
    ("EQ-AGG", "AGG_CHANGED"): EvidenceSpec(
        "PROVEN",
        "PostgreSQL returns NUMERIC for AVG over integers and NULL for SUM of an empty set; the generated "
        "pipeline returns a double, and NULL is only emulated: a documented representation difference",
    ),
    ("EQ-JOIN", "INNER_EMBEDDED"): EvidenceSpec("UNVERIFIED"),
    ("EQ-JOIN", "INNER_REFERENCED"): EvidenceSpec("UNVERIFIED"),
    ("EQ-JOIN", "LEFT_CLEAN"): EvidenceSpec("UNVERIFIED"),
    ("EQ-JOIN", "LEFT_NULLS"): EvidenceSpec(
        "PROVEN",
        "an unmatched LEFT JOIN yields NULL columns in SQL and absent fields in MongoDB; the pipeline "
        "re-creates NULL only for projected columns",
    ),
    # ---------------------------------------------------------------------------- atomicity
    ("EQ-ATOMIC", "SINGLE_DOCUMENT"): EvidenceSpec(
        "PROVEN", "MongoDB guarantees that a write to a single document is atomic"
    ),
    ("EQ-ATOMIC", "TRANSACTION"): EvidenceSpec("CONDITIONAL", conditions=(TRANSACTIONS,)),
    ("EQ-ATOMIC", "NO_TRANSACTION"): EvidenceSpec(
        "PROVEN",
        "without multi-document transactions MongoDB cannot make writes to several documents atomic",
    ),
    # ----------------------------------------------------------------------- generated values
    ("EQ-AUTOINC", "OBJECTID"): EvidenceSpec(
        "PROVEN",
        "ObjectIds are unique but not sequential or gap-free, unlike SERIAL/IDENTITY values",
    ),
    ("EQ-AUTOINC", "COUNTER"): EvidenceSpec(
        "PROVEN",
        "a counter collection updated with findOneAndUpdate/$inc is atomic per call but can leave gaps when "
        "the surrounding write fails, and orders by commit, not by transaction start",
    ),
    ("EQ-DEFAULT", "CONSTANT"): EvidenceSpec(
        "PROVEN", "MongoDB has no server-side column defaults"
    ),
    ("EQ-DEFAULT", "NOW"): EvidenceSpec("PROVEN", "MongoDB has no server-side column defaults"),
    ("EQ-DEFAULT", "EXPRESSION"): EvidenceSpec(
        "PROVEN", "MongoDB has no server-side column defaults"
    ),
    # ------------------------------------------------------------------------------ validators
    ("EQ-NOT-NULL", "VALIDATOR"): EvidenceSpec("CONDITIONAL", conditions=(VALIDATORS,)),
    ("EQ-ENUM", "ENUM_LIST"): EvidenceSpec("CONDITIONAL", conditions=(VALIDATORS,)),
    ("EQ-CHECK", "SAFE_JSONSCHEMA"): EvidenceSpec("CONDITIONAL", conditions=(VALIDATORS,)),
    ("EQ-CHECK", "SAFE_EXPR"): EvidenceSpec("CONDITIONAL", conditions=(VALIDATORS,)),
    ("EQ-CHECK", "UNTRANSLATABLE"): EvidenceSpec(
        "CONDITIONAL",
        conditions=(
            HELPERS,
            _c(
                "manual_check",
                "the untranslated predicate is re-implemented in the generated helper stub",
            ),
        ),
    ),
    ("EQ-TYPE", "EXACT"): EvidenceSpec("CONDITIONAL", conditions=(VALIDATORS,)),
    ("EQ-TYPE", "LOSSY"): EvidenceSpec(
        "PROVEN",
        "the BSON type cannot represent every value of the SQL type (offset, scale, padding, time part)",
    ),
    # -------------------------------------------------------------------------- foreign keys
    ("EQ-REF-INTEGRITY", "EMBEDDED"): EvidenceSpec(
        "PROVEN",
        "an embedded child lives inside its parent's document, so it cannot reference a missing parent",
    ),
    ("EQ-REF-INTEGRITY", "REFERENCED"): EvidenceSpec("CONDITIONAL", conditions=(HELPERS,)),
    ("EQ-CASCADE-DEL", "EMBEDDED"): EvidenceSpec(
        "PROVEN", "deleting the parent document deletes everything embedded in it"
    ),
    ("EQ-CASCADE-DEL", "REFERENCED"): EvidenceSpec(
        "PROVEN", PLATFORM + " (no cascading deletes across collections)"
    ),
    ("EQ-CASCADE-DEL", "CHAIN"): EvidenceSpec(
        "PROVEN", PLATFORM + " (no cascading deletes across collections)"
    ),
    ("EQ-CASCADE-UPD", "EMBEDDED"): EvidenceSpec(
        "PROVEN",
        "an embedded child holds no copy of the parent key, so there is nothing to propagate",
    ),
    ("EQ-CASCADE-UPD", "IMMUTABLE_KEY"): EvidenceSpec("CONDITIONAL", conditions=(IMMUTABLE_KEYS,)),
    ("EQ-CASCADE-UPD", "MUTABLE_KEY"): EvidenceSpec(
        "PROVEN", PLATFORM + " (no cascading key updates)"
    ),
    ("EQ-SET-NULL", "EMBEDDED"): EvidenceSpec(
        "PROVEN", "deleting the parent deletes the embedded child instead of nulling its key"
    ),
    ("EQ-SET-NULL", "REFERENCED"): EvidenceSpec("PROVEN", PLATFORM + " (no ON DELETE SET NULL)"),
    ("EQ-SET-DEFAULT", "EMBEDDED"): EvidenceSpec(
        "PROVEN", "deleting the parent deletes the embedded child instead of applying the default"
    ),
    ("EQ-SET-DEFAULT", "REFERENCED"): EvidenceSpec(
        "PROVEN", PLATFORM + " (no ON DELETE SET DEFAULT)"
    ),
    ("EQ-RESTRICT", "EMBEDDED"): EvidenceSpec(
        "PROVEN",
        "deleting a parent document always removes what is embedded in it, so it cannot be restricted",
    ),
    ("EQ-RESTRICT", "REFERENCED"): EvidenceSpec("CONDITIONAL", conditions=(HELPERS,)),
    # ------------------------------------------------------------------------------ uniqueness
    ("EQ-ENTITY-UNIQ", "PK_SINGLE"): EvidenceSpec(
        "PROVEN",
        "the _id field of every document is unique, enforced by MongoDB's built-in _id index",
    ),
    ("EQ-ENTITY-UNIQ", "PK_COMPOSITE"): EvidenceSpec("CONDITIONAL", conditions=(INDEXES,)),
    ("EQ-ENTITY-UNIQ", "PK_FOLDED"): EvidenceSpec(
        "PROVEN",
        "an array of references can hold duplicates; MongoDB cannot make array elements unique",
    ),
    ("EQ-ENTITY-UNIQ", "PK_EMBEDDED"): EvidenceSpec(
        "PROVEN", "a unique index does not enforce uniqueness within one document's array"
    ),
    ("EQ-VALUE-UNIQ", "UNIQUE_INDEX"): EvidenceSpec("CONDITIONAL", conditions=(INDEXES,)),
    ("EQ-VALUE-UNIQ", "PARTIAL_OK"): EvidenceSpec("CONDITIONAL", conditions=(INDEXES,)),
    ("EQ-VALUE-UNIQ", "PARTIAL_UNTRANSLATABLE"): EvidenceSpec(
        "PROVEN", "a partial index filter MongoDB cannot express cannot be reproduced as an index"
    ),
    ("EQ-VALUE-UNIQ", "NULLABLE"): EvidenceSpec("CONDITIONAL", conditions=(INDEXES,)),
    ("EQ-VALUE-UNIQ", "EMBEDDED"): EvidenceSpec(
        "PROVEN", "a unique index does not enforce uniqueness within one document's array"
    ),
    ("EQ-CROSS-UNIQ", "OWN_COLLECTION"): EvidenceSpec("CONDITIONAL", conditions=(INDEXES,)),
    ("EQ-CROSS-UNIQ", "EMBEDDED"): EvidenceSpec(
        "PROVEN", "a unique index does not enforce uniqueness within one document's array"
    ),
    ("EQ-CROSS-UNIQ", "SPLIT"): EvidenceSpec(
        "PROVEN", "no MongoDB index or constraint can span two collections"
    ),
}


def evidence_for(rule_id: str, outcome: str) -> EvidenceSpec:
    """The evidence entry of a rule outcome (UNVERIFIED when nobody has justified it yet)."""
    return EVIDENCE.get((rule_id, outcome), EvidenceSpec("UNVERIFIED"))


def validate_table() -> list[str]:
    """Problems with the table itself (used by tests): missing argument/conditions, wrong shape."""
    problems: list[str] = []
    for key, spec in EVIDENCE.items():
        if spec.level == "PROVEN" and not spec.argument:
            problems.append(f"{key}: PROVEN needs an argument")
        if spec.level == "CONDITIONAL" and not spec.conditions:
            problems.append(f"{key}: CONDITIONAL needs conditions")
        if spec.level in ("UNVERIFIED", "TESTED") and (spec.argument or spec.conditions):
            problems.append(f"{key}: {spec.level} carries neither argument nor conditions")
    return problems
