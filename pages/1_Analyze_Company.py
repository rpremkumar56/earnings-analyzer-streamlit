# pages/1_Analyze_Company.py

import streamlit as st
import os
import sys
from datetime import datetime, date
import pandas as pd
import tempfile
import traceback

# --- Ensure Modules are Found (if necessary) ---
# Add project root to path if running Streamlit from a different directory
# Example: Adjust based on your structure if needed
try:
    import database_manager # Test import
except ImportError:
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))


# --- Load Environment Variables & Configure API ---
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key: st.error("🔴 CRITICAL ERROR: GOOGLE_API_KEY not found."); st.stop()
    genai.configure(api_key=api_key)
except Exception as e: st.error(f"🔴 CRITICAL ERROR: Failed to configure Google Gemini API: {e}"); st.stop()

# --- Import Core Logic Modules ---
try:
    from pdf_processor import process_pdf_data
    from transcript_processor import process_transcript_data
    from report_generator import create_word_report
    from database_manager import ( get_db_connection, initialize_database, get_or_create_company,
                                   save_analysis_results, get_historical_metrics )
    # Assuming calculate_comparisons is still needed here
    # If calculate_comparisons was moved out of main.py, adjust import
    try:
        from main import calculate_comparisons
    except ImportError:
        # If main.py isn't suitable, define/import calculate_comparisons differently
        st.warning("`calculate_comparisons` function not found. Comparisons might be missing.")
        def calculate_comparisons(current_metrics, historical_metrics): # Dummy function
             print("Warning: calculate_comparisons is a dummy function.")
             return {"qoq_pct_change": {}, "yoy_pct_change": {}}

except ImportError as e: st.error(f"🔴 Error importing backend modules: {e}. Check file paths/structure."); st.stop()

# --- Constants & Session State ---
DEFAULT_OUTPUT_DIR = "output_reports"; DEFAULT_DB_PATH = None
# Initialize session state keys if they don't exist
state_keys = [
    'analyze_analysis_complete', 'analyze_report_bundle',
    'analyze_generated_report_path', 'analyze_error_message', 'analyze_show_results'
]
for key in state_keys:
    if key not in st.session_state:
        st.session_state[key] = None if 'path' in key or 'bundle' in key or 'message' in key else False


# --- Analysis Function (Using db_type) ---
def run_single_company_analysis(pdf_file_path, transcript_file_path, ticker, year, quarter, report_date_dt, db_path=None):
    """Processes PDF/Transcript, saves to DB, gets history, returns bundle."""
    # Reset state for this page before starting
    st.session_state.analyze_analysis_complete = False
    st.session_state.analyze_report_bundle = None
    st.session_state.analyze_generated_report_path = None
    st.session_state.analyze_error_message = None
    st.session_state.analyze_show_results = False
    db_conn, db_type = None, None # Initialize both

    try:
        st.info("Connecting to database...")
        db_conn, db_type = get_db_connection(db_path) # <<< UNPACK TUPLE
        initialize_database(db_conn, db_type) # <<< PASS DB_TYPE
        company_id = get_or_create_company(db_conn, db_type, ticker.upper()) # <<< PASS DB_TYPE
        if company_id is None: raise ValueError(f"Failed to get/create company ID for {ticker}")

        st.info(f"Processing PDF: {os.path.basename(pdf_file_path)}...")
        pdf_data = process_pdf_data(pdf_file_path)
        if pdf_data is None: raise ValueError("PDF Processing Failed.")

        st.info(f"Processing Transcript: {os.path.basename(transcript_file_path)}...")
        transcript_data = process_transcript_data(transcript_file_path, pdf_data.get('full_text'))
        if transcript_data.get('error'): st.warning(f"Transcript warning: {transcript_data['error']}")

        combined_data = { # Combine data
            "pdf_data": pdf_data if pdf_data else {},
            "transcript_data": transcript_data if transcript_data else {},
            "metadata": {
                "ticker": ticker.upper(), "fiscal_year": year, "fiscal_quarter": quarter,
                "report_date": str(report_date_dt), "pdf_filename": os.path.basename(pdf_file_path),
                "transcript_filename": os.path.basename(transcript_file_path),
                "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }

        st.info("Saving analysis results to database...")
        report_id = save_analysis_results(db_conn, db_type, company_id, year, quarter, report_date_dt, combined_data) # <<< PASS DB_TYPE
        if report_id is None: st.warning("Failed to save results to database.")
        else: st.info(f"Results saved for Report ID: {report_id}")


        st.info("Loading historical data for comparison...")
        historical_metrics = get_historical_metrics(db_conn, db_type, company_id, year, quarter) # <<< PASS DB_TYPE

        st.info("Calculating YoY/QoQ comparisons...")
        current_financials = combined_data.get("pdf_data", {}).get("financial_summary", {})
        # Ensure calculate_comparisons handles potential missing keys gracefully
        comparisons = calculate_comparisons(current_financials, historical_metrics)

        # Prepare bundle and store in session state
        report_bundle = {"current_data": combined_data, "historical_metrics": historical_metrics, "comparisons": comparisons}
        st.session_state.analyze_report_bundle = report_bundle
        st.session_state.analyze_analysis_complete = True
        st.session_state.analyze_show_results = True # Trigger results display
        st.success("Analysis completed successfully!")

    except Exception as e:
        st.session_state.analyze_error_message = f"Error during analysis: {e}\n{traceback.format_exc()}"
        st.error(st.session_state.analyze_error_message) # Show error immediately
        st.session_state.analyze_show_results = True # Allow error display area to show
    finally:
        if db_conn:
             try: db_conn.close(); print(f"Database connection closed ({db_type}).")
             except Exception as db_close_e: print(f"Error closing DB connection: {db_close_e}")


# --- Streamlit UI ---
st.header("📊 Analyze Single Company Report")
st.markdown("Upload PDF report and DOCX transcript, provide details, and click Analyze.")

with st.form("analyze_single_form"):
    st.subheader("Inputs:")
    col1, col2 = st.columns(2)
    with col1:
        uploaded_pdf = st.file_uploader("Upload Press Release PDF", type="pdf", key="analyze_pdf")
        ticker = st.text_input("Ticker Symbol (e.g., INFY, TCS)", key="analyze_ticker")
        year = st.number_input("Fiscal Year (e.g., 2024)", min_value=2000, max_value=date.today().year + 5, step=1, format="%d", key="analyze_year")
    with col2:
        uploaded_docx = st.file_uploader("Upload Transcript DOCX", type="docx", key="analyze_docx")
        quarter = st.selectbox("Fiscal Quarter", options=[1, 2, 3, 4], key="analyze_quarter")
        report_date = st.date_input("Quarter End Date", value=None, key="analyze_date", help="Official end date of the reporting period.")

    submitted = st.form_submit_button("🚀 Analyze Report")

    if submitted:
        if uploaded_pdf is None or uploaded_docx is None or not ticker or not year or not quarter or report_date is None:
            st.warning("⚠️ Please provide all inputs.")
        else:
            # Handle files safely
            with tempfile.TemporaryDirectory() as temp_dir:
                pdf_path = os.path.join(temp_dir, uploaded_pdf.name)
                docx_path = os.path.join(temp_dir, uploaded_docx.name)
                try:
                    with open(pdf_path, "wb") as f: f.write(uploaded_pdf.getbuffer())
                    with open(docx_path, "wb") as f: f.write(uploaded_docx.getbuffer())
                except Exception as file_e:
                     st.error(f"Error saving uploaded files: {file_e}")
                     st.stop() # Stop if files can't be saved

                # Run analysis within spinner
                with st.spinner("⏳ Analyzing documents... Please wait."):
                    run_single_company_analysis(pdf_path, docx_path, ticker, year, quarter, report_date, db_path=DEFAULT_DB_PATH)


# --- Display Results (Conditional) ---
if st.session_state.analyze_show_results:
    st.divider()
    if st.session_state.analyze_analysis_complete and st.session_state.analyze_report_bundle:
        st.header("📊 Analysis Results")
        bundle = st.session_state.analyze_report_bundle
        metadata = bundle['current_data'].get('metadata', {})
        st.caption(f"Displaying results for: {metadata.get('ticker','')} FY{metadata.get('fiscal_year','')} Q{metadata.get('fiscal_quarter','')}")

        # Display Executive Summary (Placeholder - requires report_generator integration)
        st.subheader("Executive Summary Snippets (Illustrative)")
        st.markdown("*(Actual summary generation from `report_generator.py` needed)*")

        # Display Financial Highlights Table
        st.subheader("Key Financial Highlights")
        financials = bundle['current_data'].get('pdf_data', {}).get('financial_summary', {})
        comparisons = bundle.get('comparisons', {})
        metrics_in_table = [ # More generic keys first
            "Revenue", "Operating Income", "Net Income", "Basic EPS",
            "Operating Margin", "Net Margin", "TCV",
            "YoY Revenue Growth", "CC Revenue Growth"
        ]
        display_data = []
        added_metrics = set() # Track base metrics added

        for metric_base in metrics_in_table:
            # Search for variations (Table, Calc, Headline, M, B, %)
            found_data = None
            potential_keys_ordered = [ # Prioritize table/calc over headline
                f"{metric_base} ($M) QX (Table)", f"{metric_base} ($) QX (Table)",
                f"{metric_base} (%) QX (Calc)",
                f"{metric_base} ($B) QX", f"{metric_base} ($M) QX", # From headlines/processing
                f"{metric_base} QX", # Raw headline if no unit?
                f"{metric_base} (%) Headline QX",
                f"{metric_base} (%) QX" # Generic percentage
            ]
            for p_key in potential_keys_ordered:
                if p_key in financials:
                     # Prevent adding Margin % if Income $M was already added
                    if ("Margin" in metric_base and "Income" in added_metrics): continue
                    # Prevent adding Income $M if Margin % was already added
                    if ("Income" in metric_base and "Margin" in added_metrics): continue

                    current_val = financials[p_key]
                    # Find corresponding comparison data (might need key adjustment)
                    yoy = comparisons.get('yoy_pct_change', {}).get(p_key, "N/A")
                    qoq = comparisons.get('qoq_pct_change', {}).get(p_key, "N/A")
                    # Clean display name
                    display_name = p_key.replace(" QX", "").replace(" (Table)","").replace(" (Calc)","").replace(" (Headline)","").replace(" ($)","")
                    display_data.append({"Metric": display_name, "Current": current_val, "YoY Change": yoy, "QoQ Change": qoq})
                    added_metrics.add(metric_base) # Mark base metric as added
                    break # Stop after finding the highest priority version

        if display_data:
            st.dataframe(pd.DataFrame(display_data).set_index("Metric"), use_container_width=True)
        else:
            st.info("No specific financial highlights extracted matching display keys.")
            st.json(financials) # Show raw extracted financials for debugging


        # Display Guidance
        st.subheader("Guidance")
        # Look for specific guidance keys
        fy_rev_guidance = financials.get('FY Revenue Guidance (%)', 'N/A')
        fy_op_margin_guidance = financials.get('FY Operating Margin Guidance (%)', 'N/A')
        guidance_found = False
        if fy_rev_guidance != 'N/A': st.markdown(f"- **Revenue:** {fy_rev_guidance}"); guidance_found = True
        if fy_op_margin_guidance != 'N/A': st.markdown(f"- **Operating Margin:** {fy_op_margin_guidance}"); guidance_found = True
        if not guidance_found: st.info("Guidance not found in extracted data.")


        # Display Transcript Summaries
        st.subheader("Transcript Thematic Summaries")
        summaries = bundle['current_data'].get('transcript_data', {}).get('commentary_summaries', {})
        if summaries:
            # Sort themes alphabetically for consistent order
            for theme in sorted(summaries.keys()):
                summary_text = summaries[theme]
                with st.expander(f"**{theme}**"):
                     lines = summary_text.split('\n', 1); sentiment = "N/A"; body = summary_text
                     try: # Robust parsing
                         if lines[0].strip().startswith("Sentiment:"):
                             sentiment = lines[0].replace("Sentiment:","").strip()
                             if len(lines) > 1: body = lines[1].replace("Summary:","").strip()
                             else: body = ""
                     except IndexError: pass # Handle case where split produces only one item
                     st.markdown(f"**Sentiment:** {sentiment}")
                     st.markdown(body if body else "(No summary text)")
        else: st.info("No transcript summaries available.")

        # --- Download Button Logic ---
        st.subheader("Download Full Report")
        # Use a unique key for the button if reusing logic
        if st.button("Generate & Prepare DOCX Report", key="analyze_download_prep"):
             with st.spinner("Generating DOCX file..."):
                os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
                # Extract metadata safely using .get
                meta_d = bundle['current_data'].get('metadata', {})
                ticker_f = meta_d.get('ticker', 'Report'); fy_f = meta_d.get('fiscal_year', 'FY'); fq_f = meta_d.get('fiscal_quarter', 'QX')
                ts_f = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_filename = os.path.join(DEFAULT_OUTPUT_DIR, f"{ticker_f}_FY{fy_f}Q{fq_f}_Analysis_{ts_f}.docx")
                # Generate the report using the stored bundle
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

    # Display error message if analysis failed
    elif st.session_state.analyze_error_message:
         st.error("Analysis failed:")
         st.code(st.session_state.analyze_error_message)

# Display error message if it occurred but results aren't shown (e.g., initial error)
elif st.session_state.analyze_error_message:
    st.error("An error occurred:")
    st.code(st.session_state.analyze_error_message)