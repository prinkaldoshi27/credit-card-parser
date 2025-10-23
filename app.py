import os
import re
import uuid
import pdfplumber
from flask import Flask, render_template, request, redirect, url_for

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
           
def determine_issuer(pdf_path):
    """Identifies the issuer by scanning text across pages."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text = ""
            for page in pdf.pages:
                txt = page.extract_text()
                if txt:
                    full_text += txt + " "
            upper_text = full_text.upper()
            if "BANK OF AMERICA" in upper_text or "WWW.BANKOFAMERICA.COM" in upper_text:
                return "Bank of America"
            if "KOTAK" in upper_text or "WWW.KOTAK.COM" in upper_text:
                return "Kotak"
            if ("ONE" in upper_text and "CREDIT CARD" in upper_text) and \
               ("SOUTH INDIAN BANK" in upper_text or "SIB" in upper_text):
                return "One/South Indian Bank"            
            if "ICICI" in upper_text and "CREDIT CARD" in upper_text:
                return "ICICI Bank"
            if "CHASE" in upper_text and "CARDMEMBER" in upper_text:
                return "Chase"
            return "Unknown"
    except Exception as e:
        return f"Error Reading PDF: {e}"
    
def clean_amount(amount_str):
    if amount_str is None: 
        return 'N/A'
    is_negative = amount_str.strip().startswith('-')
    # Remove all non-digit, non-decimal-point characters, except potentially the negative sign
    cleaned = re.sub(r'[^\d\.]', '', amount_str.replace(',', '').replace('RS', '').replace('R', '').replace('₹', '').strip())
    # Re-add the negative sign if it was present in the original string
    if is_negative and cleaned:
        return '-' + cleaned
    return cleaned

def extract_metadata(text, issuer):
    data = {"Issuer": issuer}
    clean_text = text.replace('\n', ' ').replace('\r', ' ')
    clean_text = re.sub(r'\s+', ' ', clean_text).upper()
    data.update({
        'Card Last 4 Digits': 'N/A',
        'Total Balance': 'N/A', 
        'Payment Due Date': 'N/A',
        'Minimum Payment Due': 'N/A',
    })
    if issuer == "ICICI Bank":
        match_card = re.search(r'(?:XXXX|X{4}|X{8,})\D*(\d{4})', clean_text)
        data['Card Last 4 Digits'] = match_card.group(1) if match_card else "N/A"
        match_total = re.search(r'TOTAL\s+AMOUNT\s+DUE\s*(?:₹|RS\.?)?\s*([\d,]+\.\d{2})', clean_text)
        data['Total Balance'] = clean_amount(match_total.group(1) if match_total else None)
        match_min = re.search(r'MINIMUM\s+AMOUNT\s+DUE\s*(?:₹|RS\.?)?\s*([\d,]+\.\d{2})', clean_text)
        data['Minimum Payment Due'] = clean_amount(match_min.group(1) if match_min else None)
        match_due = re.search(r'PAYMENT\s+DUE\s+DATE\s*(\d{2}[/-][A-Z]{3}[/-]\d{4}|\d{2}/\d{2}/\d{4}|[A-Z]+\s*\d{1,2},\s*\d{4})', clean_text)
        data['Payment Due Date'] = match_due.group(1) if match_due else "N/A"
        is_zero_balance_explicit = re.search(r'TOTAL\s+AMOUNT\s+DUE\s*(?:₹|RS\.?)?\s*0\.00', clean_text)
        if data['Total Balance'] in ['0.00', '0', ''] or is_zero_balance_explicit:
            data['Total Balance'] = "0.00"
            data['Minimum Payment Due'] = "0.00"
            data['Payment Due Date'] = "No payment required"
        elif "NO PAYMENT REQUIRED" in clean_text:
            data['Total Balance'] = "0.00"
            data['Minimum Payment Due'] = "0.00"
            data['Payment Due Date'] = "No payment required"
    
    elif issuer == "Kotak":
        match_card = re.search(r'PRIMARY\s+CARD\s+NUMBER.*?(\d{4})', clean_text)
        if not match_card:
            match_card = re.search(r'(?:[\dX]{4}[-\s]?){3}(\d{4})', clean_text)
        if not match_card:
            match_card = re.search(r'([X\d\s]{10,})(\d{4})', clean_text)
            if match_card:
                data['Card Last 4 Digits'] = match_card.group(2)
            else:
                data['Card Last 4 Digits'] = "N/A"
        else:
            data['Card Last 4 Digits'] = match_card.group(1) if match_card else "N/A"
        match_mad = re.search(
            r'Minimum\s+Amount\s+Due\s*\(MAD\)\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})',
            clean_text,
            re.IGNORECASE
        )
        data['Minimum Payment Due'] = clean_amount(match_mad.group(1) if match_mad else None)
        match_mad = re.search(
            r'Minimum\s+Amount\s+Due\s*\(MAD\)\s*(?:Rs\.?|₹)?\s*([\d,]+\.\d{2})',
            clean_text,
            re.IGNORECASE
        )
        data['Minimum Payment Due'] = clean_amount(match_mad.group(1) if match_mad else None)
        match_due = re.search(
            r'Remember\s+to\s+pay\s+by\s+([A-Za-z]+\s*\d{1,2},?\s*\d{4}|No\s+payment\s+required|\d{1,2}-[A-Za-z]{3}-\d{4}|\d{2}/\d{2}/\d{4})',
            clean_text,
            re.IGNORECASE
        )
        if match_due:
            due = match_due.group(1).strip()
            if re.search(r'No\s*payment\s*required', due, re.IGNORECASE):
                data['Payment Due Date'] = 'No payment required'
            else:
                data['Payment Due Date'] = due
        else:
            data['Payment Due Date'] = 'N/A'
        if (
            "NO PAYMENT REQUIRED" in clean_text.upper()
            or re.search(r'RS\.?\s*0\.00', clean_text, re.IGNORECASE)
            or re.search(r'₹\s*0\.00', clean_text)
        ):
            data['Total Balance'] = '0.00'
            data['Minimum Payment Due'] = '0.00'
            data['Payment Due Date'] = 'No payment required'
    
    elif issuer == "Bank of America":
            # Card Last 4 Digits extraction
            match_card = re.search(r'ACCOUNT(?:#| NUMBER):\s*[\dX\s]{10,}(\d{4})', clean_text)
            if not match_card:
                match_card = re.search(r'ACCOUNT(?:#| NUMBER).*?(\d{4})$', clean_text) 
            data['Card Last 4 Digits'] = match_card.group(1) if match_card else "N/A"
            # Total Balance: Adjusted regex to explicitly look for the negative sign/dollar sign and capture it.
            # This regex will handle both:
            # 1. NEW BALANCE TOTAL...-\$62.56
            # 2. NEW BALANCE TOTAL Credit balance, please do not pay...-\$62.56
            match_total_balance = re.search(
                r'NEW\s+BALANCE\s+TOTAL.*?([\-]?)\s*(?:[\$RS₹])?\s*([\d,]+\.\d{2})', clean_text
            )
            if match_total_balance:
                negative_sign = match_total_balance.group(1)
                amount_value = match_total_balance.group(2)
                # Manually combine to ensure the negative sign is not lost
                full_amount_str = negative_sign + amount_value
                data['Total Balance'] = clean_amount(full_amount_str)
            else:
                data['Total Balance'] = 'N/A'
            # Payment Due Date extraction remains the same
            match_due_date = re.search(r'PAYMENT\s+DUE\s+DATE\s*(\d{2}/\d{2}/\d{4})', clean_text)
            data['Payment Due Date'] = match_due_date.group(1) if match_due_date else "N/A"
            # Minimum Payment Due extraction remains the same
            match_min_payment = re.search(r'TOTAL\s+MINIMUM\s+PAYMENT\s+DUE.*?(?:[\$RS₹])?\s*([\d,]+\.\d{2})', clean_text)
            data['Minimum Payment Due'] = clean_amount(match_min_payment.group(1) if match_min_payment else None)
    elif issuer == "One/South Indian Bank":
            data['Card Last 4 Digits'] = re.search(r'4182-12XX-XXXX-(\d{4})', clean_text).group(1) if re.search(r'4182-12XX-XXXX-(\d{4})', clean_text) else "N/A"
            match_total = re.search(r'TOTAL AMOUNT DUE.*?([\d,]+\.\d{2})', clean_text)
            data['Total Balance'] = clean_amount(match_total.group(1) if match_total else None)
            match_min = re.search(r'MINIMUM AMOUNT DUE.*?([\d,]+\.\d{2})', clean_text)
            data['Minimum Payment Due'] = clean_amount(match_min.group(1) if match_min else None)
            match_due_date = re.search(r'PAYMENT\s+DUE\s+DATE\s+([\d]{2}\s+[A-Z]{3}\s+[\d]{4})', clean_text)
            data['Payment Due Date'] = match_due_date.group(1) if match_due_date else "N/A"
    elif issuer == "Chase":
        # Card Last 4 Digits: Account Number: XXXX XXXX XXXX XXXX (Example)
        # REVISED: Match the placeholder pattern [X\d] and remove the .replace('X', '0')
        match_last_4 = re.search(r'ACCOUNT NUMBER[:\s]+[X\d]{4}\s+[X\d]{4}\s+[X\d]{4}\s+([X\d]{4})', clean_text)
        # Now it will correctly return 'XXXX' from the document's placeholder text
        data['Card Last 4 Digits'] = match_last_4.group(1) if match_last_4 else "N/A" 
        # Total Balance: NEW BALANCE $1,245.00. Retaining the highly flexible regex.
        match_total_balance = re.search(r'NEW BALANCE\s*(?:[\$])?([\d,]+\.\d{2})', clean_text)
        data['Total Balance'] = clean_amount(match_total_balance.group(1) if match_total_balance else None)
        # Payment Due Date: PAYMENT DUE DATE 01/25/2019. Retaining the flexible regex.
        match_due_date = re.search(r'PAYMENT DUE DATE\s*(\d{2}/\d{2}/\d{4})', clean_text)
        data['Payment Due Date'] = match_due_date.group(1) if match_due_date else "N/A"
        # Minimum Payment Due: MINIMUM PAYMENT DUE $25.00. REVISED: Use a non-greedy wildcard .*? to be extremely flexible
        match_min_payment = re.search(r'MINIMUM PAYMENT DUE.*?([\d,]+\.\d{2})', clean_text)
        data['Minimum Payment Due'] = clean_amount(match_min_payment.group(1) if match_min_payment else None)
        # --- Fallback logic remains essential ---
        if data['Payment Due Date'] == "N/A":
            fallback_due_date = re.search(r'PAYMENT DUE DATE: ?(\d{2}/\d{2}/\d{2})', clean_text)
            if fallback_due_date:
                data['Payment Due Date'] = fallback_due_date.group(1)
        if data['Minimum Payment Due'] == "N/A":
            # This fallback matches the remittance section's "Amount Due: $25.00"
            fallback_min_payment = re.search(r'AMOUNT DUE: ?([\d,]+\.\d{2})', clean_text)
            if fallback_min_payment:
                data['Minimum Payment Due'] = clean_amount(fallback_min_payment.group(1))
        if data['Total Balance'] == "N/A":
            # This fallback matches the remittance section's "New Balance: $1,245.00"
            fallback_total_balance = re.search(r'NEW BALANCE: ?([\d,]+\.\d{2})', clean_text)
            if fallback_total_balance:
                data['Total Balance'] = clean_amount(fallback_total_balance.group(1))    
    else:
        data['Card Last 4 Digits'] = re.search(r'CARD NUMBER.*?(\d{3,4})', clean_text) or re.search(r'ACCOUNT NUMBER.*?(\d{3,4})', clean_text)
        data['Card Last 4 Digits'] = data['Card Last 4 Digits'].group(1) if data['Card Last 4 Digits'] else "N/A"
        data['Total Balance'] = clean_amount(re.search(r'(NEW BALANCE|BALANCE DUE|TOTAL AMOUNT DUE).*?([\d,]+\.\d{2})', clean_text).group(2) if re.search(r'(NEW BALANCE|BALANCE DUE|TOTAL AMOUNT DUE).*?([\d,]+\.\d{2})', clean_text) else None)
        data['Payment Due Date'] = re.search(r'(PAYMENT DUE DATE|DUE DATE).*?(\d{2}/\d{2}/\d{2,4})', clean_text).group(2) if re.search(r'(PAYMENT DUE DATE|DUE DATE).*?(\d{2}/\d{2}/\d{2,4})', clean_text) else "N/A"
        data['Minimum Payment Due'] = clean_amount(re.search(r'(MINIMUM PAYMENT DUE|MINIMUM AMOUNT DUE).*?([\d,]+\.\d{2})', clean_text).group(2) if re.search(r'(MINIMUM PAYMENT DUE|MINIMUM AMOUNT DUE).*?([\d,]+\.\d{2})', clean_text) else None)
    return data

@app.route('/', methods=['GET', 'POST'])

def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            return render_template('results.html', error="No file part in the request.", filename="N/A")
        file = request.files['file']
        original_filename = file.filename
        if original_filename == '':
            return render_template('results.html', error="No file selected for upload.", filename="N/A")
        if file and allowed_file(original_filename):
            file_extension = original_filename.rsplit('.', 1)[1].lower()
            secure_filename = str(uuid.uuid4()) + '.' + file_extension
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename)
            try:
                file.save(file_path)
                issuer = determine_issuer(file_path)
                with pdfplumber.open(file_path) as pdf:
                    full_text = "".join(page.extract_text() for page in pdf.pages)
                results = extract_metadata(full_text, issuer)
                def get_status(key, value):
                    if value == 'N/A' or (key == 'Issuer Detected' and value == 'Unknown'):
                        return 'error'
                    if key == 'Payment Due Date' and value == 'No payment required':
                        return 'info'
                    if key == 'Payment Due Date':
                        return 'success'
                    if key == 'Minimum Payment Due':
                        return 'warning'
                    return 'info'
                data_list = [
                    ("Issuer Detected", results.get("Issuer", "N/A"), get_status("Issuer Detected", results.get("Issuer", "N/A"))),
                    ("Card Last 4 Digits", results.get("Card Last 4 Digits", "N/A"), get_status("Card Last 4 Digits", results.get("Card Last 4 Digits", "N/A"))),
                    ("Total Balance", results.get("Total Balance", "N/A"), get_status("Total Balance", results.get("Total Balance", "N/A"))), 
                    ("Payment Due Date", results.get("Payment Due Date", "N/A"), get_status("Payment Due Date", results.get("Payment Due Date", "N/A"))),
                    ("Minimum Payment Due", results.get("Minimum Payment Due", "N/A"), get_status("Minimum Payment Due", results.get("Minimum Payment Due", "N/A")))
                ]
                return render_template('results.html', data_list=data_list, filename=original_filename)
            except Exception as e:
                error_message = f"Processing Error: {str(e)}"
                return render_template('results.html', error=error_message, filename=original_filename)
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
        else:
            return render_template('results.html', error="Invalid file format. Please upload a PDF file.", filename=original_filename)
    return render_template('upload.html')