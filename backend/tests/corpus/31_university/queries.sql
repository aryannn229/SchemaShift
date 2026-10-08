SELECT s.name, e.grade FROM students s JOIN enrollments e ON e.student_id = s.id WHERE e.grade = 'A';
SELECT d.name, COUNT(*) FROM departments d LEFT JOIN professors p ON p.department_id = d.id GROUP BY d.name;
SELECT AVG(gpa) FROM students;
BEGIN;
INSERT INTO enrollments (student_id, section_id) VALUES (1, 1);
UPDATE sections SET room = 'R1' WHERE id = 1;
COMMIT;
