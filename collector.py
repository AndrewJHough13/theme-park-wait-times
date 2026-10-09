## @file collector.py
#  @brief Polls Queue-Times.com for Universal Orlando wait times and stores them in Postgres.
#
#  @details
#  This is the "collector" half of the project (the other half is dashboard.py).
#  It is a short-lived batch job, not a daemon: it runs once, writes one snapshot
#  of every ride's wait time, and exits. GitHub Actions starts it every 30 minutes
#  (see .github/workflows/collect.yml).
#
#  Data flow, per run:
#  @code
#    Queue-Times API --(HTTP GET x4 parks)--> fetch_park()
#         --> flatten()  (JSON -> ride rows + wait rows)
#         --> main()     (upsert parks, rides; insert wait_times)  --> Postgres
#  @endcode
#
#  Data source: https://queue-times.com/pages/api (free; any UI that shows the
#  data must display "Powered by Queue-Times.com" linking to queue-times.com).
#
#  Configuration (environment variables):
#  - `DATABASE_URL` (required) Postgres connection string, e.g. a Neon URL.
#  - `FORCE=1` (optional) Skip the time-of-day gate; useful for manual testing.
#
#  Exit codes: 0 = success (or intentionally skipped), 1 = every park failed.
#
#  @note Table layout lives in schema.sql. Keep the SQL statements below in sync with it.

import os
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
import requests

## @brief Parks to collect, as {Queue-Times park id: display name}.
#  @details IDs come from https://queue-times.com/parks.json. To track another
#  park, look up its id there and add it here; no other change is required.
PARKS = {
    65: "Universal Studios Florida",
    64: "Islands of Adventure",
    334: "Epic Universe",
    67: "Volcano Bay",
}

## @brief URL template for one park's live data; `{id}` is a key of PARKS.
API = "https://queue-times.com/parks/{id}/queue_times.json"

## @brief Orlando's timezone, used only to decide whether we are in the collection window.
ET = ZoneInfo("America/New_York")

## @brief First hour (inclusive, Eastern time) in which the collector records data.
WINDOW_START_HOUR = 7
## @brief Hour (exclusive, Eastern time) at which the collector stops recording.
#  @note v1 uses a wide fixed window and stores `is_open`, so "park hours +/- 1h"
#  can be applied later at query time. A future version could read real park
#  hours from a schedule API instead.
WINDOW_END_HOUR = 23

## @brief SQL: insert a park, or refresh its name if the id already exists.
UPSERT_PARK = """
INSERT INTO parks (park_id, name) VALUES (%s, %s)
ON CONFLICT (park_id) DO UPDATE SET name = EXCLUDED.name
"""
## @brief SQL: insert a ride, or refresh its park/name/land (rides get renamed or moved).
UPSERT_RIDE = """
INSERT INTO rides (ride_id, park_id, name, land) VALUES (%s, %s, %s, %s)
ON CONFLICT (ride_id) DO UPDATE
  SET park_id = EXCLUDED.park_id, name = EXCLUDED.name, land = EXCLUDED.land
"""
## @brief SQL: insert one wait-time reading; silently ignore if this ride/bucket exists.
#  @details The (ride_id, captured_bucket) primary key makes re-running the job
#  within the same 30-minute bucket harmless (idempotent).
INSERT_WAIT = """
INSERT INTO wait_times (ride_id, captured_bucket, wait_minutes, is_open, stale_minutes)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (ride_id, captured_bucket) DO NOTHING
"""


def in_window(now_et: datetime) -> bool:
    """@brief Decide whether the current time is inside the daily collection window.

    @param now_et Current time, already converted to Eastern time.
    @return True if WINDOW_START_HOUR <= hour < WINDOW_END_HOUR.
    """
    return WINDOW_START_HOUR <= now_et.hour < WINDOW_END_HOUR


def fetch_park(park_id: int) -> dict:
    """@brief Download the live queue-times JSON for one park.

    @details Retries up to 3 times with exponential backoff (1 s, then 2 s)
    to ride out transient network or server errors. Each attempt has a 15 s timeout.

    @param park_id Queue-Times park id (a key of PARKS).
    @return Parsed JSON: `{"lands": [{"name", "rides": [...]}], "rides": [...]}`.
            Each ride has `id`, `name`, `is_open`, `wait_time`, `last_updated` (UTC).
    @throws RuntimeError If all 3 attempts fail; the message includes the last error.
    """
    last_err = None
    for attempt in range(3):
        try:
            r = requests.get(
                API.format(id=park_id),
                headers={"User-Agent": "wait-times-tracker/0.1 (personal project)"},
                timeout=15,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"park {park_id} failed after retries: {last_err}")


def stale_minutes(last_updated: str | None, bucket: datetime) -> int | None:
    """@brief Compute how old the source's reading was relative to our snapshot.

    @details Large values mean Queue-Times stopped updating that ride (e.g. it
    is closed or its feed is down), so the wait value should be treated with suspicion.

    @param last_updated ISO-8601 UTC timestamp from the API (e.g. "2026-10-09T19:20:32.000Z"),
                        or None if absent.
    @param bucket The snapshot time (timezone-aware UTC) being recorded.
    @return Whole minutes between `last_updated` and `bucket`, clamped to 0..32767
            (fits a Postgres SMALLINT); None if the timestamp is missing or unparsable.
    """
    if not last_updated:
        return None
    try:
        ts = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, min(32767, int((bucket - ts).total_seconds() // 60)))


def flatten(park_id: int, payload: dict, bucket: datetime):
    """@brief Convert one park's nested JSON into flat rows ready for the database.

    @details The API groups rides by "land" (themed area) but may also list
    ungrouped rides at the top level; both are handled (ungrouped rides get
    `land = None`). Pure function: no network or database access, so it is easy
    to unit test.

    @param park_id Park the payload belongs to.
    @param payload Parsed JSON as returned by fetch_park().
    @param bucket Snapshot time (30-minute-aligned, timezone-aware UTC).
    @return Tuple `(ride_rows, wait_rows)`:
      - ride_rows: `(ride_id, park_id, name, land)` -> UPSERT_RIDE
      - wait_rows: `(ride_id, bucket, wait_minutes, is_open, stale_minutes)` -> INSERT_WAIT
    @note `wait_time` is clamped to 0..32767. A wait of 0 can mean "walk-on" OR
    "closed"; always read it together with `is_open`.
    """
    ride_rows, wait_rows = [], []
    groups = [(l.get("name"), l.get("rides", [])) for l in payload.get("lands", [])]
    groups.append((None, payload.get("rides", [])))
    for land, rides in groups:
        for ride in rides:
            ride_rows.append((ride["id"], park_id, ride["name"], land))
            wait_rows.append((
                ride["id"], bucket,
                max(0, min(32767, int(ride["wait_time"]))),
                bool(ride["is_open"]),
                stale_minutes(ride.get("last_updated"), bucket),
            ))
    return ride_rows, wait_rows


def main() -> int:
    """@brief Program entry point: collect one snapshot of all parks and save it.

    @details Steps:
      -# Exit early (success) if outside the Eastern-time window, unless `FORCE=1`.
      -# Compute the snapshot "bucket": now, floored to :00 or :30. Runs that start
         a few minutes late still land in the intended bucket.
      -# Fetch and flatten each park. A failing park is logged to stderr and
         skipped, so one outage does not lose the other parks' data.
      -# In a single database transaction, upsert parks and rides, then insert
         the wait rows (committed when the `with` block exits cleanly).

    @return 0 on success or intentional skip; 1 if every park failed
            (so the GitHub Actions run is marked failed and you get notified).
    @throws KeyError If the `DATABASE_URL` environment variable is not set.
    """
    now = datetime.now(timezone.utc)
    if not in_window(now.astimezone(ET)) and os.getenv("FORCE") != "1":
        print("Outside collection window; skipping.")
        return 0

    bucket = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    ride_rows, wait_rows, failures = [], [], 0
    for pid, name in PARKS.items():
        try:
            r, w = flatten(pid, fetch_park(pid), bucket)
            ride_rows += r
            wait_rows += w
        except Exception as e:  # noqa: BLE001  one bad park shouldn't lose the others
            failures += 1
            print(f"ERROR {name}: {e}", file=sys.stderr)

    if wait_rows:
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:  # commits on clean exit
            with conn.cursor() as cur:
                cur.executemany(UPSERT_PARK, list(PARKS.items()))
                cur.executemany(UPSERT_RIDE, ride_rows)
                cur.executemany(INSERT_WAIT, wait_rows)
        print(f"Processed {len(wait_rows)} rides for bucket {bucket.isoformat()}")
    return 1 if failures == len(PARKS) else 0


if __name__ == "__main__":
    sys.exit(main())
