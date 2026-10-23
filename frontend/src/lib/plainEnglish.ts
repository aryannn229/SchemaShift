/** Plain-language wording for each compiler rule. Technical rule ids stay available in the Verdicts tab. */
export interface PlainRule {
  title: string;
  what: string;
  fix: string;
}

export const PLAIN_RULES: Record<string, PlainRule> = {
  "EQ-CASCADE-DEL": {
    title: "Deleting a parent leaves its children behind",
    what: "In SQL, deleting an account also deletes its transactions. MongoDB does not do this on its own, so orphan records pile up.",
    fix: "Delete the child records in the same step as the parent, using a transaction.",
  },
  "EQ-SET-NULL": {
    title: "Links are not cleared when the target is deleted",
    what: "SQL clears a link automatically when the record it points to is removed. MongoDB leaves a link that points to nothing.",
    fix: "Clear the links yourself, inside a transaction, before deleting the parent.",
  },
  "EQ-NOT-NULL": {
    title: "A required field can be left empty",
    what: "The rule that a field must always have a value is not fully enforced in MongoDB for some inputs.",
    fix: "Keep the generated validator and also check the field in your application before saving.",
  },
  "EQ-CROSS-UNIQ": {
    title: "A uniqueness rule spans two collections",
    what: "SQL can guarantee a pair of values is unique across tables. MongoDB cannot enforce that across collections.",
    fix: "Keep the pair in its own collection with a unique index on both fields.",
  },
  "EQ-CHECK": {
    title: "A value rule is not enforced exactly",
    what: "A rule such as 'amount must be above 0' can accept a value in MongoDB that SQL would reject.",
    fix: "Add the same check in your application code and keep the generated validator.",
  },
  "EQ-REF-INTEGRITY": {
    title: "Links may point to records that do not exist",
    what: "MongoDB has no foreign keys, so a link can refer to a record that was never created or was deleted.",
    fix: "Check that the parent record exists before saving the child.",
  },
  "EQ-RESTRICT": {
    title: "Deleting a record that is still in use is not blocked",
    what: "SQL refuses to delete a customer who still has loans. MongoDB will delete it anyway.",
    fix: "Count the dependent records first and stop the delete if any exist.",
  },
  "EQ-ENTITY-UNIQ": {
    title: "Duplicate items inside one document",
    what: "Items stored inside a list cannot be forced to stay unique by an index.",
    fix: "Check for duplicates before adding, or use an add-to-set update.",
  },
  "EQ-VALUE-UNIQ": {
    title: "Empty values count as duplicates",
    what: "SQL allows many empty values in a unique column. MongoDB treats the second empty value as a duplicate.",
    fix: "Use a unique index that ignores empty values (generated for you).",
  },
  "EQ-ATOMIC": {
    title: "A money transfer touches several collections",
    what: "SQL saves all steps of a transfer together or none at all. In MongoDB this needs an explicit transaction.",
    fix: "Run the steps inside a MongoDB transaction (a helper is generated for you).",
  },
  "EQ-AGG": {
    title: "Totals on empty data give a different answer",
    what: "SUM over no rows is empty in SQL but 0 in MongoDB, so reports can show different numbers.",
    fix: "The generated query already returns empty in that case. Compare results with that in mind.",
  },
  "EQ-JOIN": {
    title: "Missing matches look different",
    what: "A LEFT JOIN returns empty values in SQL. MongoDB leaves the field out entirely.",
    fix: "The generated query fills missing fields with an explicit empty value.",
  },
  "EQ-DEFAULT": {
    title: "Default values are not filled in by the database",
    what: "SQL fills in values like the creation time for you. MongoDB does not.",
    fix: "Use the generated insert helpers, which set the defaults.",
  },
  "EQ-AUTOINC": {
    title: "ID numbers change format",
    what: "Numbered IDs (1, 2, 3...) become MongoDB object IDs. They are unique but not sequential.",
    fix: "Use the new IDs, or turn on the option to keep the old integer IDs.",
  },
  "EQ-TYPE": {
    title: "Some data types are stored differently",
    what: "A few column types, such as timestamps with a time zone, are stored in a slightly different form.",
    fix: "Store the original value in an extra field if the exact form matters.",
  },
};

export function plainRule(ruleId: string, fallbackTitle: string, fallbackFix?: string | null): PlainRule {
  return (
    PLAIN_RULES[ruleId] ?? {
      title: fallbackTitle,
      what: fallbackTitle,
      fix: fallbackFix ?? "See the Verdicts tab for details.",
    }
  );
}

export const STATUS_WORDS = {
  SAFE: "Moves over cleanly",
  CHANGED: "Works, but behaves differently",
  BROKEN: "Will break without a fix",
} as const;
