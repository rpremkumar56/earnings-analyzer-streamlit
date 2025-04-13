# pdf_processor.py

import fitz  # PyMuPDF
import tabula
import pandas as pd
import re
import sys
import io
import os
import google.generativeai as genai # Import Gemini
import time

# --- Gemini Configuration & Helper (Keep as before) ---
def generate_gemini_content(prompt, context="PDF Processing", retries=3, delay=5):
    """Generates content using the Gemini API with error handling and retries."""
    print(f"  Calling Gemini API for {context}...")
    MODEL_NAME = "gemini-1.5-flash"
    SAFETY_SETTINGS = [{"category": c, "threshold": "BLOCK_MEDIUM_AND_ABOVE"} for c in ["HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT"]]
    GENERATION_CONFIG = {"temperature": 0.3, "top_p": 0.95, "top_k": 64, "max_output_tokens": 4096, "response_mime_type": "text/plain"}
    model = genai.GenerativeModel(model_name=MODEL_NAME, safety_settings=SAFETY_SETTINGS, generation_config=GENERATION_CONFIG)
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response and response.candidates and response.candidates[0].content.parts:
                 print(f"  Gemini API call successful for {context}.")
                 return response.text.strip()
            else:
                block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                safety_ratings = response.candidates[0].safety_ratings if response.candidates else 'N/A'
                print(f"  Warning ({context}): Gemini response blocked or empty. Reason: {block_reason}. Ratings: {safety_ratings}", file=sys.stderr)
                return f"Error: Response blocked or empty (Reason: {block_reason})"
        except Exception as e:
            print(f"  Error calling Gemini API ({context}, Attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"  Max retries reached for {context}. Skipping this request.", file=sys.stderr)
                return f"Error: API call failed after multiple retries ({e})"
    return "Error: Max retries reached."

# --- Text and Table Extraction (Keep as before) ---
def extract_text_and_pages(pdf_path):
    """Extracts text page by page and full text."""
    pages_text = []
    full_text = ""
    try:
        doc = fitz.open(pdf_path)
        print(f"Reading text from {len(doc)} pages in {pdf_path}...")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text", sort=True) + "\n"
            pages_text.append(page_text)
            full_text += page_text
        doc.close()
        print(f"Successfully extracted text (total {len(full_text)} chars).")
        return full_text, pages_text
    except Exception as e:
        print(f"Error extracting text from PDF {pdf_path}: {e}", file=sys.stderr)
        return None, None

def extract_tables_from_pdf(pdf_path):
    """Extracts tables using Tabula (no change here)."""
    print(f"Attempting to extract tables from {pdf_path} using Tabula...")
    all_tables = []
    try:
        # Important: Use guess=False and potentially stream=True if lattice fails badly on headers
        # Or adjust area if tables are consistently located
        tables_lattice = tabula.read_pdf(pdf_path, pages='all', multiple_tables=True, lattice=True, guess=False, silent=True, pandas_options={'header': None})
        if tables_lattice:
            all_tables.extend(tables_lattice)
            print(f"  Found {len(tables_lattice)} tables using lattice mode.")

        # Try stream mode as a fallback if lattice finds nothing or if specifically needed
        if not all_tables:
             print("  Lattice found no tables, trying stream mode...")
             tables_stream = tabula.read_pdf(pdf_path, pages='all', multiple_tables=True, stream=True, guess=False, silent=True, pandas_options={'header': None})
             if tables_stream:
                  all_tables.extend(tables_stream)
                  print(f"  Found {len(tables_stream)} tables using stream mode.")

        if not all_tables:
            print("  No tables found by Tabula (lattice or stream).")
        else:
            # Clean empty tables or tables with only one column (often parsing errors)
            all_tables = [df for df in all_tables if isinstance(df, pd.DataFrame) and not df.empty and not df.isnull().all().all() and df.shape[1] > 1]
            print(f"Total non-empty tables extracted (more than 1 col): {len(all_tables)}")
        return all_tables
    except Exception as e:
        print(f"Error extracting tables from PDF {pdf_path} with Tabula: {e}", file=sys.stderr)
        print("  Ensure Java is installed, JAVA_HOME is set, and JPype1 is installed.", file=sys.stderr)
        return []

# --- NEW: Table Preprocessing Function ---
def preprocess_financial_table(df_raw):
    """Attempts to clean headers and remove junk rows from extracted financial tables."""
    print("  Attempting to preprocess extracted financial table...")
    df = df_raw.copy()
    try:
        # 1. Drop fully empty columns first
        df = df.dropna(axis=1, how='all')
        if df.empty or df.shape[1] < 2: # Need at least label + value
             print("    Table empty or too narrow after dropping empty columns.")
             return None

        # 2. Detect Header End Row (Improved Heuristic)
        header_end_row = -1
        potential_data_start_keyword = ['revenue', 'cost of', 'gross margin', 'gross profit']
        max_header_rows = 4 # Limit how many rows can be considered header

        for i in range(min(max_header_rows, df.shape[0])):
            row_text = ' '.join(df.iloc[i].astype(str)).lower()
            # If row contains a likely first data item, the previous row was the last header row
            if any(keyword in row_text for keyword in potential_data_start_keyword):
                header_end_row = i - 1
                break
            # If we haven't found data start by max_header_rows, assume header is shorter
            if i == max_header_rows - 1 and header_end_row == -1:
                 header_end_row = 0 # Default to just the first row as header

        if header_end_row < 0 and df.shape[0] > 1 : # If no data keywords found, maybe header is just row 0
             header_end_row = 0
        elif header_end_row < 0: # Single row table?
             print("    Table has only one row, cannot preprocess header.")
             return None # Treat as unusable

        print(f"    Detected header potentially ending at row index: {header_end_row}")

        # 3. Combine Header Rows (handle NaNs better)
        new_headers = []
        if header_end_row >= 0:
            # Combine header rows intelligently - join non-NaN values vertically
            num_cols = df.shape[1]
            temp_headers = [[''] * num_cols for _ in range(header_end_row + 1)]
            for r in range(header_end_row + 1):
                for c in range(num_cols):
                     if pd.notna(df.iat[r, c]):
                          temp_headers[r][c] = str(df.iat[r, c])

            # Join parts vertically
            for c in range(num_cols):
                col_header = " ".join(temp_headers[r][c] for r in range(header_end_row + 1)).strip()
                new_headers.append(col_header if col_header else f"Unnamed: {c}") # Add placeholder if empty

            df.columns = new_headers
            # Drop original header rows
            df = df.iloc[header_end_row + 1:].reset_index(drop=True)
            print(f"    Preprocessed headers: {list(df.columns)}")
        else:
             # Should not happen based on logic above, but as fallback:
             df.columns = [f"Col_{j}" for j in range(df.shape[1])] # Generic headers
             print("    Warning: Could not reliably identify header rows, using generic column names.")


        # 4. Remove rows that are likely separators or empty
        df = df.dropna(how='all') # Remove fully empty rows
        # Remove rows where the first column is NaN or looks like a separator ----
        if not df.empty:
             df = df[pd.notna(df.iloc[:, 0])]
             df = df[~df.iloc[:, 0].astype(str).str.match(r'^-+$')]

        if df.empty:
             print("    Table empty after cleaning header/empty rows.")
             return None

        print(f"    Preprocessing finished. Table shape: {df.shape}")
        return df

    except Exception as e:
         print(f"    Error during table preprocessing: {e}", file=sys.stderr)
         import traceback
         traceback.print_exc() # Print full trace for debugging
         return df_raw # Return original on error

# --- Table Identification (Tuned Keywords, includes preprocessing call check) ---
def find_specific_tables(tables):
    """Identifies key financial tables based on keywords and tries preprocessing."""
    identified_tables = {"income_statement": None}
    print("Attempting to identify specific financial tables...")
    income_keywords = [ # Tuned list
        'revenue', 'cost of revenue', 'cost of sales', 'gross margin', 'gross profit',
        'operating expenses', 'sg & a expenses', 'selling, general',
        'operating income', 'operating profit', 'income before income taxes',
        'profit before tax', 'income taxes', 'income after income taxes',
        'net income', 'net profit', 'earnings per share', 'eps'
    ]
    identified_indices = {}
    best_match_score = -1
    best_match_idx = -1
    best_match_df = None

    for i, df_raw in enumerate(tables):
        if df_raw.empty or df_raw.shape[1] < 2: continue

        # --- Try Preprocessing the Table FIRST ---
        df_processed = preprocess_financial_table(df_raw)
        if df_processed is None or df_processed.empty:
            print(f"  Skipping Table Index {i} after preprocessing yielded empty/unusable table.")
            continue # Skip if preprocessing makes it unusable

        table_text = ""
        try:
            # Use the *processed* DataFrame for text matching
            string_buffer = io.StringIO()
            df_processed.to_string(buf=string_buffer, index=False, header=True)
            table_text = string_buffer.getvalue().lower()
            string_buffer.close()
        except Exception as e:
            print(f"  Warning: Could not convert Processed Table Index {i} to string: {e}")
            continue

        # --- Scoring Logic ---
        income_matches = sum(keyword in table_text for keyword in income_keywords)
        # Check for specific column headers indicative of financial statements
        header_keywords = ['year ended', 'periods ended', 'three-month', 'quarter ended', 'ex adj', 'reported']
        header_match_score = sum(str(col).lower() in header_keywords for col in df_processed.columns)
        # Check for presence of likely label column (non-numeric strings)
        label_col_present = pd.api.types.is_string_dtype(df_processed.iloc[:, 0]) or \
                           (pd.api.types.is_object_dtype(df_processed.iloc[:, 0]) and \
                            df_processed.iloc[:, 0].str.contains(r'[a-zA-Z]').any())


        current_score = income_matches + (header_match_score * 2) + (1 if label_col_present else 0)
        print(f"  Table {i}: Score = {current_score} (Keyword Matches: {income_matches}, Header Score: {header_match_score}, Label Col: {label_col_present})")


        # Identify the best candidate based on score
        if current_score > best_match_score and current_score > 5: # Set a minimum viable score
             best_match_score = current_score
             best_match_idx = i
             best_match_df = df_processed # Store the PREPROCESSED version


    if best_match_df is not None:
         print(f"  Identified Income Statement (Table Index {best_match_idx}) with score {best_match_score}.")
         identified_tables["income_statement"] = best_match_df
         #--- DEBUG: Print identified table ---
         print("--- Identified Income Statement Table (Processed) ---")
         print(identified_tables["income_statement"].head())
         print("------------------------------------------------")
         #--- END DEBUG ---
    else:
         print("  Could not reliably identify the main Income Statement table.")

    return identified_tables

# --- Gemini-Based Extraction Functions (Keep as before, but use processed table) ---
def extract_headline_figures_with_gemini(first_page_text):
    # (Function remains the same as previous version)
    print("Extracting headline figures from Page 1 using Gemini...")
    if not first_page_text: return {}
    prompt = f"""
    Analyze the following text from the first page of an earnings press release.
    Extract the key performance indicators for the **latest reported quarter** (usually Q4 or the latest mentioned).
    Identify and extract the following specifically:

    *   Revenue (Value and Unit, e.g., $7.47 Bn, $4939 Million)
    *   YoY Revenue Growth (%)
    *   Constant Currency (CC) Revenue Growth (%)
    *   Operating Margin (%)
    *   Net Margin (%)
    *   Total Contract Value (TCV) for the quarter (Value and Unit, e.g., $12.2 billion, $2.5 Bn)
    *   Full Year Revenue (Value and Unit, e.g., $30.18 Bn) - If mentioned for the completed FY
    *   YoY Full Year Revenue Growth (%) - If mentioned
    *   CC Full Year Revenue Growth (%) - If mentioned
    *   Full Year TCV (Value and Unit, e.g., $39.4 billion) - If mentioned

    Format the output strictly as KEY: VALUE pairs, one per line.
    If a value is not explicitly mentioned on this page, state "Not Found". Do not calculate or infer values.

    First Page Text:
    ---
    {first_page_text[:10000]}
    ---
    Extracted Headline Figures:
    """
    result = generate_gemini_content(prompt, context="Headline Figures")
    if result.startswith("Error:"):
        print(f"  Headline figure extraction via Gemini failed: {result}")
        return {"Error": "Gemini headline extraction failed"}
    headlines = {}
    for line in result.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if value.lower() != 'not found':
                 headlines[key] = value
                 print(f"  Found (Gemini Headline) - {key}: {value}")
    return headlines

def extract_statement_data_with_gemini(processed_income_table_df): # Takes processed DF
    """Extracts specific figures from the PROCESSED income statement DataFrame using Gemini."""
    if processed_income_table_df is None or processed_income_table_df.empty:
        print("  Processed income statement DataFrame is missing or empty, cannot extract with Gemini.")
        return {}
    print("Extracting data from processed Income Statement table using Gemini...")
    try:
        table_string = processed_income_table_df.to_string(index=False, header=True, na_rep='')
        table_string = re.sub(r' +', ' ', table_string)
        table_string = table_string[:15000]
    except Exception as e:
        print(f"  Error converting processed table to string: {e}", file=sys.stderr)
        return {"Error": "Failed to convert processed table to string"}

    prompt = f"""
    Analyze the following financial table text, which represents a Consolidated Statement of Comprehensive Income. The headers should now be cleaner.
    Focus ONLY on the data column representing the **latest three-month period** (e.g., the quarter ending March 31, 2025, or similar). If multiple three-month periods are shown, use the one with the latest date. Look for column headers indicating the period.
    Extract the following financial figures *from that specific latest quarter column*:

    *   Revenue (Value only, e.g., 7465)
    *   Operating income (or Operating Profit) (Value only)
    *   Net income (or Net Profit) (Value only)
    *   Basic Earnings per share ($) (Value only, e.g., 0.39)

    Report the numeric values exactly as they appear in that column. Assume the scale is millions of dollars unless explicitly stated otherwise in the headers you extract.
    Format the output strictly as KEY: VALUE pairs, one per line. If a value cannot be found for the latest quarter, state "Not Found".

    Financial Table Text:
    ---
    {table_string}
    ---
    Extracted Latest Quarter Statement Figures:
    """
    result = generate_gemini_content(prompt, context="Income Statement Table")
    if result.startswith("Error:"):
        print(f"  Income Statement extraction via Gemini failed: {result}")
        return {"Error": "Gemini table extraction failed"}
    table_figures = {}
    for line in result.splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            if value.lower() != 'not found':
                # Attempt to clean to numeric here before storing? Optional.
                # cleaned_val_for_print = clean_numeric_value(value)
                table_figures[key] = value # Store raw value Gemini found
                print(f"  Found (Gemini Table) - {key}: {value}")
    return table_figures

# --- Helper Function for Data Cleaning (Keep as before) ---
def clean_numeric_value(value_str):
    # (Function remains the same as previous version)
    if isinstance(value_str, (int, float)): return float(value_str)
    if not isinstance(value_str, str): return None
    value_str = str(value_str).strip().lower()
    if value_str in ["not found", "n/a", "not calculated", "error", "inf", ""]: return None
    multiplier = 1.0
    if 'billion' in value_str or 'bn' in value_str: multiplier = 1_000_000_000
    elif 'million' in value_str or 'mn' in value_str or 'm' in value_str:
         if 'b' not in value_str: multiplier = 1_000_000
    value_str = re.sub(r'[$,%a-z]', '', value_str).strip()
    match = re.match(r'^-?([\d,\.]+)', value_str) # Allow comma in regex match
    if match:
        try:
            numeric_part = float(match.group(1).replace(',','')) # Remove comma before float conversion
            return numeric_part * multiplier if multiplier != 1.0 else numeric_part
        except ValueError: return None
    return None

# --- PDF Section Extraction (Keep as before) ---
def extract_pdf_sections(full_text):
    # (Function remains the same as previous version)
    print("Attempting to extract specific text sections from PDF...")
    sections = {}
    patterns = {
        "Outlook": r"^\s*(?:“?Outlook”?|Guidance|Financial Outlook|Business Outlook)\s*[:–—-]?\s*$",
        "Management Commentary": r"^\s*(?:“?Management Commentary”?|CEO Commentary|CFO Commentary|Executive Commentary|Management Comments)\s*[:–—-]?\s*$",
        "Financial Highlights": r"^\s*(?:“?Financial Highlights”?|Key Financials|Performance Highlights)\s*[:–—-]?\s*$",
        "Services": r"^\s*Services\s*[:–—-]?\s*$",
        "Research and Innovation": r"^\s*Research and Innovation\s*[:–—-]?\s*$",
        "Human Resources": r"^\s*Human Resources\s*[:–—-]?\s*$",
    }
    next_header_pattern = r"^\s*(?:(?:[A-Z][a-zA-Z\s]+[:–—-]?)|About\s+|Investor\s+|Conference\s+|Forward-looking)"
    lines = full_text.splitlines()
    current_section_key = None
    current_section_text = []
    capture = False
    for i, line in enumerate(lines):
        line_strip = line.strip()
        if not line_strip: continue
        found_primary_header = False
        for key, pattern in patterns.items():
            if re.match(pattern, line_strip, re.IGNORECASE):
                if current_section_key and current_section_text:
                    sections[current_section_key] = "\n".join(current_section_text).strip()
                    print(f"  Extracted section: {current_section_key} (approx. {len(sections[current_section_key])} chars)")
                current_section_key = key
                current_section_text = [line_strip]
                capture = True
                found_primary_header = True
                break
        if found_primary_header: continue
        if capture and current_section_key:
            if re.match(next_header_pattern, line_strip) and len(line_strip) < 100:
                 sections[current_section_key] = "\n".join(current_section_text).strip()
                 print(f"  Extracted section: {current_section_key} (approx. {len(sections[current_section_key])} chars)")
                 current_section_key = None
                 current_section_text = []
                 capture = False
            else:
                 current_section_text.append(line_strip)
    if current_section_key and current_section_text:
        sections[current_section_key] = "\n".join(current_section_text).strip()
        print(f"  Extracted section: {current_section_key} (approx. {len(sections[current_section_key])} chars)")
    if not sections: print("  No specific text sections identified based on defined patterns.")
    return sections

# --- Main PDF Processing Function (Uses Preprocessing) ---
def process_pdf_data(pdf_path):
    """Orchestrates PDF processing using table preprocessing and Gemini."""
    print(f"\n--- Starting PDF Processing for: {pdf_path} ---")
    full_text, pages_text = extract_text_and_pages(pdf_path)
    if full_text is None: return None
    first_page_text = pages_text[0] if pages_text else ""

    headline_figures_gemini = extract_headline_figures_with_gemini(first_page_text)
    all_tables = extract_tables_from_pdf(pdf_path)
    # find_specific_tables now returns the *preprocessed* DataFrame if found
    identified_tables = find_specific_tables(all_tables)

    table_figures_gemini = {}
    processed_income_statement_df = identified_tables.get("income_statement") # This is already processed
    if processed_income_statement_df is not None:
        # Check size again just in case preprocessing failed silently
        if processed_income_statement_df.shape[0] > 2 and processed_income_statement_df.shape[1] > 1:
             table_figures_gemini = extract_statement_data_with_gemini(processed_income_statement_df)
        else:
             print("  Preprocessed income statement table still seems too small. Skipping Gemini extraction.")
             identified_tables["income_statement"] = None # Mark unusable

    # --- Combine and Process Financial Summary (Logic mostly same as before) ---
    print("Combining and processing financial summary...")
    final_summary = {}

    # 1. Prioritize figures from the Financial Statement table
    # Assume values from Gemini are in millions based on prompt
    revenue_m = clean_numeric_value(table_figures_gemini.get('Revenue')) # Already in M
    op_income_m = clean_numeric_value(table_figures_gemini.get('Operating income')) # Already in M
    net_income_m = clean_numeric_value(table_figures_gemini.get('Net income')) # Already in M
    eps_val = clean_numeric_value(table_figures_gemini.get('Basic Earnings per share ($)'))

    if revenue_m is not None:
        final_summary["Revenue ($M) QX (Table)"] = f"{revenue_m:.1f}"
        print(f"  Added from Table: Revenue ($M): {revenue_m:.1f}")
    # Add other table values... (OpInc, NetInc, EPS) - same logic as before
    if op_income_m is not None:
        final_summary["Operating Income ($M) QX (Table)"] = f"{op_income_m:.1f}"
        print(f"  Added from Table: Operating Income ($M): {op_income_m:.1f}")
    if net_income_m is not None:
        final_summary["Net Income ($M) QX (Table)"] = f"{net_income_m:.1f}"
        print(f"  Added from Table: Net Income ($M): {net_income_m:.1f}")
    if eps_val is not None:
        final_summary["Basic EPS ($) QX (Table)"] = f"{eps_val:.2f}"
        print(f"  Added from Table: Basic EPS ($): {eps_val:.2f}")

    # 2. Calculate Margins from table figures
    # (Logic same as before)
    if op_income_m is not None and revenue_m is not None and revenue_m != 0:
        op_margin_pct = (op_income_m / revenue_m) * 100
        final_summary["Operating Margin (%) QX (Calc)"] = f"{op_margin_pct:.1f}"
        print(f"  Calculated from Table: Operating Margin (%): {op_margin_pct:.1f}")
    if net_income_m is not None and revenue_m is not None and revenue_m != 0:
        net_margin_pct = (net_income_m / revenue_m) * 100
        final_summary["Net Margin (%) QX (Calc)"] = f"{net_margin_pct:.1f}"
        print(f"  Calculated from Table: Net Margin (%): {net_margin_pct:.1f}")

    # 3. Add headline figures (Logic same as before, using the corrected key logic)
    headline_map = { # Map Gemini keys to potentially standardized keys
        "Revenue": "Revenue ($) Headline QX",
        "YoY Revenue Growth (%)": "YoY Revenue Growth (%) QX",
        "Constant Currency (CC) Revenue Growth (%)": "CC Revenue Growth (%) QX",
        "Operating Margin (%)": "Operating Margin (%) Headline QX",
        "Net Margin (%)": "Net Margin (%) Headline QX",
        "Total Contract Value (TCV) for the quarter": "TCV QX",
        "Full Year Revenue": "FY Revenue",
        "YoY Full Year Revenue Growth (%)": "FY YoY Revenue Growth (%)",
        "CC Full Year Revenue Growth (%)": "FY CC Revenue Growth (%)",
        "Full Year TCV": "FY TCV"
    }
    for gemini_key, value in headline_figures_gemini.items():
        standard_key = headline_map.get(gemini_key, gemini_key)
        actual_key_assigned = None
        add_headline = False
        if any(k in standard_key for k in ["Growth", "TCV", "FY"]): add_headline = True
        elif "Operating Margin" in standard_key and "Operating Margin (%) QX (Calc)" not in final_summary: add_headline = True
        elif "Net Margin" in standard_key and "Net Margin (%) QX (Calc)" not in final_summary: add_headline = True
        elif "Revenue" in standard_key and "Revenue ($M) QX (Table)" not in final_summary: add_headline = True

        if add_headline:
            if "TCV" in standard_key or "Revenue" in standard_key:
                unit = 'M' if 'million' in value.lower() else 'B' if 'billion' in value.lower() or 'bn' in value.lower() else None
                numeric_val = clean_numeric_value(value)
                if numeric_val is not None:
                    if unit == 'B':
                        actual_key_assigned = standard_key.replace('QX', '($B) QX')
                        final_summary[actual_key_assigned] = f"{numeric_val / 1_000_000_000:.2f}"
                    elif unit == 'M':
                        actual_key_assigned = standard_key.replace('QX', '($M) QX')
                        final_summary[actual_key_assigned] = f"{numeric_val / 1_000_000:.1f}"
                    else:
                        actual_key_assigned = standard_key.replace('($) ','')
                        final_summary[actual_key_assigned] = f"{numeric_val}"
                else:
                    actual_key_assigned = standard_key
                    final_summary[actual_key_assigned] = value
            else:
                actual_key_assigned = standard_key
                final_summary[actual_key_assigned] = value

            if actual_key_assigned:
                print(f"  Added from Headline: {actual_key_assigned}: {final_summary[actual_key_assigned]}")


    # 4. Attempt to add Guidance (using regex fallback)
    # (Logic same as before)
    guidance_patterns = {
        "FY Revenue Guidance (%)": r"(?:guidance|outlook).*?revenue\s+growth.*?([\d\.\-%]+\s*(?:to|-)\s*[\d\.\-%]+|[\d\.\-%]+)",
        "FY Operating Margin Guidance (%)": r"(?:guidance|outlook).*?operating\s+margin.*?([\d\.\-%]+\s*(?:to|-)\s*[\d\.\-%]+|[\d\.\-%]+)",
    }
    search_text_guidance = full_text
    for key, pattern in guidance_patterns.items():
         if key not in final_summary:
             match = re.search(pattern, search_text_guidance, re.IGNORECASE | re.DOTALL)
             if match:
                 value = match.group(1).strip()
                 value = re.sub(r'\s+', '', value).replace('to', '-')
                 if '%' not in value: value += '%'
                 final_summary[key] = value
                 print(f"  Added from Regex (Guidance): {key}: {value}")

    # --- Prepare Final Output Dict ---
    extracted_sections = extract_pdf_sections(full_text)
    extracted_data = {
        "full_text": full_text,
        "extracted_sections": extracted_sections,
        "financial_summary": final_summary,
        "all_extracted_tables": all_tables,
        "identified_tables": identified_tables
    }
    print("--- PDF Processing Complete ---")
    return extracted_data


# Example usage (Keep as before)
if __name__ == "__main__":
    # (Code remains the same - requires .env and test PDFs)
    # --- IMPORTANT: Configure Gemini API Key if running standalone ---
    from dotenv import load_dotenv
    load_dotenv()
    API_KEY = os.getenv("GOOGLE_API_KEY")
    if not API_KEY:
         print("Error: Set GOOGLE_API_KEY in .env for testing.")
         sys.exit(1)
    try:
        genai.configure(api_key=API_KEY)
        print("Gemini API Key configured for standalone pdf_processor test.")
    except Exception as e:
        print(f"Error configuring Gemini: {e}")
        sys.exit(1)
    # ---------------------------------------------------------------

    test_pdf_path_tcs = 'tcs_q4_fy25_press_release.pdf' # RENAME YOUR TCS PDF TO THIS
    test_pdf_path_infy = 'ifrs-usd-press-release.pdf'

    if os.path.exists(test_pdf_path_tcs):
         test_pdf_path = test_pdf_path_tcs
         print(f"--- Testing PDF Processor with TCS PDF: {test_pdf_path} ---")
    elif os.path.exists(test_pdf_path_infy):
         test_pdf_path = test_pdf_path_infy
         print(f"--- Testing PDF Processor with Infosys PDF: {test_pdf_path} ---")
    else:
        print(f"Error: Test PDF not found at '{test_pdf_path_tcs}' or '{test_pdf_path_infy}'")
        sys.exit(1)

    pdf_results = process_pdf_data(test_pdf_path)
    if pdf_results:
        print("\n--- PDF Processing Test Results ---")
        print("\nCombined Financial Summary:")
        if pdf_results.get("financial_summary"):
            sorted_summary = dict(sorted(pdf_results["financial_summary"].items()))
            for key, value in sorted_summary.items():
                print(f"  {key}: {value}")
        else:
            print("  No financial summary generated.")

        print("\nExtracted Sections:")
        if pdf_results.get("extracted_sections"):
            for key, value in pdf_results["extracted_sections"].items():
                print(f"  {key}: (Found, {len(value)} chars)")
        else:
            print("  None found.")

        print("\nIdentified Tables:")
        if pdf_results.get("identified_tables"):
            for key, table in pdf_results["identified_tables"].items():
                status = "Found" if table is not None else "Not Found"
                print(f"  {key}: {status}")
        else:
            print(" No specific tables identified.")
    else:
        print("PDF processing failed.")