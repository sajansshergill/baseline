-- Per-activity features as of one data date.
--
-- Used for BOTH training and live predictions, so the two can never drift apart.
-- Point-in-time correct: only snapshots with data_date <= :as_of are visible here.
-- Row-level security means this only ever sees the current tenant's data.
--
-- Parameters: :project_id, :as_of

WITH snaps AS (
    SELECT snapshot_id, data_date,
           row_number() OVER (ORDER BY data_date DESC) AS weeks_ago   -- 1 = the as_of update
    FROM snapshot_current
    WHERE project_id = :project_id
      AND data_date <= :as_of
),
hist AS (
    SELECT a.activity_id, s.data_date, s.weeks_ago,
           a.status, a.activity_type, a.delay_note,
           a.percent_complete::float8        AS percent_complete,
           a.remaining_duration_days::float8 AS remaining_duration_days,
           c.total_float_days::float8        AS total_float_days,
           c.early_finish, c.is_critical
    FROM snaps s
    JOIN activity a     ON a.snapshot_id = s.snapshot_id
    JOIN activity_cpm c ON c.snapshot_id = a.snapshot_id AND c.activity_id = a.activity_id
),
moves AS (   -- how far each activity's forecast finish moved, update over update
    SELECT activity_id, data_date,
           early_finish - lag(early_finish) OVER (PARTITION BY activity_id ORDER BY data_date)
               AS moved_days
    FROM hist
),
slips AS (
    SELECT activity_id,
           count(*) FILTER (WHERE moved_days > 0)                    AS finish_slip_count,
           coalesce(sum(moved_days) FILTER (WHERE moved_days > 0), 0) AS finish_slip_days
    FROM moves
    GROUP BY activity_id
),
last_move AS (
    SELECT activity_id, moved_days
    FROM moves
    WHERE data_date = (SELECT max(data_date) FROM snaps)
),
preds AS (   -- predecessors in the current update, and how many just slipped
    SELECT r.successor_id AS activity_id,
           count(*)                                AS pred_count,
           count(*) FILTER (WHERE lm.moved_days > 0) AS late_pred_count
    FROM relationship r
    JOIN snaps s ON s.snapshot_id = r.snapshot_id AND s.weeks_ago = 1
    LEFT JOIN last_move lm ON lm.activity_id = r.predecessor_id
    GROUP BY r.successor_id
),
window8 AS (
    SELECT activity_id, min(total_float_days) AS min_float_8w
    FROM hist WHERE weeks_ago <= 8
    GROUP BY activity_id
),
tracked AS (
    SELECT activity_id, count(*) AS weeks_tracked FROM hist GROUP BY activity_id
),
cur   AS (SELECT * FROM hist WHERE weeks_ago = 1),
back4 AS (SELECT * FROM hist WHERE weeks_ago = 5)

SELECT cur.activity_id,
       cur.total_float_days,
       cur.total_float_days - back4.total_float_days            AS float_change_4w,
       window8.min_float_8w,
       cur.percent_complete,
       (cur.percent_complete - back4.percent_complete) / 4.0    AS progress_velocity_4w,
       cur.remaining_duration_days,
       slips.finish_slip_count::float8                          AS finish_slip_count,
       slips.finish_slip_days::float8                           AS finish_slip_days,
       coalesce(preds.pred_count, 0)::float8                    AS pred_count,
       coalesce(preds.late_pred_count, 0)::float8               AS late_pred_count,
       cur.is_critical::int::float8                             AS is_critical,
       tracked.weeks_tracked::float8                            AS weeks_tracked,
       (cur.delay_note IS NOT NULL)::int::float8                AS has_delay_note,
       (cur.activity_type <> 'task')::int::float8               AS is_milestone
FROM cur
LEFT JOIN back4   USING (activity_id)
LEFT JOIN window8 USING (activity_id)
LEFT JOIN slips   USING (activity_id)
LEFT JOIN preds   USING (activity_id)
LEFT JOIN tracked USING (activity_id)
WHERE cur.status <> 'complete'
ORDER BY cur.activity_id