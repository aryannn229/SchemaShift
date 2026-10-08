CREATE TYPE grade_letter AS ENUM ('A', 'B', 'C', 'D', 'F');

CREATE TABLE departments (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  budget NUMERIC(12,2)
);
CREATE TABLE professors (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department_id INT NOT NULL REFERENCES departments(id)
);
CREATE TABLE students (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  enrolled_on DATE NOT NULL DEFAULT CURRENT_DATE,
  advisor_id INT REFERENCES professors(id) ON DELETE SET NULL,
  gpa NUMERIC(3,2),
  CONSTRAINT ck_gpa CHECK (gpa BETWEEN 0 AND 4)
);
CREATE TABLE courses (
  id SERIAL PRIMARY KEY,
  code VARCHAR(10) NOT NULL UNIQUE,
  title TEXT NOT NULL,
  credits INT NOT NULL CONSTRAINT ck_credits CHECK (credits BETWEEN 1 AND 6),
  department_id INT NOT NULL REFERENCES departments(id) ON DELETE CASCADE
);
CREATE TABLE prerequisites (
  course_id INT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  requires_id INT NOT NULL REFERENCES courses(id),
  PRIMARY KEY (course_id, requires_id),
  CONSTRAINT ck_not_self CHECK (course_id <> requires_id)
);
CREATE TABLE sections (
  id SERIAL PRIMARY KEY,
  course_id INT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
  professor_id INT REFERENCES professors(id) ON DELETE SET NULL,
  term VARCHAR(6) NOT NULL,
  room TEXT,
  UNIQUE (course_id, term)
);
CREATE TABLE enrollments (
  student_id INT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  section_id INT NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
  grade grade_letter,
  PRIMARY KEY (student_id, section_id)
);
CREATE TABLE office_hours (
  id SERIAL PRIMARY KEY,
  professor_id INT NOT NULL REFERENCES professors(id) ON DELETE CASCADE,
  weekday INT NOT NULL CONSTRAINT ck_weekday CHECK (weekday BETWEEN 0 AND 6),
  starts TIME NOT NULL,
  ends TIME NOT NULL
);
