# pages/3_Analysis_History.py

import streamlit as st
import os
import sys
import pandas as pd
from datetime import date

# --- Ensure Modules are Found (if necessary) ---
# script_dir = os.path.dirname(os.path.abspath(__file__))
# project_root = os.path.abspath(os.path.join(script_dir, '..'))
# sys.path.append(project_root)

# --- Import Core Logic Modules ---
try:
    from database_manager import get_db_connection # Only need DB connection and potentially a new query function
    # We might need a new function in database_manager to fetch report list based on filters
except ImportError as e:
     st.error(f"🔴 Error importing backend modules: {e}. Check file paths.")
     st.stop()

# --- Constants ---
DEFAULT_DB_PATH = None

# --- DB Function Placeholder (Needs to be implemented in database_manager.py) ---
def get_analysis_history(conn, ticker=None, year=None, quarter=None):
    """
    Fetches a list of analyzed reports based on filters.
    (This function needs to be added to database_manager.py)
    """
    cursor = conn.cursor()
    query = """
        SELECT c.ticker, r.fiscal_year, r.fiscal_quarter, r.report_date,
               r.pdf_filename, r.transcript_filename, r.analysis_timestamp, r.id as report_id
        FROM reports r
        JOIN companies c ON r.company_id = c.id
        WHERE 1=1
    """
    params = []
    if ticker:
        query += " AND c.ticker = ?"
        params.append(ticker.upper())
    if year:
        query += " AND r.fiscal_year = ?"
        params.append(year)
    if quarter:
        query += " AND r.fiscal_quarter = ?"
        params.append(quarter)

    query += " ORDER BY r.analysis_timestamp DESC" # Show most recent first

    try:
        cursor.execute(query, params)
        results = cursor.fetchall()
        # Convert to list of dicts or DataFrame
        history_df = pd.DataFrame(results, columns=[desc[0] for desc in cursor.description])
        return history_df
    except Exception as e:
         st.error(f"Error fetching history from database: {e}")
         return pd.DataFrame() # Return empty DataFrame on error
    finally:
        cursor.close()


# --- Streamlit UI ---
st.header("⏳ Analysis History")
st.markdown("Browse and search previously analyzed earnings reports.")

# --- Filters ---
st.subheader("Filters")
col1, col2, col3 = st.columns(3)
with col1:
    ticker_filter = st.text_input("Ticker Symbol", key="history_ticker")
with col2:
    year_filter = st.number_input("Fiscal Year (Optional)", min_value=2000, max_value=date.today().year + 5, step=1, value=None, key="history_year")
with col3:
    quarter_filter = st.selectbox("Fiscal Quarter (Optional)", options=[None, 1, 2, 3, 4], index=0, key="history_quarter")

# --- Load History ---
history_df = pd.DataFrame() # Initialize empty
db_conn = None
try:
    db_conn = get_db_connection(DEFAULT_DB_PATH)
    if db_conn:
         # Call the function (assuming it's added to database_manager.py)
         history_df = get_analysis_history(db_conn, ticker=ticker_filter, year=year_filter, quarter=quarter_filter)
except Exception as e:
     st.error(f"Failed to connect to database: {e}")
finally:
     if db_conn: db_conn.close()


# --- Display History Table ---
st.subheader("Analyzed Reports")
if not history_df.empty:
    # Customize columns displayed
    display_df = history_df[[
        "ticker", "fiscal_year", "fiscal_quarter", "report_date",
        "analysis_timestamp", "pdf_filename" # Example columns
    ]].rename(columns={
        "fiscal_year": "Year", "fiscal_quarter": "Quarter",
        "report_date": "Report Date", "analysis_timestamp": "Analyzed On",
        "pdf_filename": "PDF File"
    })
    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("No analysis history found matching the current filters.")

st.caption("Future enhancements: Click row to view detailed results.")