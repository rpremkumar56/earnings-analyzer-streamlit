# app.py
import streamlit as st
import os
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables early
load_dotenv()

# Configure API Key (optional here if all pages configure it, but good practice)
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
        print("Gemini API Key configured for Main App.")
    else:
         print("Warning: GOOGLE_API_KEY not set in main app.py, pages should handle configuration.")
except Exception as e:
    print(f"Warning: Error configuring Gemini API in main app.py: {e}")


# --- Streamlit UI Configuration ---
st.set_page_config(
    page_title="Earnings Analysis Suite",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded" # Keep sidebar open
)

# --- Main Page Content ---
st.title("📈 Earnings Analysis Suite")

st.sidebar.success("Select an analysis tool above.") # Guide user

st.markdown(
    """
    Welcome to the Earnings Analysis Suite!

    This tool helps automate the process of analyzing financial earnings reports
    and transcripts using AI, with support for historical comparisons and competitor benchmarking.

    **👈 Select a tool from the sidebar** to get started:

    *   **Analyze Company:** Process a single company's PDF report and DOCX transcript
        to generate a detailed analysis, calculate historical comparisons (YoY/QoQ),
        and save results to the database.
    *   **Compare Competitors:** Generate a report comparing key financial
        and qualitative metrics across multiple companies for the same period, using
        data stored in the database.
    *   **Analysis History:** Browse, search, and view past analysis
        results stored in the database.

    
    """
)

st.info("Note: This tool uses Google Gemini. Processing may take a few minutes depending on document size and API response times.")

# Check DB connection type on main page load (optional)
# Moved DB check inside try-except for robustness
db_type_display = "N/A"
try:
    from database_manager import get_db_connection
    conn_check, db_type_check = get_db_connection()
    db_type_display = db_type_check.upper()
    if conn_check: conn_check.close()
except Exception as e:
    db_type_display = f"Connection Check Failed: {e}"

st.sidebar.caption(f"Database Mode: {db_type_display}")