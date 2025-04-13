# pages/1_Analyze_Company.py

import streamlit as st
import os
import sys
from datetime import datetime, date
import pandas as pd
import tempfile
import traceback

# --- Ensure Modules are Found (if necessary) ---
# If pages dir causes import issues, uncomment and adjust path:
# script_dir = os.path.dirname(os.path.abspath(__file__))
# project_root = os.path.abspath(os.path.join(script_dir, '..'))
# sys.path.append(project_root)

# --- Load Environment Variables & Configure API ---
# Necessary on each page if not handled globally by Streamlit structure
from dotenv import load_dotenv
load_dotenv() # Load from .env in the root project directory

import google.generativeai as genai
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        st.error("🔴 CRITICAL ERROR: GOOGLE_API_KEY not found.")
        st.stop()
    genai.configure(api_key=api_key)
    # print("Gemini API Key configured for Analyze Company page.") # Optional print
except Exception as e:
    st.error(f"🔴 CRITICAL ERROR: Failed to configure Google Gemini API: {e}")
    st.stop()

# --- Import Core Logic Modules ---
try:
    from pdf_processor import process_pdf_data
    from transcript_processor import process_transcript_data
    from report_generator import create_word_report
    from database_manager import (
        get_db_connection,
        initialize_database,
        get_or_create_company,
        save_analysis_results,
        get_historical_metrics
    )
    # Assuming calculate_comparisons might be needed, refactor later if possible
    from main import calculate_comparisons
except ImportError as e:
     st.error(f"🔴 Error importing backend modules: {e}. Check file paths and ensure pages directory setup is correct.")
     st.stop()

# --- Constants ---
DEFAULT_OUTPUT_DIR = "output_reports"
DEFAULT_DB_PATH = None

# --- Page Specific Session State ---
# Prefix session state keys to avoid conflicts between pages
if 'analyze_analysis_complete' not in st.session_state:
    st.session_state.analyze_analysis_complete = False
if 'analyze_report_bundle' not in st.session_state:
    st.session_state.analyze_report_bundle = None
if 'analyze_generated_report_path' not in st.session_state:
    st.session_state.analyze_generated_report_path = None
if 'analyze_error_message' not in st.session_state:
     st.session_state.analyze_error_message = None
if 'analyze_show_results' not in st.session_state:
     st.session_state.analyze_show_results = False # Control results visibility


# --- Analysis Function (Adapted for this page's state) ---
def run_single_company_analysis(pdf_file_path, transcript_file_path, ticker, year, quarter, report_date_dt, db_path=None):
    """Processes PDF/Transcript, saves to DB, gets history, returns bundle."""
    # Reset this page's state
    st.session_state.analyze_analysis_complete = False
    st.session_state.analyze_report_bundle = None
    st.session_state.analyze_generated_report_path = None
    st.session_state.analyze_error_message = None
    st.session_state.analyze_show_results = False
    db_conn = None

    try:
        st.info("Connecting to database...")
        db_conn = get_db_connection(db_path)
        initialize_database(db_conn) # Ensure tables exist
        company_id = get_or_create_company(db_conn, ticker.upper())
        if company_id is None: raise ValueError(f"Failed to get/create company ID for {ticker}")

        st.info(f"Processing PDF: {os.path.basename(pdf_file_path)}...")
        pdf_data = process_pdf_data(pdf_file_path)
        if pdf_data is None: raise ValueError("PDF Processing Failed.")

        st.info(f"Processing Transcript: {os.path.basename(transcript_file_path)}...")
        transcript_data = process_transcript_data(transcript_file_path, pdf_data.get('full_text'))
        if transcript_data.get('error'): st.warning(f"Transcript warning: {transcript_data['error']}")

        combined_data = { # Combine data
            "pdf_data": pdf_data, "transcript_data": transcript_data,
            "metadata": {
                "ticker": ticker.upper(), "fiscal_year": year, "fiscal_quarter": quarter,
                "report_date": str(report_date_dt), "pdf_filename": os.path.basename(pdf_file_path),
                "transcript_filename": os.path.basename(transcript_file_path),
                "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }

        st.info("Saving analysis results to database...")
        report_id = save_analysis_results(db_conn, company_id, year, quarter, report_date_dt, combined_data)
        if report_id is None: st.warning("Failed to save results to database.")

        st.info("Loading historical data for comparison...")
        historical_metrics = get_historical_metrics(db_conn, company_id, year, quarter)

        st.info("Calculating YoY/QoQ comparisons...")
        current_financials = combined_data.get("pdf_data", {}).get("financial_summary", {})
        comparisons = calculate_comparisons(current_financials, historical_metrics)

        # Prepare bundle and store in session state
        report_bundle = {"current_data": combined_data, "historical_metrics": historical_metrics, "comparisons": comparisons}
        st.session_state.analyze_report_bundle = report_bundle
        st.session_state.analyze_analysis_complete = True
        st.session_state.analyze_show_results = True # Trigger results display
        st.success("Analysis completed successfully!")

    except Exception as e:
        st.session_state.analyze_error_message = f"Error during analysis: {e}\n{traceback.format_exc()}"
        st.error(st.session_state.analyze_error_message)
    finally:
        if db_conn: db_conn.close(); print("Database connection closed.")

# --- Streamlit UI ---
st.header("📊 Analyze Single Company Report")
st.markdown("Upload PDF report and DOCX transcript, provide details, and click Analyze.")

with st.form("analyze_single_form"):
    st.subheader("Inputs:")
    col1, col2 = st.columns(2)
    with col1:
        uploaded_pdf = st.file_uploader("Upload Press Release PDF", type="pdf", key="analyze_pdf")
        ticker = st.text_input("Ticker Symbol (e.g., INFY, TCS)", key="analyze_ticker")
        year = st.number_input("Fiscal Year (e.g., 2024)", min_value=2000, max_value=date.today().year + 5, step=1, key="analyze_year")
    with col2:
        uploaded_docx = st.file_uploader("Upload Transcript DOCX", type="docx", key="analyze_docx")
        quarter = st.selectbox("Fiscal Quarter", options=[1, 2, 3, 4], key="analyze_quarter")
        report_date = st.date_input("Quarter End Date", value=None, key="analyze_date", help="Official end date of the reporting period.")

    submitted = st.form_submit_button("🚀 Analyze Report")

    if submitted:
        if uploaded_pdf is None or uploaded_docx is None or not ticker or not year or not quarter or report_date is None:
            st.warning("⚠️ Please provide all inputs.")
        else:
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, uploaded_pdf.name)
                docx_path = os.path.join(temp_dir, uploaded_docx.name)
                with open(pdf_path, "wb") as f: f.write(uploaded_pdf.getbuffer())
                with open(docx_path, "wb") as f: f.write(uploaded_docx.getbuffer())

                with st.spinner("⏳ Analyzing documents... Please wait."):
                    run_single_company_analysis(pdf_path, docx_path, ticker, year, quarter, report_date, db_path=DEFAULT_DB_PATH)

# --- Display Results (Conditional) ---
if st.session_state.analyze_show_results:
    if st.session_state.analyze_analysis_complete and st.session_state.analyze_report_bundle:
        st.divider()
        st.header("📊 Analysis Results")
        bundle = st.session_state.analyze_report_bundle
        metadata = bundle['current_data'].get('metadata', {})
        st.caption(f"Displaying results for: {metadata.get('ticker','')} FY{metadata.get('fiscal_year','')} Q{metadata.get('fiscal_quarter','')}")

        # Display sections (Executive Summary placeholder, Financials, Guidance, Summaries)
        # (Keep the display logic largely the same as in the previous app.py)
        st.subheader("Executive Summary Snippets (Illustrative)")
        st.markdown("*(Summary generation logic to be added/refined)*") # Placeholder

        st.subheader("Key Financial Highlights")
        financials = bundle['current_data'].get('pdf_data', {}).get('financial_summary', {})
        comparisons = bundle.get('comparisons', {})
        # (Keep the DataFrame creation and display logic for financials table)
        metrics_in_table = [ # Match keys used in financial_summary & comparisons
            "Revenue ($M) QX (Table)", "Revenue ($B) Headline QX",
            "Operating Margin (%) QX (Calc)", "Operating Margin (%) Headline QX",
            "Net Margin (%) QX (Calc)", "Net Margin (%) Headline QX",
            "Net Income ($M) QX (Table)",
            "Basic EPS ($) QX (Table)",
            "TCV ($B) QX", "TCV ($M) QX"
        ]
        display_data = []
        for metric_key_base in metrics_in_table:
             current_val = "N/A"; found_key = None
             potential_keys = [metric_key_base, metric_key_base.replace("($M)", "($B)")]
             for p_key in potential_keys:
                  if p_key in financials: current_val = financials[p_key]; found_key = p_key; break
             if found_key:
                 display_name = found_key.replace(" QX", "").replace(" (Table)","").replace(" (Calc)","").replace(" (Headline)","").replace(" ($)","")
                 yoy = comparisons.get('yoy_pct_change', {}).get(found_key, "N/A"); qoq = comparisons.get('qoq_pct_change', {}).get(found_key, "N/A")
                 display_data.append({"Metric": display_name, "Current": current_val, "YoY Change": yoy, "QoQ Change": qoq})
        if display_data: st.dataframe(pd.DataFrame(display_data).set_index("Metric"), use_container_width=True)
        else: st.info("No specific financial highlights extracted.")

        st.subheader("Guidance")
        fy_rev_guidance = financials.get('FY Revenue Guidance (%)', 'N/A'); fy_op_margin_guidance = financials.get('FY Operating Margin Guidance (%)', 'N/A')
        if fy_rev_guidance != 'N/A' or fy_op_margin_guidance != 'N/A':
            if fy_rev_guidance != 'N/A': st.markdown(f"- **Revenue:** {fy_rev_guidance}")
            if fy_op_margin_guidance != 'N/A': st.markdown(f"- **Operating Margin:** {fy_op_margin_guidance}")
        else: st.info("Guidance not found.")

        st.subheader("Transcript Thematic Summaries")
        summaries = bundle['current_data'].get('transcript_data', {}).get('commentary_summaries', {})
        if summaries:
            for theme, summary_text in summaries.items():
                with st.expander(f"**{theme}**"):
                     lines = summary_text.split('\n', 1); sentiment = "N/A"; body = summary_text
                     if lines[0].strip().startswith("Sentiment:"):
                          sentiment = lines[0].replace("Sentiment:","").strip()
                          if len(lines) > 1: body = lines[1].replace("Summary:","").strip()
                          else: body = ""
                     st.markdown(f"**Sentiment:** {sentiment}")
                     st.markdown(body if body else "(No summary text)")
        else: st.info("No transcript summaries available.")

        # --- Download Button Logic ---
        st.subheader("Download Full Report")
        if st.button("Generate & Prepare DOCX Report", key="analyze_download_prep"):
             with st.spinner("Generating DOCX file..."):
                os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
                ticker_f = metadata.get('ticker', 'Report'); fy_f = metadata.get('fiscal_year', 'FY'); fq_f = metadata.get('fiscal_quarter', 'QX')
                ts_f = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_filename = os.path.join(DEFAULT_OUTPUT_DIR, f"{ticker_f}_FY{fy_f}Q{fq_f}_Analysis_{ts_f}.docx")
                success = create_word_report(st.session_state.analyze_report_bundle, output_filename)
                if success: st.session_state.analyze_generated_report_path = output_filename
                else: st.error("🔴 Failed to generate DOCX report.")

        if st.session_state.analyze_generated_report_path:
             try:
                  with open(st.session_state.analyze_generated_report_path, "rb") as file:
                       st.download_button(
                            label="Download Report", data=file,
                            file_name=os.path.basename(st.session_state.analyze_generated_report_path),
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key="analyze_download_final"
                       )
             except FileNotFoundError: st.error("🔴 Error: Generated report file not found.")
             except Exception as e: st.error(f"🔴 Error preparing download: {e}")

    elif st.session_state.analyze_error_message:
         st.error("Analysis failed. See error message below.")
         st.code(st.session_state.analyze_error_message)

# Display error below results area if it occurred after form submission but before results display
elif st.session_state.analyze_error_message:
    st.error("Analysis failed. See error message below.")
    st.code(st.session_state.analyze_error_message)