-- leading comment
CREATE TABLE notes ( /* inline ; comment */
  id INT PRIMARY KEY,
  body TEXT DEFAULT 'semi;colon -- not a comment' -- trailing
);
