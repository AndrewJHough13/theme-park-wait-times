-- Run once in the Neon SQL Editor. Normalized to stay well under the 0.5 GB free limit.
-- (If you already ran the old schema: DROP TABLE wait_times; first.)

CREATE TABLE IF NOT EXISTS parks (
    park_id  INTEGER PRIMARY KEY,
    name     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rides (
    ride_id  INTEGER PRIMARY KEY,
    park_id  INTEGER NOT NULL REFERENCES parks(park_id),
    name     TEXT NOT NULL,
    land     TEXT
);

-- Narrow fact table: ~50 bytes/row of data, roughly 150 MB/year including the index.
CREATE TABLE IF NOT EXISTS wait_times (
    ride_id          INTEGER     NOT NULL REFERENCES rides(ride_id),
    captured_bucket  TIMESTAMPTZ NOT NULL,   -- run time floored to 30 min (dedupes reruns)
    wait_minutes     SMALLINT    NOT NULL,
    is_open          BOOLEAN     NOT NULL,
    stale_minutes    SMALLINT,               -- bucket time minus source's last_updated
    PRIMARY KEY (ride_id, captured_bucket)
);

-- BRIN index: tiny, good for time-range scans on append-only data.
CREATE INDEX IF NOT EXISTS idx_wait_times_time
    ON wait_times USING BRIN (captured_bucket);
