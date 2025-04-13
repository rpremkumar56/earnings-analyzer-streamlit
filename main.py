# main.py

import argparse
import os
import sys
from datetime import datetime, date
from dotenv import load_dotenv
import google.generativeai as genai
import sqlite3 # Import sqlite3 for error handling

# --- Import project modules ---
from pdf_processor import process_pdf_data
from transcript_processor import process_transcript_data
from report_generator import create_word_report
# Import the new database manager
from database_manager import (
    get_db_connection,
    initialize_database,
    get_or_create_company,
    save_analysis_results,
    get_historical_metrics
)
# --------------------------------

def calculate_comparisons(current_metrics, historical_metrics):
    """Calculates YoY and QoQ percentage changes for numeric metrics."""
    comparisons = {
        "qoq_pct_change": {},
        "yoy_pct_change": {}
    }
    print("Calculating YoY and QoQ comparisons...")

    # Define key metrics to compare (adjust as needed)
    # Ensure these keys match exactly what's stored in financial_summary/database
    metrics_to_compare = [
        "Revenue ($M) Q3", # Assuming Q3 is the current quarter label
        "Operating Margin (%) Q3",
        "Net Margin (%) Q3 (calculated from Table)",
        "Basic EPS ($) Q3",
        "Large Deal TCV ($B) Q3",
        "Free Cash Flow ($M) Q3",
        "Net Income ($M) Q3 (from Table)", # Use the table one if available
    ]

    for metric_key in metrics_to_compare:
        current_raw = current_metrics.get(metric_key)
        # Use the numeric value cleaned by the database manager for calculations
        # We need to re-clean here if we are using the direct output of pdf_processor
        # Or ideally, retrieve the cleaned numeric value *after* saving/loading from DB
        # For now, let's assume pdf_processor's output needs cleaning for calc.
        from database_manager import clean_numeric_value # Import helper temporarily
        current_val = clean_numeric_value(current_raw)

        if current_val is None:
            print(f"  Skipping comparisons for '{metric_key}' (current value non-numeric: {current_raw})")
            continue # Skip if current value isn't numeric

        # --- QoQ Comparison ---
        qoq_hist = historical_metrics.get("qoq", {}).get(metric_key, {})
        qoq_val = qoq_hist.get('value') # Get the numeric value stored in DB
        qoq_raw = qoq_hist.get('raw', 'N/A')

        if qoq_val is not None:
            try:
                if qoq_val != 0:
                    qoq_change = ((current_val - qoq_val) / abs(qoq_val)) * 100
                    comparisons["qoq_pct_change"][metric_key] = f"{qoq_change:.1f}%"
                    print(f"  QoQ Change for '{metric_key}': {qoq_change:.1f}% (Current: {current_val}, Prev: {qoq_val})")
                elif current_val == 0:
                    comparisons["qoq_pct_change"][metric_key] = "0.0%" # Both zero
                    print(f"  QoQ Change for '{metric_key}': 0.0% (Both zero)")
                else:
                    comparisons["qoq_pct_change"][metric_key] = "Inf" # Change from zero to non-zero
                    print(f"  QoQ Change for '{metric_key}': Inf (Prev was zero)")
            except Exception as e:
                comparisons["qoq_pct_change"][metric_key] = "Error"
                print(f"  Error calculating QoQ for '{metric_key}': {e}")
        else:
             comparisons["qoq_pct_change"][metric_key] = "N/A" # No prior quarter data
             print(f"  QoQ Change for '{metric_key}': N/A (Previous data not found: {qoq_raw})")

        # --- YoY Comparison ---
        yoy_hist = historical_metrics.get("yoy", {}).get(metric_key, {})
        yoy_val = yoy_hist.get('value') # Numeric value from DB
        yoy_raw = yoy_hist.get('raw', 'N/A')

        if yoy_val is not None:
            try:
                if yoy_val != 0:
                    yoy_change = ((current_val - yoy_val) / abs(yoy_val)) * 100
                    comparisons["yoy_pct_change"][metric_key] = f"{yoy_change:.1f}%"
                    print(f"  YoY Change for '{metric_key}': {yoy_change:.1f}% (Current: {current_val}, YoY: {yoy_val})")
                elif current_val == 0:
                    comparisons["yoy_pct_change"][metric_key] = "0.0%"
                    print(f"  YoY Change for '{metric_key}': 0.0% (Both zero)")
                else:
                    comparisons["yoy_pct_change"][metric_key] = "Inf"
                    print(f"  YoY Change for '{metric_key}': Inf (YoY was zero)")
            except Exception as e:
                comparisons["yoy_pct_change"][metric_key] = "Error"
                print(f"  Error calculating YoY for '{metric_key}': {e}")
        else:
             comparisons["yoy_pct_change"][metric_key] = "N/A"
             print(f"  YoY Change for '{metric_key}': N/A (YoY data not found: {yoy_raw})")

    return comparisons


def main():
    """
    Main function to orchestrate the analysis process with historical context.
    """
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
         print("CRITICAL ERROR: GOOGLE_API_KEY missing.", file=sys.stderr)
         sys.exit(1)
    try:
        genai.configure(api_key=api_key)
        print("Google Gemini API Key configured.")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to configure Google Gemini API: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Argument Parser Changes ---
    parser = argparse.ArgumentParser(description="Research Analyst Tool - Analyze Earnings Report and Transcript with Historical Context.")
    parser.add_argument("pdf_path", help="Path to the financial report PDF file.")
    parser.add_argument("transcript_path", help="Path to the earnings call transcript DOCX file.")
    parser.add_argument("--ticker", required=True, help="Company ticker symbol (e.g., INFY, AAPL).")
    parser.add_argument("--year", required=True, type=int, help="Fiscal year of the report (e.g., 2024).")
    parser.add_argument("--quarter", required=True, type=int, choices=[1, 2, 3, 4], help="Fiscal quarter of the report (1, 2, 3, or 4).")
    parser.add_argument("--report_date", help="Optional: Official end date of the reporting period (YYYY-MM-DD). If omitted, uses current date.")
    parser.add_argument("-o", "--output_dir", default="output", help="Directory to save the generated report (default: output).")
    parser.add_argument("--db_path", default=None, help="Optional path to the SQLite database file.")

    args = parser.parse_args()

    # --- Input File Validation (Existing) ---
    if not os.path.isfile(args.pdf_path):
        print(f"Error: PDF file not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(args.transcript_path):
        print(f"Error: Transcript file not found: {args.transcript_path}", file=sys.stderr)
        sys.exit(1)
    if not args.transcript_path.lower().endswith('.docx'):
        print(f"Error: Transcript file must be .docx: {args.transcript_path}", file=sys.stderr)
        sys.exit(1)
    # --- Report Date Handling ---
    report_date = None
    if args.report_date:
        try:
            report_date = datetime.strptime(args.report_date, "%Y-%m-%d").date()
        except ValueError:
            print(f"Error: Invalid report_date format. Use YYYY-MM-DD. Got: {args.report_date}", file=sys.stderr)
            sys.exit(1)
    else:
        report_date = date.today() # Default if not provided
        print(f"Warning: --report_date not provided. Using current date: {report_date}. This should ideally be the quarter end date.")

    # --- Database Setup ---
    db_conn = None # Initialize connection variable
    try:
        db_conn = get_db_connection(args.db_path)
        initialize_database(db_conn)
        company_id = get_or_create_company(db_conn, args.ticker.upper())
        if company_id is None:
            print(f"Error: Failed to get or create company ID for ticker {args.ticker}", file=sys.stderr)
            sys.exit(1)
    except Exception as db_e:
         print(f"Database setup failed: {db_e}", file=sys.stderr)
         if db_conn:
             db_conn.close()
         sys.exit(1)

    print("="*50)
    print(f"Starting Analysis for: {args.ticker.upper()} - FY{args.year} Q{args.quarter}")
    print(f"PDF File: {args.pdf_path}")
    print(f"Transcript File: {args.transcript_path}")
    print(f"Report Date: {report_date}")
    print("="*50)

    # --- Main Processing Logic ---
    try:
        # 1. Process PDF
        pdf_data = process_pdf_data(args.pdf_path)
        if pdf_data is None:
             raise ValueError("PDF Processing Failed.") # Raise error to trigger finally block

        # 2. Process Transcript
        transcript_data = process_transcript_data(args.transcript_path, pdf_data.get('full_text'))
        # Handle potential transcript processing errors before saving
        if transcript_data.get('error'):
            print(f"Warning: Transcript processing encountered an error: {transcript_data['error']}. Some data might be missing.", file=sys.stderr)
            # Continue processing, but the error will be noted in the report

        # 3. Combine Current Data
        combined_data = {
            "pdf_data": pdf_data if pdf_data else {},
            "transcript_data": transcript_data if transcript_data else {},
            "metadata": {
                "ticker": args.ticker.upper(),
                "fiscal_year": args.year,
                "fiscal_quarter": args.quarter,
                "report_date": str(report_date), # Store as string
                "pdf_filename": os.path.basename(args.pdf_path),
                "transcript_filename": os.path.basename(args.transcript_path),
                "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }

        # 4. Save Current Analysis Results to Database
        print("\n--- Saving Current Analysis to Database ---")
        report_id = save_analysis_results(
            db_conn,
            company_id,
            args.year,
            args.quarter,
            report_date,
            combined_data
        )
        if report_id is None:
            print("Error: Failed to save analysis results to database. Historical comparison may be incomplete.", file=sys.stderr)
            # Decide whether to continue or exit
            # sys.exit(1) # Option: Exit if saving fails

        # 5. Load Historical Financial Metrics
        print("\n--- Loading Historical Data ---")
        historical_metrics = get_historical_metrics(db_conn, company_id, args.year, args.quarter)

        # 6. Calculate Comparisons
        # Use the financial summary from the *current* processed data
        current_financials = combined_data.get("pdf_data", {}).get("financial_summary", {})
        comparisons = calculate_comparisons(current_financials, historical_metrics)

        # 7. Prepare Data Bundle for Report Generator
        # Include current data, historical raw values, and calculated comparisons
        report_bundle = {
            "current_data": combined_data,
            "historical_metrics": historical_metrics, # Contains raw values and numeric values for QoQ/YoY
            "comparisons": comparisons # Contains calculated percentages
        }

        # 8. Generate DOCX Report
        pdf_basename = os.path.splitext(os.path.basename(args.pdf_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = os.path.join(args.output_dir, f"{args.ticker.upper()}_FY{args.year}Q{args.quarter}_Analysis_{timestamp}.docx")

        # Call the report generator with the bundled data
        success = create_word_report(report_bundle, output_filename)

        if success:
            print(f"\nAnalysis Complete. Report generated: {output_filename}")
        else:
            print("\nAnalysis completed, but encountered errors during report generation.", file=sys.stderr)

    except Exception as e:
        print(f"\nAn unexpected error occurred during main execution: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        # No sys.exit here, let finally block handle DB closing

    finally:
        # --- Ensure Database Connection is Closed ---
        if db_conn:
            try:
                db_conn.close()
                print("Database connection closed.")
            except sqlite3.Error as e:
                print(f"Error closing database connection: {e}", file=sys.stderr)

    print("="*50)


if __name__ == "__main__":
    main()