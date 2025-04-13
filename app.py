# app.py
import streamlit as st
import os
from dotenv import load_dotenv

# Load environment variables early
load_dotenv()

# Configure API Key (optional here if all pages configure it, but good practice)
import google.generativeai as genai
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
        print("Gemini API Key configured for Main App.")
    else:
         # Don't stop the whole app, pages might handle it
         print("Warning: GOOGLE_API_KEY not found in main app.py, pages should handle configuration.")
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
    and transcripts using AI.

    **👈 Select a tool from the sidebar** to get started:

    *   **Analyze Company:** Process a single company's PDF report and DOCX transcript
        to generate a detailed analysis, calculate historical comparisons (YoY/QoQ),
        and save results to the database.
    *   **Compare Competitors (Coming Soon):** Generate a report comparing key financial
        and qualitative metrics across multiple companies for the same period, using
        data stored in the database.
    *   **Analysis History (Coming Soon):** Browse, search, and view past analysis
        results stored in the database.

    **Requirements:**
    *   Ensure you have a `.env` file in the project root with your `GOOGLE_API_KEY`.
    *   For analysis, you need the company's PDF press release and DOCX transcript file.
    *   For comparisons/history, ensure data has been previously analyzed and saved using the 'Analyze Company' tool.
    """
)

st.info("Note: This tool uses Google Gemini. Processing may take a few minutes depending on document size and API response times.")