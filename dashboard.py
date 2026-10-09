## @file dashboard.py
#  @brief Streamlit web dashboard that charts the wait times saved by collector.py.
#
#  @details
#  Streamlit re-executes this whole script, top to bottom, every time the viewer
#  changes a sidebar widget. That is why this file is a flat script rather than
#  functions plus a main(): the "program" is the page. Layout, in order:
#  -# Title and the required Queue-Times attribution link.
#  -# Sidebar controls: days of history, park, rides.
#  -# Three charts: wait over time, average by hour of day, average by weekday.
#
#  Configuration: `DATABASE_URL` as an environment variable (local runs) or as a
#  Streamlit secret (Streamlit Community Cloud). Read-only: this file never writes
#  to the database.
#
#  Run locally: `streamlit run dashboard.py`
#
#  @note The attribution link below is a licence requirement of the Queue-Times
#  free API. Do not remove it.

import os

import pandas as pd
import psycopg
import streamlit as st

st.set_page_config(page_title="Universal Orlando Wait Times", layout="wide")
st.title("Universal Orlando Wait Times")
st.markdown("[Powered by Queue-Times.com](https://queue-times.com/)")  # required attribution

## @brief Postgres connection string; environment first, then Streamlit secrets.
DB_URL = os.getenv("DATABASE_URL") or st.secrets["DATABASE_URL"]


@st.cache_data(ttl=600)
def load(days: int) -> pd.DataFrame:
    """@brief Read recent open-ride wait times from the database.

    @details Results are cached for 10 minutes (`ttl=600`) per distinct `days`
    value, so moving other widgets does not re-query Postgres. Closed rides are
    excluded because their wait of 0 is meaningless. Timestamps are converted to
    Orlando local time (naive, i.e. without a tz attached) so hour-of-day and
    weekday charts match what a park guest experiences.

    @param days How many days back from now to load.
    @return DataFrame with columns:
      - `ts` (datetime, Eastern local time, 30-minute resolution)
      - `park_name` (str)
      - `ride_name` (str)
      - `wait_minutes` (int)
    """
    q = """SELECT w.captured_bucket AT TIME ZONE 'America/New_York' AS ts,
                  p.name AS park_name, r.name AS ride_name, w.wait_minutes
           FROM wait_times w
           JOIN rides r ON r.ride_id = w.ride_id
           JOIN parks p ON p.park_id = r.park_id
           WHERE w.is_open AND w.captured_bucket > now() - make_interval(days => %s)"""
    with psycopg.connect(DB_URL) as conn:
        return pd.read_sql(q, conn, params=(days,))


## @name Sidebar controls and data selection
#  @{
days = st.sidebar.slider("Days of history", 1, 90, 14)
df = load(days)
if df.empty:
    st.info("No data yet. Run the collector first.")
    st.stop()  # halts this script run; nothing below executes

park = st.sidebar.selectbox("Park", sorted(df.park_name.unique()))
pdf = df[df.park_name == park]
# Default selection: the 5 rides with the highest average wait in this park.
rides = st.sidebar.multiselect("Rides", sorted(pdf.ride_name.unique()),
                               default=list(pdf.groupby("ride_name").wait_minutes.mean()
                                            .nlargest(5).index))
if not rides:
    st.warning("Select at least one ride in the sidebar.")
    st.stop()

sel = pdf[pdf.ride_name.isin(rides)]  # rows for the chosen park and rides
## @}

## @name Charts
#  @{
st.subheader("Wait over time")
st.line_chart(sel.pivot_table(index="ts", columns="ride_name", values="wait_minutes"))

st.subheader("Average wait by hour of day")
sel = sel.assign(hour=sel.ts.dt.hour)
st.line_chart(sel.pivot_table(index="hour", columns="ride_name", values="wait_minutes", aggfunc="mean"))

st.subheader("Average wait by day of week")
sel = sel.assign(dow=sel.ts.dt.day_name())
order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
st.bar_chart(sel.groupby("dow").wait_minutes.mean().reindex(order))
## @}
