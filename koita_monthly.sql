-- Metabase native question "Koita Monthly API" (database 2).
-- Variables: start_date, end_date, both type "Date", both required. end_date is exclusive (1st of next month).
-- Same logic as the Koita Dashboard cards, but counted only up to end_date ("as of month end").
-- monthly_active_students is the only figure that covers just the month itself.
-- Aggregates only, so no names or emails leave the database.
-- NULL watched_on / submitted_at rows (missed sessions, unsubmitted assignments) are kept on purpose:
-- the dashboard counts them in attendance and assignment denominators.
-- ponytail: they have no date, so past months also count sessions/assignments created later;
-- join stg_live_session.conducted_on if historical attendance must be exact.
WITH
sess AS (SELECT * FROM staging.stg_student_sessions     WHERE source_system = 'Open edX' AND (watched_on   IS NULL OR watched_on   < {{end_date}})),
rec  AS (SELECT * FROM staging.stg_student_pre_recorded WHERE source_system = 'Open edX' AND watched_at   < {{end_date}}),
quiz AS (SELECT * FROM staging.stg_student_quiz         WHERE source_system = 'Open edX' AND attempted_at < {{end_date}}),
asg  AS (SELECT * FROM staging.stg_student_assignment   WHERE source_system = 'Open edX' AND (submitted_at IS NULL OR submitted_at < {{end_date}})),

totals AS (
    SELECT student_id, SUM(d) AS d
    FROM (SELECT student_id, duration_in_sec AS d FROM sess
          UNION ALL SELECT student_id, watchtime_in_sec FROM rec) x
    GROUP BY student_id
),
active AS (SELECT student_id, d FROM totals WHERE d >= 14256),

quiz_map AS (
    SELECT DISTINCT usage_key::TEXT AS resource_id, TRIM(subsection_name) AS quiz_name
    FROM raw."Openedx_course_content_catalog_coursecontentblock"
    WHERE block_type = 'problem' AND TRIM(subsection_name) ILIKE 'Quiz %:%'
),
active_quiz AS (
    SELECT DISTINCT q.student_id, m.quiz_name
    FROM quiz q
    JOIN active a   ON a.student_id::TEXT = q.student_id::TEXT
    JOIN quiz_map m ON m.resource_id = q.resource_id::TEXT
),
n AS (SELECT (SELECT COUNT(*) FROM active) AS active_n,
             (SELECT COUNT(DISTINCT quiz_name) FROM quiz_map) AS quiz_n),

quiz_pct AS (SELECT student_id, 100.0 * SUM(marks) / NULLIF(SUM(max_marks), 0) AS p
             FROM quiz WHERE marks IS NOT NULL AND max_marks > 0 GROUP BY student_id),
asg_pct  AS (SELECT student_id, MAX(marks_pct) AS p FROM asg GROUP BY student_id),
att_pct  AS (SELECT student_id,
                    100.0 * COUNT(DISTINCT session_id) FILTER (WHERE duration_in_sec > 0)
                          / NULLIF(COUNT(DISTINCT session_id), 0) AS p
             FROM sess GROUP BY student_id),
asg_done AS (SELECT student_id FROM asg GROUP BY student_id
             HAVING COUNT(DISTINCT resource_id) = COUNT(DISTINCT resource_id)
                    FILTER (WHERE LOWER(TRIM(submission_status)) IN ('accepted', 'submitted'))),

enrolled AS (
    SELECT DISTINCT r.student_id
    FROM rec r
    JOIN staging.stg_cohort c ON c.cohort_code::TEXT = r.cohort_code::TEXT AND c.source_system = 'Open edX'
    JOIN staging.stg_student_details sd ON sd.student_id::TEXT = r.student_id::TEXT AND sd.source_system = 'Open edX'
    WHERE r.student_id IS NOT NULL
),
submitted AS (
    SELECT DISTINCT student_id FROM asg
    WHERE student_id IS NOT NULL
      AND LOWER(TRIM(submission_status)) IN ('accepted', 'reattempt', 'under review', 'submitted', 'draft')
)

SELECT
    -- includes 2 test courses (2026_Test, CS101) until Hiloni confirms whether to count them
    (SELECT COUNT(*) FROM staging.stg_cohort WHERE source_system = 'Open edX'
       AND start_date < {{end_date}})                                                      AS onboarded_cohorts,
    n.active_n                                                                             AS total_active_students,
    (SELECT COUNT(DISTINCT student_id) FROM (
        SELECT student_id FROM sess WHERE watched_on >= {{start_date}}
        UNION SELECT student_id FROM rec WHERE watched_at >= {{start_date}}) m)           AS monthly_active_students,
    ROUND((SELECT SUM(d) FROM active) / 3600.0 / NULLIF(n.active_n, 0), 2)                 AS avg_watch_hours_active,
    ROUND(100.0 * (SELECT COUNT(*) FROM active_quiz) / NULLIF(n.active_n * n.quiz_n, 0), 2) AS quiz_participation_rate,
    ROUND(100.0 * (SELECT COUNT(*) FROM (SELECT student_id FROM active_quiz GROUP BY student_id
                                         HAVING COUNT(DISTINCT quiz_name) = n.quiz_n) z)
                / NULLIF(n.active_n, 0), 2)                                                AS quiz_completion_rate,
    ROUND((SELECT AVG(100.0 * q.marks / q.max_marks) FROM quiz q
           JOIN active a ON a.student_id::TEXT = q.student_id::TEXT
           WHERE q.marks IS NOT NULL AND q.max_marks > 0), 2)                              AS avg_quiz_score,
    ROUND(100.0 * (SELECT COUNT(*) FROM active a
                   JOIN quiz_pct q USING (student_id) JOIN asg_pct s USING (student_id)
                   JOIN att_pct t USING (student_id)
                   WHERE q.p >= 70 AND s.p >= 70 AND t.p >= 70) / NULLIF(n.active_n, 0), 2) AS course_completion_rate,
    ROUND(100.0 * (SELECT COUNT(*) FROM active a JOIN asg_done USING (student_id))
                / NULLIF(n.active_n, 0), 2)                                                AS assignment_submission_rate,
    ROUND(100.0 * (SELECT COUNT(*) FROM enrolled WHERE student_id NOT IN (SELECT student_id FROM submitted))
                / NULLIF((SELECT COUNT(*) FROM enrolled), 0), 2)                           AS no_assignment_rate
FROM n
