CREATE TABLE students (id INT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE courses (id INT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE enrollments (
  student_id INT NOT NULL REFERENCES students(id),
  course_id INT NOT NULL REFERENCES courses(id),
  grade CHAR(1),
  PRIMARY KEY (student_id, course_id)
);
