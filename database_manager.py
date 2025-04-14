# database_manager.py

import sqlite3
import psycopg2 # For PostgreSQL
# Optional: Use DictCursor for dictionary-like rows from psycopg2 if preferred
# import psycopg2.extras
import os
import sys
from datetime import date
import re
import pandas as pd # Needed for get_analysis_history return type

# --- Database Configuration ---
DEFAULT_DB_PATH = "earnings_analysis.db" # Default SQLite path

def get_db_connection(db_path=None):
    """
    Establishes connection to PostgreSQL (using NEON_CONNECTION_STRING env var)
    or falls back to SQLite.
    Returns a tuple: (connection_object, db_type_string)
    """
    neon_conn_string = os.getenv("NEON_CONNECTION_STRING")

    # --- Try PostgreSQL First ---
    if neon_conn_string:
        try:
            conn = psycopg2.connect(neon_conn_string)
            # Optional: Use DictCursor
            # Needs: import psycopg2.extras
            # cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            print("PostgreSQL (Neon) database connection established.")
            return conn, 'postgres' # Return connection and type 'postgres'
        except psycopg2.Error as e:
            print(f"Warning: Error connecting to PostgreSQL database: {e}", file=sys.stderr)
            print("Falling back to local SQLite.")
        except ImportError:
            print("Warning: psycopg2 driver not installed. Falling back to SQLite.", file=sys.stderr)
        except Exception as e: # Catch other potential connection errors
             print(f"Warning: Unexpected error connecting to PostgreSQL: {e}", file=sys.stderr)
             print("Falling back to local SQLite.")


    # --- SQLite Fallback ---
    if db_path is None:
        db_path = os.getenv("DATABASE_PATH", DEFAULT_DB_PATH)
    try:
        conn_sqlite = sqlite3.connect(db_path)
        conn_sqlite.row_factory = sqlite3.Row # Use Row factory for dictionary-like access
        print(f"SQLite database connection established: {db_path}")
        return conn_sqlite, 'sqlite' # Return connection and type 'sqlite'
    except sqlite3.Error as e:
        print(f"CRITICAL Error connecting to SQLite database {db_path}: {e}", file=sys.stderr)
        sys.exit(1) # Exit if even SQLite fails

def initialize_database(conn, db_type='sqlite'):
    """Creates necessary tables if they don't exist (SQL adjusted for PG/SQLite)."""
    # Use SERIAL PRIMARY KEY for PostgreSQL auto-increment, standard TEXT type
    autoincrement_pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if db_type == 'sqlite' else "SERIAL PRIMARY KEY"
    timestamp_default = "DATETIME DEFAULT CURRENT_TIMESTAMP" if db_type == 'sqlite' else "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP" # Use TZ for PG
    text_type = "TEXT"
    real_type = "REAL" # Works for both
    date_type = "DATE" # Works for both
    integer_type = "INTEGER" # Works for both

    print(f"Initializing database ({db_type})...")
    try:
        # Use 'with' statement for cursor management (automatically closes)
        with conn.cursor() as cursor:
            # Companies Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS companies (
                id {autoincrement_pk},
                ticker {text_type} UNIQUE NOT NULL,
                name {text_type}
            );
            """)
            # Reports Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS reports (
                id {autoincrement_pk},
                company_id {integer_type} NOT NULL,
                report_date {date_type} NOT NULL,
                fiscal_year {integer_type} NOT NULL,
                fiscal_quarter {integer_type} NOT NULL CHECK(fiscal_quarter IN (1, 2, 3, 4)),
                pdf_filename {text_type},
                transcript_filename {text_type},
                analysis_timestamp {timestamp_default},
                FOREIGN KEY (company_id) REFERENCES companies (id) ON DELETE CASCADE, -- Added ON DELETE CASCADE
                UNIQUE (company_id, fiscal_year, fiscal_quarter)
            );
            """)
            # Financial Metrics Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS financial_metrics (
                id {autoincrement_pk},
                report_id {integer_type} NOT NULL,
                metric_name {text_type} NOT NULL,
                metric_value {real_type},
                raw_value {text_type},
                FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE, -- Added ON DELETE CASCADE
                UNIQUE (report_id, metric_name)
            );
            """)
            # PDF Sections Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS pdf_sections (
                id {autoincrement_pk},
                report_id {integer_type} NOT NULL,
                section_name {text_type} NOT NULL,
                section_text {text_type},
                FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE, -- Added ON DELETE CASCADE
                UNIQUE (report_id, section_name)
            );
            """)
            # Transcript Summaries Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS transcript_summaries (
                id {autoincrement_pk},
                report_id {integer_type} NOT NULL,
                theme_name {text_type} NOT NULL,
                summary_text {text_type},
                sentiment {text_type},
                FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE, -- Added ON DELETE CASCADE
                UNIQUE (report_id, theme_name)
            );
            """)
            # Executive Quotes Table
            cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS executive_quotes (
                id {autoincrement_pk},
                report_id {integer_type} NOT NULL,
                role {text_type} NOT NULL,
                quote_text {text_type},
                FOREIGN KEY (report_id) REFERENCES reports (id) ON DELETE CASCADE -- Added ON DELETE CASCADE
                -- No unique constraint needed here
            );
            """)
        conn.commit() # Commit changes if all statements succeeded
        print("Database initialized successfully.")
    except (sqlite3.Error, psycopg2.Error) as e:
        print(f"Error initializing database: {e}", file=sys.stderr)
        conn.rollback() # Rollback on error
        raise # Re-raise the exception for calling code to handle

def get_or_create_company(conn, db_type, ticker, name=None):
    """Gets the ID of a company by ticker, creating it if needed."""
    placeholder = "?" if db_type == 'sqlite' else "%s"
    ticker = ticker.strip().upper() # Ensure consistency
    if not ticker: raise ValueError("Ticker cannot be empty")

    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SELECT id FROM companies WHERE ticker = {placeholder}", (ticker,))
            result = cursor.fetchone()
            if result:
                # SQLite Row object allows access by index or name
                return result[0] if isinstance(result, tuple) else result['id']
            else:
                cursor.execute(f"INSERT INTO companies (ticker, name) VALUES ({placeholder}, {placeholder})", (ticker, name if name else ticker))
                conn.commit()
                if db_type == 'sqlite':
                    last_id = cursor.lastrowid
                else: # PostgreSQL requires RETURNING id or separate SELECT
                    # Assuming SERIAL updates sequence correctly, fetch last value
                    # More robust might be RETURNING id in the INSERT statement if driver supports it easily
                    cursor.execute("SELECT lastval();") # Get the last value generated by a sequence in the session
                    last_id = cursor.fetchone()[0]
                print(f"Created new company entry for ticker: {ticker} with ID: {last_id}")
                return last_id
    except (sqlite3.Error, psycopg2.Error) as e:
        print(f"Error getting/creating company {ticker}: {e}", file=sys.stderr)
        conn.rollback()
        return None

def clean_numeric_value(value_str):
    """Attempts to clean and convert a string to a float."""
    if isinstance(value_str, (int, float)): return float(value_str)
    if not isinstance(value_str, str): return None
    value_str = str(value_str).strip().lower()
    if value_str in ["not found", "n/a", "not calculated", "error", "inf", ""]: return None
    multiplier = 1.0
    if 'billion' in value_str or 'bn' in value_str: multiplier = 1_000_000_000
    elif 'million' in value_str or 'mn' in value_str or 'm' in value_str:
         if 'b' not in value_str: multiplier = 1_000_000
    # Remove currency symbols, commas, units, percentages AFTER checking units
    value_str = re.sub(r'[$,%a-z]', '', value_str).strip()
    # Handle potential ranges (take the first number)
    match = re.match(r'^-?([\d,\.]+)', value_str) # Allow comma in regex match
    if match:
        try:
            numeric_part = float(match.group(1).replace(',','')) # Remove comma before float conversion
            # Apply multiplier only if it was detected based on units
            return numeric_part * multiplier if multiplier != 1.0 else numeric_part
        except ValueError: return None
    return None

def save_analysis_results(conn, db_type, company_id, fiscal_year, fiscal_quarter, report_date, analysis_data):
    """Saves the complete analysis results to the database."""
    placeholder = "?" if db_type == 'sqlite' else "%s"
    report_id = None
    try:
        with conn.cursor() as cursor:
            # --- 1. Insert or Update Report Entry ---
            pdf_filename = analysis_data.get('metadata', {}).get('pdf_filename')
            transcript_filename = analysis_data.get('metadata', {}).get('transcript_filename')

            if db_type == 'sqlite':
                # SQLite specific ON CONFLICT
                cursor.execute(f"""
                    INSERT INTO reports (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, fiscal_year, fiscal_quarter) DO UPDATE SET
                        report_date=excluded.report_date, pdf_filename=excluded.pdf_filename,
                        transcript_filename=excluded.transcript_filename, analysis_timestamp=CURRENT_TIMESTAMP;
                """, (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename))
                # Get report_id after insert/update for SQLite
                cursor.execute(f"""SELECT id FROM reports WHERE company_id = ? AND fiscal_year = ? AND fiscal_quarter = ?""",
                               (company_id, fiscal_year, fiscal_quarter))
                result = cursor.fetchone()
                report_id = result[0] if result else None
            else: # PostgreSQL specific ON CONFLICT
                cursor.execute(f"""
                    INSERT INTO reports (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (company_id, fiscal_year, fiscal_quarter) DO UPDATE SET
                        report_date=excluded.report_date, pdf_filename=excluded.pdf_filename,
                        transcript_filename=excluded.transcript_filename, analysis_timestamp=CURRENT_TIMESTAMP
                    RETURNING id;
                """, (company_id, fiscal_year, fiscal_quarter, report_date, pdf_filename, transcript_filename))
                result = cursor.fetchone() # Get returned ID
                report_id = result[0] if result else None

            if report_id is None:
                raise Exception("Failed to get report ID after insert/update.")

            print(f"Saved/Updated report entry with ID: {report_id} for FY{fiscal_year} Q{fiscal_quarter}")

            # --- 2. Delete Old Data for this Report ID ---
            tables_to_clear = ["financial_metrics", "pdf_sections", "transcript_summaries", "executive_quotes"]
            for table in tables_to_clear:
                cursor.execute(f"DELETE FROM {table} WHERE report_id = {placeholder}", (report_id,))
            print(f"Cleared previous data for report ID: {report_id}")

            # --- 3. Insert Financial Metrics ---
            financial_summary = analysis_data.get('pdf_data', {}).get('financial_summary', {})
            metrics_to_insert = []
            for name, raw_value in financial_summary.items():
                numeric_value = clean_numeric_value(raw_value)
                metrics_to_insert.append((report_id, name, numeric_value, str(raw_value)))
            if metrics_to_insert:
                sql = f"INSERT INTO financial_metrics (report_id, metric_name, metric_value, raw_value) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})"
                cursor.executemany(sql, metrics_to_insert)
                print(f"Inserted {len(metrics_to_insert)} financial metrics.")

            # --- 4. Insert PDF Sections ---
            pdf_sections = analysis_data.get('pdf_data', {}).get('extracted_sections', {})
            sections_to_insert = [(report_id, name, text) for name, text in pdf_sections.items()]
            if sections_to_insert:
                 sql = f"INSERT INTO pdf_sections (report_id, section_name, section_text) VALUES ({placeholder}, {placeholder}, {placeholder})"
                 cursor.executemany(sql, sections_to_insert)
                 print(f"Inserted {len(sections_to_insert)} PDF sections.")

            # --- 5. Insert Transcript Summaries ---
            transcript_summaries = analysis_data.get('transcript_data', {}).get('commentary_summaries', {})
            summaries_to_insert = []
            for theme, summary_text_with_sentiment in transcript_summaries.items():
                sentiment, summary_body = "N/A", summary_text_with_sentiment
                try: # Simple parsing logic
                     lines = summary_text_with_sentiment.split('\n', 1)
                     if lines[0].strip().startswith("Sentiment:"):
                         sentiment = lines[0].replace("Sentiment:", "").strip()
                         if len(lines) > 1: summary_body = lines[1].replace("Summary:", "").strip()
                         else: summary_body = ""
                except Exception: pass
                summaries_to_insert.append((report_id, theme, summary_body, sentiment))
            if summaries_to_insert:
                sql = f"INSERT INTO transcript_summaries (report_id, theme_name, summary_text, sentiment) VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})"
                cursor.executemany(sql, summaries_to_insert)
                print(f"Inserted {len(summaries_to_insert)} transcript summaries.")

            # --- 6. Insert Executive Quotes ---
            executive_quotes = analysis_data.get('transcript_data', {}).get('executive_quotes', {})
            quotes_to_insert = [(report_id, role, quote) for role, quote in executive_quotes.items()]
            if quotes_to_insert:
                 sql = f"INSERT INTO executive_quotes (report_id, role, quote_text) VALUES ({placeholder}, {placeholder}, {placeholder})"
                 cursor.executemany(sql, quotes_to_insert)
                 print(f"Inserted {len(quotes_to_insert)} executive quotes.")

        conn.commit() # Commit transaction
        print(f"Successfully saved all analysis results for report ID: {report_id}")
        return report_id

    except (sqlite3.Error, psycopg2.Error, Exception) as e: # Catch generic Exception too
        print(f"Error saving analysis results: {e}", file=sys.stderr)
        conn.rollback() # Rollback on any error during the process
        return None

def get_historical_metrics(conn, db_type, company_id, current_fy, current_fq):
    """Retrieves metrics for QoQ and YoY comparison."""
    placeholder = "?" if db_type == 'sqlite' else "%s"
    historical_data = {"qoq": {}, "yoy": {}}
    prev_fq, prev_fy = (current_fq - 1, current_fy) if current_fq > 1 else (4, current_fy - 1)
    yoy_fq, yoy_fy = current_fq, current_fy - 1
    periods = {"qoq": (prev_fy, prev_fq), "yoy": (yoy_fy, yoy_fq)}

    print(f"Fetching historical data for QoQ (FY{prev_fy} Q{prev_fq}) and YoY (FY{yoy_fy} Q{yoy_fq})...")
    try:
        with conn.cursor() as cursor:
            for period_key, (fy, fq) in periods.items():
                cursor.execute(f"""
                    SELECT fm.metric_name, fm.metric_value, fm.raw_value
                    FROM financial_metrics fm
                    JOIN reports r ON fm.report_id = r.id
                    WHERE r.company_id = {placeholder} AND r.fiscal_year = {placeholder} AND r.fiscal_quarter = {placeholder}
                """, (company_id, fy, fq))
                results = cursor.fetchall()
                if results:
                    colnames = [desc[0] for desc in cursor.description]
                    for row_tuple in results:
                        row_dict = dict(zip(colnames, row_tuple))
                        metric_name = row_dict['metric_name']
                        historical_data[period_key][metric_name] = {'value': row_dict['metric_value'], 'raw': row_dict['raw_value']}
                    print(f"  Found {len(results)} metrics for {period_key.upper()} period (FY{fy} Q{fq}).")
                else:
                     print(f"  No data found for {period_key.upper()} period (FY{fy} Q{fq}).")
    except (sqlite3.Error, psycopg2.Error) as e:
        print(f"Error fetching historical data: {e}", file=sys.stderr)

    return historical_data

def get_comparison_data(conn, db_type, tickers, fiscal_year, fiscal_quarter):
    """Retrieves data for multiple companies for a specific period."""
    placeholder = "?" if db_type == 'sqlite' else "%s"
    comparison_data = {}
    valid_report_ids = {}
    ticker_map = {t.upper(): None for t in tickers}

    print(f"Fetching comparison data for {tickers} for FY{fiscal_year} Q{fiscal_quarter}...")
    try:
        with conn.cursor() as cursor:
            # 1. Get company IDs and report IDs
            for ticker in ticker_map.keys():
                comparison_data[ticker] = {"report_id": None, "financials": {}, "summaries": {}}
                cursor.execute(f"""
                    SELECT r.id, c.id as company_id
                    FROM reports r JOIN companies c ON r.company_id = c.id
                    WHERE c.ticker = {placeholder} AND r.fiscal_year = {placeholder} AND r.fiscal_quarter = {placeholder}
                """, (ticker, fiscal_year, fiscal_quarter))
                result = cursor.fetchone()
                if result:
                    report_id = result[0]; company_id = result[1]
                    comparison_data[ticker]["report_id"] = report_id
                    valid_report_ids[ticker] = report_id
                    ticker_map[ticker] = company_id
                    print(f"  Found report ID {report_id} for {ticker}.")
                else:
                    print(f"  Warning: No report found for {ticker} for FY{fiscal_year} Q{fiscal_quarter}.")

            report_id_list = list(valid_report_ids.values())
            if not report_id_list:
                print("  No valid reports found for any requested ticker in this period.")
                return comparison_data

            # Create correct placeholders string for IN clause
            in_placeholders = ','.join([placeholder] * len(report_id_list))

            # 2. Fetch Financial Metrics
            cursor.execute(f"""
                SELECT c.ticker, fm.metric_name, fm.metric_value, fm.raw_value
                FROM financial_metrics fm
                JOIN reports r ON fm.report_id = r.id JOIN companies c ON r.company_id = c.id
                WHERE fm.report_id IN ({in_placeholders})
            """, tuple(report_id_list))
            metrics = cursor.fetchall()
            colnames_fin = [desc[0] for desc in cursor.description]
            for row_tuple in metrics:
                row_dict = dict(zip(colnames_fin, row_tuple))
                ticker = row_dict['ticker']
                if ticker in comparison_data:
                     comparison_data[ticker]["financials"][row_dict['metric_name']] = {'value': row_dict['metric_value'],'raw': row_dict['raw_value']}
            print(f"  Fetched {len(metrics)} financial metrics.")

            # 3. Fetch Transcript Summaries
            cursor.execute(f"""
                SELECT c.ticker, ts.theme_name, ts.summary_text, ts.sentiment
                FROM transcript_summaries ts
                JOIN reports r ON ts.report_id = r.id JOIN companies c ON r.company_id = c.id
                WHERE ts.report_id IN ({in_placeholders})
            """, tuple(report_id_list))
            summaries = cursor.fetchall()
            colnames_sum = [desc[0] for desc in cursor.description]
            for row_tuple in summaries:
                 row_dict = dict(zip(colnames_sum, row_tuple))
                 ticker = row_dict['ticker']
                 if ticker in comparison_data:
                     comparison_data[ticker]["summaries"][row_dict['theme_name']] = {'text': row_dict['summary_text'], 'sentiment': row_dict['sentiment']}
            print(f"  Fetched {len(summaries)} transcript summaries.")

    except (sqlite3.Error, psycopg2.Error) as e:
        print(f"Error fetching comparison data: {e}", file=sys.stderr)

    return comparison_data

def get_analysis_history(conn, db_type, ticker=None, year=None, quarter=None):
    """Fetches a list of analyzed reports based on filters."""
    placeholder = "?" if db_type == 'sqlite' else "%s"
    history_df = pd.DataFrame() # Initialize empty
    try:
        with conn.cursor() as cursor:
            # Use COALESCE for potentially null filenames
            query = f"""
                SELECT c.ticker, r.fiscal_year, r.fiscal_quarter, r.report_date,
                       r.analysis_timestamp, -- Get raw timestamp
                       COALESCE(r.pdf_filename, 'N/A') as pdf_filename,
                       COALESCE(r.transcript_filename, 'N/A') as transcript_filename,
                       r.id as report_id
                FROM reports r
                JOIN companies c ON r.company_id = c.id
                WHERE 1=1
            """
            params = []
            if ticker and ticker.strip():
                query += f" AND UPPER(c.ticker) = {placeholder}"
                params.append(ticker.strip().upper())
            if year:
                query += f" AND r.fiscal_year = {placeholder}"
                params.append(year)
            if quarter:
                query += f" AND r.fiscal_quarter = {placeholder}"
                params.append(quarter)

            query += " ORDER BY r.analysis_timestamp DESC"

            print(f"Fetching history with query: {query} and params: {params}") # Debug print
            cursor.execute(query, tuple(params))
            results = cursor.fetchall()
            colnames = [desc[0] for desc in cursor.description]
            history_list = [dict(zip(colnames, row)) for row in results]
            history_df = pd.DataFrame(history_list)

            # Format timestamp column after creating DataFrame
            if 'analysis_timestamp' in history_df.columns:
                 # Convert to pandas datetime, handling potential timezone differences
                 history_df['analysis_timestamp'] = pd.to_datetime(history_df['analysis_timestamp'], utc=True).dt.tz_convert(None) # Convert to naive UTC then remove tz
                 history_df['analysis_timestamp'] = history_df['analysis_timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')


             # Reorder columns
            if not history_df.empty:
                 cols_order = ['ticker', 'fiscal_year', 'fiscal_quarter', 'report_date', 'analysis_timestamp', 'pdf_filename', 'transcript_filename', 'report_id']
                 cols_order = [col for col in cols_order if col in history_df.columns]
                 history_df = history_df[cols_order]

    except (sqlite3.Error, psycopg2.Error, Exception) as e: # Catch generic Exception
         print(f"Error fetching history from database: {e}", file=sys.stderr)
         # Raise the error so Streamlit can display it
         raise e

    return history_df