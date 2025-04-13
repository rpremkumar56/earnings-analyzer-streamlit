# transcript_processor.py

import google.generativeai as genai
from dotenv import load_dotenv
import docx
import os
import re
import sys
import time

# Load API key from .env file
load_dotenv()
API_KEY = os.getenv("GOOGLE_API_KEY")

if not API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.", file=sys.stderr)
    sys.exit("API Key missing. Please create a .env file with GOOGLE_API_KEY='YOUR_API_KEY'")

# Configure the Gemini API client
genai.configure(api_key=API_KEY)

# --- Model Configuration (No change) ---
MODEL_NAME = "gemini-2.0-flash"
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]
GENERATION_CONFIG = {
    "temperature": 0.5,
    "top_p": 0.95,
    "top_k": 64,
    "max_output_tokens": 8192,
    "response_mime_type": "text/plain",
}

# --- Helper Function for API Calls (No change) ---
def generate_gemini_content(prompt, retries=3, delay=5):
    """Generates content using the Gemini API with error handling and retries."""
    print(f"  Calling Gemini API (Model: {MODEL_NAME})...")
    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        safety_settings=SAFETY_SETTINGS,
        generation_config=GENERATION_CONFIG,
    )
    for attempt in range(retries):
        try:
            response = model.generate_content(prompt)
            if response and response.candidates and response.candidates[0].content.parts:
                 print("  Gemini API call successful.")
                 # Added simple check for potential blocked content message in the response text itself
                 response_text = response.text.strip()
                 if "content has been blocked" in response_text.lower():
                     print(f"  Warning: Gemini response indicates content may be blocked. Response: '{response_text[:100]}...'")
                     # You might want to return a specific error or the message itself
                     # return f"Error: Response possibly blocked by safety settings."
                 return response_text
            else:
                block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else 'Unknown'
                safety_ratings = response.candidates[0].safety_ratings if response.candidates else 'N/A'
                print(f"  Warning: Gemini response blocked or empty. Reason: {block_reason}. Ratings: {safety_ratings}", file=sys.stderr)
                return f"Error: Response blocked or empty (Reason: {block_reason})"

        except Exception as e:
            print(f"  Error calling Gemini API (Attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
            if attempt < retries - 1:
                print(f"  Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print("  Max retries reached. Skipping this request.", file=sys.stderr)
                return f"Error: API call failed after multiple retries ({e})"
    return "Error: Max retries reached."


# --- Transcript Fetching and Cleaning (No change) ---
def get_transcript_from_word(docx_path):
    """Reads the text content from a Word document (.docx)."""
    if not os.path.exists(docx_path):
        print(f"Error: Transcript file not found at {docx_path}", file=sys.stderr)
        return None, f"File not found: {docx_path}"
    if not docx_path.lower().endswith('.docx'):
        print(f"Error: Invalid file type. Expected .docx, got {docx_path}", file=sys.stderr)
        return None, "Invalid file type (expected .docx)"

    try:
        print(f"Reading transcript from Word file: {docx_path}")
        document = docx.Document(docx_path)
        full_text = []
        for para in document.paragraphs:
            full_text.append(para.text)
        transcript = "\n".join(full_text)
        print("Transcript read successfully from Word file.")
        return transcript, None
    except Exception as e:
        print(f"Error reading Word document {docx_path}: {e}", file=sys.stderr)
        return None, f"Error reading docx file: {e}"

def clean_transcript(text):
    """Basic cleaning: removes timestamps and normalizes whitespace."""
    if not text: return ""
    text = re.sub(r'\[\d{2}:\d{2}:\d{2}\]', '', text)
    text = re.sub(r'\(\d{2}:\d{2}:\d{2}\)', '', text)
    text = re.sub(r'^\s*[A-Za-z\s]+:\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# --- Gemini-based Analysis Functions ---

# extract_keywords_gemini (No change)
def extract_keywords_gemini(text, top_n=25):
    """Extracts keywords using Gemini API."""
    if not text: return []
    print("Extracting keywords using Gemini...")
    prompt = f"""
    Analyze the following text from a financial earnings call transcript.
    Extract the top {top_n} most relevant and important keywords or keyphrases.
    Focus on business terms, company-specific names, financial metrics, products, technologies, market trends, and strategic initiatives.
    Exclude common stop words and generic terms unless they are part of a specific keyphrase (e.g., 'cost optimization').
    List the keywords/keyphrases, one per line.

    Transcript Text:
    ---
    {text[:15000]}
    ---
    Keywords/Keyphrases:
    """
    result = generate_gemini_content(prompt)
    if result.startswith("Error:"):
        print(f"  Keyword extraction failed: {result}")
        return []
    keywords = [line.strip() for line in result.splitlines() if line.strip()]
    print(f"  Extracted {len(keywords)} keywords.")
    return [(keyword, 1) for keyword in keywords]

# extract_executive_quotes_gemini (No change)
def extract_executive_quotes_gemini(press_release_text):
    """Extracts executive quotes using Gemini API from press release text."""
    if not press_release_text: return {}
    print("Extracting executive quotes using Gemini...")
    prompt = f"""
    Analyze the following press release text from a company's earnings announcement.
    Identify direct quotes attributed specifically to the CEO (Chief Executive Officer) and the CFO (Chief Financial Officer).
    Extract the full quote (within the quotation marks) and the name of the executive it is attributed to.
    Format the output strictly as follows for each role found:
    ROLE: "Quote text..." - Executive Name

    Example:
    CEO: "We had a strong quarter..." - John Doe
    CFO: "Margins improved due to efficiency..." - Jane Smith

    If no quote is found for a specific role (CEO or CFO), do not include that role in the output.

    Press Release Text:
    ---
    {press_release_text}
    ---
    Identified Quotes:
    """
    result = generate_gemini_content(prompt)
    if result.startswith("Error:"):
        print(f"  Quote extraction failed: {result}")
        return {}
    quotes = {}
    for line in result.splitlines():
        line = line.strip()
        if line.startswith("CEO:"):
            quotes["CEO"] = line[4:].strip()
            print(f"  Found CEO quote.")
        elif line.startswith("CFO:"):
            quotes["CFO"] = line[4:].strip()
            print(f"  Found CFO quote.")
    return quotes

# extract_themed_summary_gemini (Updated Prompt)
def extract_themed_summary_gemini(text, theme_name, theme_keywords):
    """Generates a summary for a specific theme using Gemini API, including sentiment."""
    if not text: return "Not analyzed."
    print(f"Generating summary for theme: {theme_name} using Gemini...")
    # --- Updated Prompt ---
    prompt = f"""
    Analyze the following financial earnings call transcript. Focus specifically on comments related to the theme: '{theme_name}'.
    Relevant keywords for this theme include: {', '.join(theme_keywords)}.

    1.  **Sentiment:** Briefly assess the overall sentiment expressed *regarding this theme* in the transcript (e.g., Positive, Negative, Neutral, Cautiously Optimistic, Mixed). Base this *only* on the provided text. Format as: `Sentiment: [Your Assessment]`
    2.  **Summary:** Provide a concise summary (2-4 bullet points or a short paragraph) of the key points, discussions, outlook, or specific figures mentioned regarding '{theme_name}'. Prioritize quantifiable information if available in the text.

    If nothing significant related to this theme is discussed, state "No significant discussion found for this theme." in the summary section and "Sentiment: N/A".

    Transcript Text:
    ---
    {text}
    ---

    Analysis for {theme_name}:
    Sentiment:
    Summary:
    """
    # --------------------

    summary = generate_gemini_content(prompt)
    if summary.startswith("Error:"):
        print(f"  Summary generation failed for theme {theme_name}: {summary}")
        return f"Error during summary generation for {theme_name}."

    # Simple formatting check/cleanup (optional): Ensure Sentiment and Summary labels exist
    if "Sentiment:" not in summary and "Summary:" not in summary and "No significant discussion" not in summary:
         print(f"  Warning: Unexpected format for theme '{theme_name}'. Using raw output.")
         return summary # Return raw output if format is unexpected
    # Ensure there's a newline between sentiment and summary if both present
    summary = summary.replace("Summary:", "\nSummary:")


    return summary.strip()


# --- Main Processing Function (No change) ---
def process_transcript_data(transcript_docx_path, press_release_text=None):
    """
    Orchestrates transcript reading from DOCX and analysis using Google Gemini API.
    """
    print(f"\n--- Starting Transcript Processing for File: {transcript_docx_path} ---")
    full_transcript, error = get_transcript_from_word(transcript_docx_path)
    if error:
        return {"error": error}

    cleaned_transcript = clean_transcript(full_transcript)
    print(f"Transcript length (cleaned): {len(cleaned_transcript)} characters")
    if not cleaned_transcript:
         print("Warning: Cleaned transcript is empty. Analysis may not be effective.", file=sys.stderr)

    # --- Analysis using Gemini ---
    keywords = extract_keywords_gemini(cleaned_transcript)
    executive_quotes = extract_executive_quotes_gemini(press_release_text)

    # --- Thematic Summaries ---
    theme_definitions = {
        "AI/GenAI": ["ai", "genai", "generative ai", "artificial intelligence", "topaz"],
        "Demand Environment/Pipeline": ["demand", "budget", "spending", "pipeline", "discretionary", "optimism", "headwinds", "modernization", "outlook"],
        "Deal Activity/TCV": ["tcv", "deal", "wins", "large deal", "order book", "booking"],
        "Margins/Profitability/Costs": ["margin", "profitability", "cost optimization", "realization", "utilization", "pricing", "efficiency"],
        "Hiring/Headcount/Attrition": ["hiring", "headcount", "attrition", "talent", "onboard", "employees", "promotions"],
        "Vertical Performance (e.g., BFSI, Retail)": ["bfsi", "banking", "financial services", "insurance", "manufacturing", "retail", "cpg", "consumer", "life sciences", "healthcare", "energy", "utilities", "communications", "media"],
        "Cloud Services": ["cloud", "azure", "aws", "gcp", "migration"],
        "Geographic Performance": ["north america", "europe", "uk", "india", "emerging markets"]
    }
    commentary_summaries = {}
    themes_to_process = theme_definitions.keys()

    for theme in themes_to_process:
        keywords_for_theme = theme_definitions[theme]
        summary = extract_themed_summary_gemini(cleaned_transcript, theme, keywords_for_theme)
        commentary_summaries[theme] = summary
        # time.sleep(1) # Optional delay

    analysis_results = {
        "full_transcript": full_transcript,
        "cleaned_transcript": cleaned_transcript,
        "keywords": keywords,
        "executive_quotes": executive_quotes,
        "commentary_summaries": commentary_summaries,
        "error": None
    }
    print("--- Transcript Processing Complete ---")
    return analysis_results


# --- Example usage (No change, will just show new summary format if run) ---
if __name__ == "__main__":
    # (Code for creating dummy file, calling process_transcript_data, and printing results remains the same)
    # ... [rest of the testing code as before] ...
    # Create a dummy transcript file for testing
    dummy_file_path = "dummy_transcript.docx"
    try:
        doc = docx.Document()
        doc.add_paragraph("Operator: Good morning everyone.")
        doc.add_paragraph("[00:00:15] Jane Doe:")
        doc.add_paragraph("Welcome to the Q3 call. We saw strong demand this quarter, it was really fantastic. Beat expectations.")
        doc.add_paragraph("AI and cloud adoption are key drivers, everyone is excited.")
        doc.add_paragraph("John Smith:")
        doc.add_paragraph("Margins were stable due to cost optimization efforts, although inflation remains a slight concern.")
        doc.save(dummy_file_path)
        print(f"Created dummy transcript file: {dummy_file_path}")

        dummy_press_release = """
        "Our strong revenue growth sequentially...", said Salil Parekh, CEO and MD.
        "We had another quarter of strong performance...", said Jayesh Sanghrajka, CFO.
        """
        print("Running transcript processor directly for testing...")
        results = process_transcript_data(dummy_file_path, press_release_text=dummy_press_release)

        if results.get('error'):
            print(f"\nProcessing failed: {results['error']}")
        else:
            print("\n--- Transcript Processing Test Results ---")
            print(f"\nKeywords Found: {len(results.get('keywords', []))}")
            print("\nExecutive Quotes:")
            quotes = results.get('executive_quotes', {})
            if quotes:
                for role, quote in quotes.items():
                    print(f"  {role}: {quote}")
            else:
                print("  None found.")

            print("\nCommentary Summaries:")
            summaries = results.get('commentary_summaries', {})
            if summaries:
                for theme, summary in summaries.items():
                    print(f"--- {theme} ---")
                    print(summary) # This will now hopefully include "Sentiment: ..."
                    print("-" * (len(theme) + 8))
            else:
                print("  None generated.")

    except Exception as e:
        print(f"Error during test execution: {e}")
    finally:
        if os.path.exists(dummy_file_path):
            os.remove(dummy_file_path)
            print(f"Removed dummy transcript file: {dummy_file_path}")