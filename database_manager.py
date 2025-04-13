# database_manager.py

import sqlite3
import os
import sys
from datetime import date
import re # For cleaning numeric values

# --- Database Configuration ---
DEFAULT_DB_PATH = "earnings_analysis.db"

def get_db_connection(db_path=None):
    """Establishes connection to the SQLite database."""
    if db_path is None:
        db_path = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row # Return rows as dictionary-like objects
        print(f"Database connection established: {db_path}")
        return conn
    except sqlite3.Error as e:
        print(f"Error connecting to database {db_path}: {e}", file=sys.stderr)
        sys.exit(1)

def initialize_database(conn):
    """Creates necessary tables if they don't exist."""
    cursor = conn.cursor()
    try:
        # Companies Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE NOT NULL,
            name TEXT
        );
        """)

        # Reports Table (One entry per analyzed report)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            report_date DATE NOT NULL, -- Official end date of the quarter/period
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL CHECK(fiscal_quarter IN (1, 2, 3, 4)),
            pdf_filename TEXT,
            transcript_filename TEXT,
            analysis_timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (company_id) REFERENCES companies (id),
            UNIQUE (company_id, fiscal_year, fiscal_quarter) -- Ensure only one report per company/quarter
        );
        """)

        # Financial Metrics Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS financial_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL, -- Store numeric values here (handle None for non-numeric)
            raw_value TEXT,   -- Store original string value (e.g., ranges, "Not Found")
            FOREIGN KEY (report_id) REFERENCES reports (id),
            UNIQUE (report_id, metric_name)
        );
        """)

        # PDF Sections Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdf_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            section_name TEXT NOT NULL,
            section_text TEXT,
            FOREIGN KEY (report_id) REFERENCES reports (id),
            UNIQUE (report_id, section_name)
        );
        """)

        # Transcript Summaries Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS transcript_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            theme_name TEXT NOT NULL,
            summary_text TEXT,
            sentiment TEXT, -- Store extracted sentiment
            FOREIGN KEY (report_id) REFERENCES reports (id),
            UNIQUE (report_id, theme_name)
        );
        """)

        # Executive Quotes Table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS executive_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            role TEXT NOT NULL, -- e.g., CEO, CFO
            quote_text TEXT,
            FOREIGN KEY (report_id) REFERENCES reports (id)
            -- Not unique per role, as multiple quotes might exist
        );
        """)

        conn.commit()
        print("Database initialized successfully (tables created if needed).")
    except sqlite3.Error as e:
        print(f"Error initializing database: {e}", file=sys.stderr)
        conn.rollback() # Rollback changes on error
        sys.exit(1)
    finally:
        cursor.close()

def get_or_create_company(conn, ticker, name=None):
    """Gets the ID of a company by ticker, creating it if it doesn't exist."""
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM companies WHERE ticker = ?", (ticker,))
        result = cursor.fetchone()
        if result:
            return result['id']
        else:
            cursor.execute("INSERT INTO companies (ticker, name) VALUES (?, ?)", (ticker, name if name else ticker))
            conn.commit()
            print(f"Created new company entry for ticker: {ticker}")
            return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Error getting/creating company {ticker}: {e}", file=sys.stderr)
        conn.rollback()
        return None
    finally:
        cursor.close()

def clean_numeric_value(value_str):
    """Attempts to clean and convert a string to a float."""
    if isinstance(value_str, (int, float)):
        return float(value_str)
    if not isinstance(value_str, str):
        return None

    # Handle "Not Found", "N/A", etc.
    if value_str.strip().lower() in ["not found", "n/a", "not calculated", ""]:
        return None
    # Handle percentages
    value_str = value_str.replace('%', '').strip()
    # Handle thousands separators
    value_str = value_str.replace(',', '').strip()
    # Handle ranges (take the first number found, or return None)
    match = re.match(r'^-?([\d\.]+)', value_str)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def save_analysis_results(conn, company_id, fiscal_year, fiscal_quarter, report_date, analysis_data):
    """Saves the complete analysis results to the database."""
    cursor = conn.cursor()
    report_id = None
    try:
        # --- 1. Insert or Update Report Entry ---
        pdf_filename = analysis_data.get('metadata', {}).get('pdf_filename')
        transcript_filename = analysis_data.get('metadata', {}).get('transcript_filename')

        # Use INSERT OR REPLACE to handle reruns for the same quarter
        cursor.execute("""
            INSERT INTO reports (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, fiscal_year, fiscal_quarter) DO UPDATE SET
                report_date=excluded.report_date,
                pdf_filename=excluded.pdf_filename,
                transcript_filename=excluded.transcript_filename,
                analysis_timestamp=CURRENT_TIMESTAMP
            RETURNING id;
        """, (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename))
        report_id = cursor.fetchone()['id']
        print(f"Saved/Updated report entry with ID: {report_id} for FY{fiscal_year} Q{fiscal_quarter}")

        # --- 2. Delete Old Data for this Report ID (to handle updates cleanly) ---
        tables_to_clear = ["financial_metrics", "pdf_sections", "transcript_summaries", "executive_quotes"]
        for table in tables_to_clear:
            cursor.execute(f"DELETE FROM {table} WHERE report_id = ?", (report_id,))
        print(f"Cleared previous data for report ID: {report_id}")

        # --- 3. Insert Financial Metrics ---
        financial_summary = analysis_data.get('pdf_data', {}).get('financial_summary', {})
        metrics_to_insert = []
        for name, raw_value in financial_summary.items():
            numeric_value = clean_numeric_value(raw_value)
            metrics_to_insert.append((report_id, name, numeric_value, str(raw_value)))

        if metrics_to_insert:
            cursor.executemany("""
                INSERT INTO financial_metrics (report_id, metric_name, metric_value, raw_value)
                VALUES (?, ?, ?, ?)
            """, metrics_to_insert)
            print(f"Inserted {len(metrics_to_insert)} financial metrics.")

        # --- 4. Insert PDF Sections ---
        pdf_sections = analysis_data.get('pdf_data', {}).get('extracted_sections', {})
        sections_to_insert = []
        for name, text in pdf_sections.items():
            sections_to_insert.append((report_id, name, text))

        if sections_to_insert:
            cursor.executemany("""
                INSERT INTO pdf_sections (report_id, section_name, section_text)
                VALUES (?, ?, ?)
            """, sections_to_insert)
            print(f"Inserted {len(sections_to_insert)} PDF sections.")

        # --- 5. Insert Transcript Summaries ---
        transcript_summaries = analysis_data.get('transcript_data', {}).get('commentary_summaries', {})
        summaries_to_insert = []
        for theme, summary_text_with_sentiment in transcript_summaries.items():
            sentiment = "N/A"
            summary_body = summary_text_with_sentiment # Default if split fails
            try:
                 lines = summary_text_with_sentiment.split('\n', 1) # Split only once
                 if lines[0].strip().startswith("Sentiment:"):
                     sentiment = lines[0].replace("Sentiment:", "").strip()
                     if len(lines) > 1:
                         summary_body = lines[1].replace("Summary:", "").strip()
                     else:
                         summary_body = "" # Only sentiment was found
                 else:
                      summary_body = summary_text_with_sentiment # No sentiment line found
            except Exception:
                 pass # Keep default values if split fails
            summaries_to_insert.append((report_id, theme, summary_body, sentiment))

        if summaries_to_insert:
            cursor.executemany("""
                INSERT INTO transcript_summaries (report_id, theme_name, summary_text, sentiment)
                VALUES (?, ?, ?, ?)
            """, summaries_to_insert)
            print(f"Inserted {len(summaries_to_insert)} transcript summaries.")

        # --- 6. Insert Executive Quotes ---
        executive_quotes = analysis_data.get('transcript_data', {}).get('executive_quotes', {})
        quotes_to_insert = []
        for role, quote in executive_quotes.items():
            quotes_to_insert.append((report_id, role, quote))

        if quotes_to_insert:
            cursor.executemany("""
                INSERT INTO executive_quotes (report_id, role, quote_text)
                VALUES (?, ?, ?)
            """, quotes_to_insert)
            print(f"Inserted {len(quotes_to_insert)} executive quotes.")

        # --- Commit All Changes ---
        conn.commit()
        print(f"Successfully saved all analysis results for report ID: {report_id}")
        return report_id

    except sqlite3.Error as e:
        print(f"Error saving analysis results: {e}", file=sys.stderr)
        conn.rollback()
        return None
    finally:
        cursor.close()


def get_historical_metrics(conn, company_id, current_fy, current_fq):
    """
    Retrieves key financial metrics for the previous quarter and the same quarter last year.
    """
    cursor = conn.cursor()
    historical_data = {
        "qoq": {}, # Quarter over Quarter (Previous Quarter)
        "yoy": {}  # Year over Year (Same Quarter Last Year)
    }

    # Calculate previous quarter and year-ago quarter/year
    # Previous Quarter
    prev_fq = current_fq - 1
    prev_fy = current_fy
    if prev_fq == 0:
        prev_fq = 4
        prev_fy -= 1

    # Year Ago Quarter
    yoy_fq = current_fq
    yoy_fy = current_fy - 1

    periods = {
        "qoq": (prev_fy, prev_fq),
        "yoy": (yoy_fy, yoy_fq)
    }

    print(f"Fetching historical data for QoQ (FY{prev_fy} Q{prev_fq}) and YoY (FY{yoy_fy} Q{yoy_fq})...")

    for period_key, (fy, fq) in periods.items():
        try:
            cursor.execute("""
                SELECT fm.metric_name, fm.metric_value, fm.raw_value
                FROM financial_metrics fm
                JOIN reports r ON fm.report_id = r.id
                WHERE r.company_id = ? AND r.fiscal_year = ? AND r.fiscal_quarter = ?
            """, (company_id, fy, fq))
            results = cursor.fetchall()
            if results:
                for row in results:
                    historical_data[period_key][row['metric_name']] = {
                        'value': row['metric_value'], # Numeric value
                        'raw': row['raw_value']     # Original text value
                    }
                print(f"  Found {len(results)} metrics for {period_key.upper()} period (FY{fy} Q{fq}).")
            else:
                 print(f"  No data found for {period_key.upper()} period (FY{fy} Q{fq}).")

        except sqlite3.Error as e:
            print(f"Error fetching historical data for {period_key.upper()} (FY{fy} Q{fq}): {e}", file=sys.stderr)
            # Continue to try fetching the other period

    cursor.close()
    return historical_data


def get_comparison_data(conn, tickers, fiscal_year, fiscal_quarter):
    """
    Retrieves financial metrics and transcript summaries for a list of companies
    for a specific fiscal year and quarter.
    """
    cursor = conn.cursor()
    comparison_data = {}
    valid_report_ids = {} # Store report_id per ticker

    print(f"Fetching comparison data for {tickers} for FY{fiscal_year} Q{fiscal_quarter}...")

    # 1. Get company IDs and report IDs for the requested period
    for ticker in tickers:
        ticker = ticker.upper()
        comparison_data[ticker] = {"report_id": None, "financials": {}, "summaries": {}} # Initialize structure
        try:
            cursor.execute("""
                SELECT r.id, c.id as company_id
                FROM reports r
                JOIN companies c ON r.company_id = c.id
                WHERE c.ticker = ? AND r.fiscal_year = ? AND r.fiscal_quarter = ?
            """, (ticker, fiscal_year, fiscal_quarter))
            result = cursor.fetchone()
            if result:
                report_id = result['id']
                comparison_data[ticker]["report_id"] = report_id
                valid_report_ids[ticker] = report_id
                print(f"  Found report ID {report_id} for {ticker}.")
            else:
                print(f"  Warning: No report found for {ticker} for FY{fiscal_year} Q{fiscal_quarter}.")
                # Keep the empty structure for this ticker in comparison_data
        except sqlite3.Error as e:
            print(f"Error fetching report ID for {ticker}: {e}", file=sys.stderr)

    report_id_list = list(valid_report_ids.values())
    if not report_id_list:
        print("  No valid reports found for any requested ticker in this period.")
        cursor.close()
        return comparison_data # Return initialized structure with Nones

    # Create placeholders for the SQL IN clause
    placeholders = ','.join('?' * len(report_id_list))

    # 2. Fetch Financial Metrics for all valid reports
    try:
        cursor.execute(f"""
            SELECT c.ticker, fm.metric_name, fm.metric_value, fm.raw_value
            FROM financial_metrics fm
            JOIN reports r ON fm.report_id = r.id
            JOIN companies c ON r.company_id = c.id
            WHERE fm.report_id IN ({placeholders})
        """, report_id_list)
        metrics = cursor.fetchall()
        for row in metrics:
            ticker = row['ticker']
            metric_name = row['metric_name']
            # Ensure the ticker structure exists (it should from step 1)
            if ticker in comparison_data:
                 comparison_data[ticker]["financials"][metric_name] = {
                     'value': row['metric_value'],
                     'raw': row['raw_value']
                 }
        print(f"  Fetched {len(metrics)} financial metrics for relevant reports.")
    except sqlite3.Error as e:
         print(f"Error fetching financial metrics: {e}", file=sys.stderr)

    # 3. Fetch Transcript Summaries for all valid reports
    try:
        cursor.execute(f"""
            SELECT c.ticker, ts.theme_name, ts.summary_text, ts.sentiment
            FROM transcript_summaries ts
            JOIN reports r ON ts.report_id = r.id
            JOIN companies c ON r.company_id = c.id
            WHERE ts.report_id IN ({placeholders})
        """, report_id_list)
        summaries = cursor.fetchall()
        for row in summaries:
             ticker = row['ticker']
             theme_name = row['theme_name']
             if ticker in comparison_data:
                 comparison_data[ticker]["summaries"][theme_name] = {
                     'text': row['summary_text'],
                     'sentiment': row['sentiment']
                 }
        print(f"  Fetched {len(summaries)} transcript summaries for relevant reports.")
    except sqlite3.Error as e:
         print(f"Error fetching transcript summaries: {e}", file=sys.stderr)

    # (Optional: Fetch PDF sections like 'Outlook' similarly if needed)

    cursor.close()
    return comparison_data

def get_analysis_history(conn, ticker=None, year=None, quarter=None):
    """
    Fetches a list of analyzed reports based on filters.
    """
    cursor = conn.cursor()
    query = """
        SELECT c.ticker, r.fiscal_year, r.fiscal_quarter, r.report_date,
               strftime('%Y-%m-%d %H:%M:%S', r.analysis_timestamp) as analysis_timestamp, -- Format timestamp
               r.pdf_filename, r.transcript_filename, r.id as report_id
        FROM reports r
        JOIN companies c ON r.company_id = c.id
        WHERE 1=1
    """
    params = []
    if ticker and ticker.strip(): # Check if ticker is not empty
        query += " AND UPPER(c.ticker) = ?" # Case-insensitive search
        params.append(ticker.strip().upper())
    if year:
        query += " AND r.fiscal_year = ?"
        params.append(year)
    if quarter:
        query += " AND r.fiscal_quarter = ?"
        params.append(quarter)

    query += " ORDER BY r.analysis_timestamp DESC" # Show most recent first

    try:
        print(f"Fetching history with query: {query} and params: {params}") # Debug print
        cursor.execute(query, params)
        results = cursor.fetchall()
        # Convert sqlite3.Row objects to a list of dictionaries for DataFrame
        history_list = [dict(row) for row in results]
        history_df = pd.DataFrame(history_list)
        # Reorder columns for potentially better display if needed
        if not history_df.empty:
             cols_order = ['ticker', 'fiscal_year', 'fiscal_quarter', 'report_date', 'analysis_timestamp', 'pdf_filename', 'transcript_filename', 'report_id']
             # Filter out columns not present (in case of schema changes)
             cols_order = [col for col in cols_order if col in history_df.columns]
             history_df = history_df[cols_order]
        return history_df
    except Exception as e:
         print(f"Error fetching history from database: {e}", file=sys.stderr) # Log error
         st.error(f"Error fetching history from database: {e}") # Show error in UI too
         return pd.DataFrame() # Return empty DataFrame on error
    finally:
        cursor.close()

# --- Example Usage (for testing this module) ---
if __name__ == "__main__":
    print("Testing database_manager...")
    db_conn = get_db_connection("test_db.sqlite") # Use a test DB file
    initialize_database(db_conn)

    # Test company creation
    comp_id = get_or_create_company(db_conn, "TESTCO", "Test Company Inc.")
    print(f"Company ID for TESTCO: {comp_id}")
    comp_id_again = get_or_create_company(db_conn, "TESTCO")
    print(f"Company ID for TESTCO (again): {comp_id_again}")

    # Test saving dummy data
    dummy_analysis = {
         "metadata": {"pdf_filename": "test.pdf", "transcript_filename": "test.docx"},
         "pdf_data": {
             "financial_summary": {"Revenue ($M) Q3": "1000", "Operating Margin (%) Q3": "20.5", "Invalid Metric": "N/A"},
             "extracted_sections": {"Outlook": "Looks good."},
         },
         "transcript_data": {
             "commentary_summaries": {"Demand": "Sentiment: Positive\nSummary: Strong."},
             "executive_quotes": {"CEO": "Great quarter!"}
         }
    }
    report_date = date(2024, 9, 30) # Example Q3 end date
    save_analysis_results(db_conn, comp_id, 2024, 3, report_date, dummy_analysis)

    # Test retrieving historical (won't find much yet)
    hist = get_historical_metrics(db_conn, comp_id, 2024, 3)
    print("Historical Data Retrieved:", hist)

    # Test comparison data retrieval (assuming TESTCO and NEWCO exist and have data for FY2024 Q3)
    print("\n--- Testing Comparison Data Retrieval ---")
    # Create another company and save data for it for the same period
    new_comp_id = get_or_create_company(db_conn, "NEWCO", "New Company Ltd.")
    if new_comp_id:
         dummy_analysis_newco = { # Slightly different data
              "metadata": {"pdf_filename": "new.pdf", "transcript_filename": "new.docx"},
              "pdf_data": {
                  "financial_summary": {"Revenue ($M) Q3": "950", "Operating Margin (%) Q3": "22.1"},
                  "extracted_sections": {"Outlook": "Cautious outlook."},
              },
              "transcript_data": {
                  "commentary_summaries": {"Demand": "Sentiment: Neutral\nSummary: Mixed signals."},
                  "executive_quotes": {"CEO": "Challenging quarter."}
              }
         }
         save_analysis_results(db_conn, new_comp_id, 2024, 3, report_date, dummy_analysis_newco)

         comp_data = get_comparison_data(db_conn, ["TESTCO", "NEWCO", "MISSINGCO"], 2024, 3)
         print("\nComparison Data Retrieved:")
         import json
         print(json.dumps(comp_data, indent=2)) # Pretty print the complex dict
    else:
         print("Skipping comparison test as second company creation failed.")

    print("\n--- Testing History Retrieval ---")
    history = get_analysis_history(db_conn, ticker="TESTCO")
    print("History for TESTCO:")
    print(history)

    history_all = get_analysis_history(db_conn)
    print("\nAll History:")
    print(history_all)

    db_conn.close()
    print("Test finished. Check 'test_db.sqlite'.")
    # os.remove("test_db.sqlite")
