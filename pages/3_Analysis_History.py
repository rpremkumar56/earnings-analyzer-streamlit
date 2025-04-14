# pages/3_Analysis_History.py

import streamlit as st
import os
import sys
import pandas as pd
from datetime import date
import traceback

# --- Ensure Modules are Found (if necessary) ---
try:
    import database_manager # Test import
except ImportError:
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

# --- Import Core Logic Modules ---
try:
    # Import only necessary DB functions
    from database_manager import get_db_connection, get_analysis_history
except ImportError as e:
     st.error(f"🔴 Error importing backend modules: {e}. Check file paths.")
     st.stop()

# --- Constants ---
DEFAULT_DB_PATH = None

# --- Streamlit UI ---
st.header("⏳ Analysis History")
st.markdown("Browse and search previously analyzed earnings reports stored in the database.")

# --- Filters ---
st.subheader("Filters")
col1, col2, col3 = st.columns(3)
with col1:
    ticker_filter = st.text_input("Ticker Symbol (Leave blank for all)", key="history_ticker")
with col2:
    # Use format="%d" to avoid decimals in number input for year
    year_filter = st.number_input("Fiscal Year (Optional)", min_value=2000, max_value=date.today().year + 5, step=1, value=None, format="%d", key="history_year")
with col3:
    quarter_filter = st.selectbox("Fiscal Quarter (Optional)", options=[None, 1, 2, 3, 4], index=0, format_func=lambda x: 'Any' if x is None else f'Q{x}', key="history_quarter")

# --- Load History (UPDATED DB CALL) ---
history_df = pd.DataFrame() # Initialize empty
db_conn, db_type = None, None # Initialize
error_msg = None
try:
    # Connect using the updated function
    db_conn, db_type = get_db_connection(DEFAULT_DB_PATH) # <<< UNPACK TUPLE
    if db_conn:
         # Call the function with db_type
         history_df = get_analysis_history(db_conn, db_type, ticker=ticker_filter, year=year_filter, quarter=quarter_filter) # <<< PASS DB_TYPE
except Exception as e:
     error_msg = f"Failed to connect to or query database: {e}\n{traceback.format_exc()}"
     st.error(error_msg) # Show error in UI
finally:
     if db_conn:
         try: db_conn.close()
         except Exception as db_close_e: print(f"Error closing db connection: {db_close_e}")


# --- Display History Table ---
st.subheader("Analyzed Reports")
if error_msg:
     st.info("Could not load history due to database error.")
elif not history_df.empty:
    # Customize columns displayed
    display_df = history_df.copy()
    # Convert report_date to date only if it's not already
    if 'report_date' in display_df.columns:
        display_df['report_date'] = pd.to_datetime(display_df['report_date']).dt.date
    # Rename columns
    display_df = display_df.rename(columns={
        "ticker": "Ticker", "fiscal_year": "Year", "fiscal_quarter": "Quarter",
        "report_date": "Report Date", "analysis_timestamp": "Analyzed On",
        "pdf_filename": "PDF File", "transcript_filename": "Transcript File",
        "report_id": "Report ID"
    })
    # Select and reorder columns
    cols_to_display = ["Ticker", "Year", "Quarter", "Report Date", "Analyzed On", "PDF File", "Transcript File"]
    cols_to_display = [col for col in cols_to_display if col in display_df.columns] # Ensure columns exist
    # Use st.data_editor for potential future interactions (like selection)
    st.dataframe(
        display_df[cols_to_display],
        use_container_width=True,
        hide_index=True
    )
else:
    st.info("No analysis history found matching the current filters. Use the 'Analyze Company' page to add data.")

# --- Placeholder for future 'View Details' functionality ---
st.caption("Future enhancements: Click row to view detailed results (requires fetching summaries/metrics by Report ID).")