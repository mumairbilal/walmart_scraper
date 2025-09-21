import datetime
import os
import time
import string
import random
import uuid
import streamlit as st
import pandas as pd
from firecrawl import Firecrawl
import firebase_admin
from firebase_admin import credentials, firestore
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Plan limits configuration
PLAN_LIMITS = {
    "Free": {"daily_limit": 50, "valid_days": 7},
    "Basic": {"daily_limit": 500, "valid_days": 30},
    "Premium": {"daily_limit": 2500, "valid_days": 90},
    "Enterprise": {"daily_limit": 5000, "valid_days": 365}
}

LOCAL_RECORDS_FILE = "./walmart_scraper_records.csv"
LOCAL_DEVICE_ID_FILE = os.path.expanduser("~/.walmart_scraper_device_id")

def hide_file_on_windows(file_path):
    try:
        import ctypes
        FILE_ATTRIBUTE_HIDDEN = 0x02
        ret = ctypes.windll.kernel32.SetFileAttributesW(file_path, FILE_ATTRIBUTE_HIDDEN)
        if ret == 0:
            raise OSError("Failed to hide file on Windows.")
    except Exception as e:
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error hiding file: {e}")

def get_device_id():
    try:
        if os.path.exists(LOCAL_DEVICE_ID_FILE):
            with open(LOCAL_DEVICE_ID_FILE, "r") as f:
                return f.read().strip()
        else:
            device_id = str(uuid.uuid4())
            os.makedirs(os.path.dirname(LOCAL_DEVICE_ID_FILE), exist_ok=True)
            with open(LOCAL_DEVICE_ID_FILE, "w") as f:
                f.write(device_id)
            if os.name == 'nt':
                hide_file_on_windows(LOCAL_DEVICE_ID_FILE)
            return device_id
    except Exception as e:
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error generating device ID: {e}")
        return None

def load_records():
    try:
        if os.path.exists(LOCAL_RECORDS_FILE):
            df = pd.read_csv(LOCAL_RECORDS_FILE)
            return df
        return pd.DataFrame(columns=["Bot Name", "Sender Name", "Email", "Time", "Date", "Request Status"])
    except Exception as e:
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error loading records: {e}")
        return pd.DataFrame(columns=["Bot Name", "Sender Name", "Email", "Time", "Date", "Request Status"])

def save_records(df):
    try:
        df.to_csv(LOCAL_RECORDS_FILE, index=False)
        if os.name == 'nt':
            hide_file_on_windows(LOCAL_RECORDS_FILE)
    except Exception as e:
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error saving records: {e}")

def send_request_email(name, client_email, bot_name, plan):
    admin_email = os.getenv("ADMIN_EMAIL", "umisoftbotnotifier@gmail.com")
    smtp_user = os.getenv("SMTP_USER", "umisoftbotnotifier@gmail.com")
    smtp_pass = os.getenv("SMTP_PASS", "ylor vkis zarh mokt")
    max_retries = 3
    retry_count = 0
    while retry_count < max_retries:
        try:
            msg = MIMEMultipart()
            msg['From'] = smtp_user
            msg['To'] = admin_email
            msg['Subject'] = "New Client License Request"
            body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333333; }}
                    .container {{ max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 10px; box-shadow: 0 0 10px rgba(0, 0, 0, 0.1); }}
                    .header {{ background-color: #f7f7f7; padding: 10px 20px; border-bottom: 1px solid #e0e0e0; text-align: center; }}
                    .header h1 {{ margin: 0; font-size: 24px; color: #000000; }}
                    .content {{ padding: 20px; }}
                    .content p {{ margin: 0; }}
                    .footer {{ text-align: center; margin-top: 20px; }}
                    .footer p {{ font-size: 12px; color: #888888; }}
                </style>
            </head>
            <body>
                <div class='container'>
                    <div class='header'>
                        <h1>New Key Request</h1>
                    </div>
                    <div class='content'>
                        <p><strong>Name:</strong> {name}</p><br>
                        <p><strong>Client Email:</strong> <a href='mailto:{client_email}'>{client_email}</a></p><br>
                        <p><strong>Bot Name:</strong> {bot_name}</p><br>
                        <p><strong>Plan:</strong> {plan}</p>
                    </div>
                    <div class='footer'>
                        <p>Thank you for using our bot service. Please process this request.</p>
                    </div>
                </div>
            </body>
            </html>"""
            msg.attach(MIMEText(body, 'html'))
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            return True
        except smtplib.SMTPServerDisconnected:
            retry_count += 1
            time.sleep(1)
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Email send error: {e}")
            return False
    st.session_state.error_log.append(f"{datetime.datetime.now()}: Email send failed after {max_retries} retries")
    return False
class FirebaseFunctions:
    _firestore_db = None
    
    # @staticmethod
    # def initialize_firebase():
    #     """Initialize Firebase connection"""
    #     if not firebase_admin._apps:
    #         cred = credentials.Certificate("umisoft-client-database-firebase-adminsdk.json")
    #         firebase_admin.initialize_app(cred)
    #     FirebaseFunctions._firestore_db = firestore.client()
    @staticmethod
    def initialize_firebase():
        if not firebase_admin._apps:
            firebase_env = os.getenv("FIREBASE_CREDENTIALS")
            if firebase_env:
                firebase_config = json.loads(firebase_env)
                cred = credentials.Certificate(firebase_config)
            else:
                cred = credentials.Certificate("umisoft-client-database-firebase-adminsdk.json")
            firebase_admin.initialize_app(cred)
        FirebaseFunctions._firestore_db = firestore.client()
    
    @staticmethod
    def get_client_data_by_license_key(license_key):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                client_data = docs[0].to_dict()
                client_data["id"] = docs[0].id
                return client_data
            return None
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Get client by key error: {e}")
            return None
    
    @staticmethod
    def get_client_data_by_email(email):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientEmail", "==", email)
            docs = list(query.stream())
            if len(docs) > 0:
                client_data = docs[0].to_dict()
                client_data["id"] = docs[0].id
                return client_data
            return None
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Get client by email error: {e}")
            return None
    
    @staticmethod
    def has_existing_free_account(email):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientEmail", "==", email).where("Plan", "==", "Free")
            docs = list(query.stream())
            return len(docs) > 0
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Check existing free account error: {e}")
            return False
    
    @staticmethod
    def is_client_eligible(client_data, expected_bot_name, expected_valid_date, current_device_id):
        if client_data is None:
            return False, "Client data not found."
        if str(client_data.get("ToolName", "")).lower() != str(expected_bot_name).lower():
            return False, "Invalid bot name."
        if str(client_data.get("AccessStatus", "")) != "ON":
            return False, "Access is not active."
        stored_device_id = client_data.get("DeviceID", "")
        if stored_device_id and stored_device_id != current_device_id:
            return False, "License key is already in use on another device."
        try:
            date_string = str(client_data.get("ValidUntil", ""))
            date_formats = ["%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y"]
            valid_date = None
            for fmt in date_formats:
                try:
                    valid_date = datetime.datetime.strptime(date_string, fmt)
                    break
                except ValueError:
                    continue
            if valid_date is None or valid_date < expected_valid_date:
                return False, "License has expired."
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Date validation error: {e}")
            return False, "Invalid license date."
        return True, "Eligible"

    @staticmethod
    def add_request(client_data):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            client_data["RegistrationDate"] = datetime.datetime.now().strftime("%Y-%m-%d")
            client_data["AccessStatus"] = "PENDING"
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            new_doc_ref = clients_ref.document()
            new_doc_ref.set(client_data)
            return new_doc_ref.id
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Add request error: {e}")
            return None
    
    @staticmethod
    def update_client_validation(license_key, url_count):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                batch = FirebaseFunctions._firestore_db.batch()
                doc_ref = clients_ref.document(docs[0].id)
                batch.update(doc_ref, {
                    "LastValidated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "DailyUrlCount": firestore.Increment(url_count)
                })
                batch.commit()
                return True
            return False
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Update validation error: {e}")
            return False
    
    @staticmethod
    def retry_pending_quota_updates(license_key, pending_updates):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                batch = FirebaseFunctions._firestore_db.batch()
                doc_ref = clients_ref.document(docs[0].id)
                batch.update(doc_ref, {
                    "LastValidated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "DailyUrlCount": firestore.Increment(len(pending_updates))
                })
                batch.commit()
                return True
            return False
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Retry quota error: {e}")
            return False
    
    @staticmethod
    def reset_daily_url_count(license_key):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                doc_ref = clients_ref.document(docs[0].id)
                doc_ref.update({"DailyUrlCount": 0})
                return True
            return False
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Reset count error: {e}")
            return False
    
    @staticmethod
    def generate_license_key(length=20):
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(length))
    
    @staticmethod
    def bind_license_to_device_in_firebase(license_key, device_id):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                doc_ref = clients_ref.document(docs[0].id)
                doc_ref.update({"DeviceID": device_id})
                return True
            return False
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error binding license to device in Firebase: {e}")
            return False

def check_license_eligibility(license_key, bot_name):
    try:
        expected_valid_date = datetime.datetime.now()
        current_device_id = get_device_id()
        if not current_device_id:
            return False, None, "Failed to generate device ID."
        client_data = FirebaseFunctions.get_client_data_by_license_key(license_key)
        is_eligible, message = FirebaseFunctions.is_client_eligible(client_data, bot_name, expected_valid_date, current_device_id)
        return is_eligible, client_data, message
    except Exception as e:
        st.session_state.error_log.append(f"{datetime.datetime.now()}: License check error: {e}")
        return False, None, f"License check error: {e}"

def should_reset_daily_count(client_data):
    last_validated = client_data.get("LastValidated", "")
    if not last_validated:
        return True
    try:
        last_validated_dt = datetime.datetime.strptime(last_validated, "%Y-%m-%d %H:%M:%S")
        return last_validated_dt.day != datetime.datetime.now().day
    except ValueError:
        return True

def validate_new_request(email, plan):
    existing_email = FirebaseFunctions.get_client_data_by_email(email)
    if existing_email:
        return False, "Email already exists. Use your existing account or contact support."
    if plan == "Free" and FirebaseFunctions.has_existing_free_account(email):
        return False, "You already have a free account registered. Please use your existing account or upgrade to a paid plan."
    return True, "OK"

def handle_form_submission(full_name, email, firecrawl_api_key, base_plan):
    if not all([full_name, email, firecrawl_api_key]):
        st.error("Please fill in all required fields including Firecrawl API Key.")
        return False
    is_valid, message = validate_new_request(email, base_plan)
    if not is_valid:
        st.error(f"{message}")
        return False
    device_id = get_device_id()
    if not device_id:
        st.error("Failed to generate device ID. Please try again.")
        return False
    client_data = {
        "ClientName": full_name,
        "ClientEmail": email,
        "Plan": base_plan,
        "ToolName": "Walmart Scraper",
        "FirecrawlApiKey": firecrawl_api_key,
        "DeviceID": device_id
    }
    doc_id = FirebaseFunctions.add_request(client_data)
    if not doc_id:
        st.error("Failed to save request to database. Please try again.")
        return False
    email_sent = send_request_email(full_name, email, "Walmart Scraper", base_plan)
    if not email_sent:
        st.error("Failed to send request email. Please try again or contact support.")
        FirebaseFunctions._firestore_db.collection("licenses").document(doc_id).delete()
        return False
    new_record = pd.DataFrame([{
        "Bot Name": "Walmart Scraper",
        "Sender Name": full_name,
        "Email": email,
        "Time": datetime.datetime.now().strftime("%I:%M %p"),
        "Date": datetime.datetime.now().strftime("%d-%m-%Y"),
        "Request Status": "Sent"
    }])
    st.session_state.records_df = pd.concat(
        [st.session_state.records_df, new_record], ignore_index=True
    )
    save_records(st.session_state.records_df)
    return {
        "full_name": full_name,
        "email": email,
        "bot": "Walmart Scraper",
        "plan": base_plan
    }

# Initialize Firebase
try:
    FirebaseFunctions.initialize_firebase()
except Exception as e:
    st.error(f"Firebase init error: {e}")
    st.stop()

# App state
if "app_state" not in st.session_state:
    st.session_state.app_state = "auth"
if "user_data" not in st.session_state:
    st.session_state.user_data = None
if "license_valid" not in st.session_state:
    st.session_state.license_valid = False
if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = []
if "user_tier" not in st.session_state:
    st.session_state.user_tier = "free"
if "daily_urls_used" not in st.session_state:
    st.session_state.daily_urls_used = 0
if "scraping_in_progress" not in st.session_state:
    st.session_state.scraping_in_progress = False
if "current_scraping_index" not in st.session_state:
    st.session_state.current_scraping_index = 0
if "selected_fields" not in st.session_state:
    st.session_state.selected_fields = [
        "Product Title", "Brand", "Price", "Availability", "Rating", "Review_count",
        "Description", "Highlights", "Specifications", "Variants", "Colors",
        "Sizes", "Seller", "Shipping", "Pickups", "Return_policy",
        "Images", "Videos", "Category", "Breadcrumbs", "Sourceurl"
    ]
if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = True
if "total_urls" not in st.session_state:
    st.session_state.total_urls = 0
if "scraped_count" not in st.session_state:
    st.session_state.scraped_count = 0
if "error_count" not in st.session_state:
    st.session_state.error_count = 0
if "all_data" not in st.session_state:
    st.session_state.all_data = []
if "firecrawl_api_key" not in st.session_state:
    st.session_state.firecrawl_api_key = ""
if "error_log" not in st.session_state:
    st.session_state.error_log = []
if "local_scraped_count" not in st.session_state:
    st.session_state.local_scraped_count = 0
if "pending_quota_updates" not in st.session_state:
    st.session_state.pending_quota_updates = []
if "rate_limit_delay" not in st.session_state:
    st.session_state.rate_limit_delay = 0.5
if "request_processed" not in st.session_state:
    st.session_state.request_processed = None

# Load records into session state
if "records_df" not in st.session_state:
    st.session_state.records_df = load_records()

# -------------------------------
# CSS for UI Styling
# -------------------------------
st.markdown("""
<style>
    /* Increase specificity to override Streamlit defaults */
    div.stButton > button {
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: bold;
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #000000 !important;
        min-height: 40px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    div.stButton > button:hover {
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #ffffff !important;
    }
    div.stTextInput > div > div > input {
        border-radius: 8px;
        padding: 10px;
        border: 1px solid #000000 !important;
        background-color: #f8f8f8 !important;
        color: #000000 !important;
    }
    div.stTextInput > div > div > input::placeholder {
        color: #666666 !important;
        opacity: 1;
    }
    div.stSelectbox > div > div > select {
        border-radius: 8px;
        padding: 10px;
        border: 1px solid #000000 !important;
        background-color: #f8f8f8 !important;
        color: #000000 !important;
    }
    div.stDownloadButton > button {
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: bold;
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #000000 !important;
        min-height: 40px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    div.stDownloadButton > button:hover {
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #ffffff !important;
    }
    div[data-testid="stFileUploaderDropzone"] button {
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: bold;
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #000000 !important;
        min-height: 40px !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
    }
    div[data-testid="stFileUploaderDropzone"] button:hover {
        transition: background-color 0.3s, border-color 0.3s;
        border: 1px solid #ffffff !important;
    }
    section[data-testid="stSidebar"] {
        padding: 20px;
        border-right: 1px solid #000000 !important;
    }
    div.stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 10px 20px;
        margin: 0 5px;
        border: 1px solid #000000 !important;
        border-bottom: none;
    }
    div.stTabs [data-baseweb="tab"]:hover {
        background-color: #e0e0e0 !important;
    }
    div.stTabs [aria-selected="true"] {
        background-color: #e0e0e0 !important;
    }
    div.stDataFrame {
        border-radius: 8px;
        border: 1px solid #000000 !important;
    }
    div.stAlert {
        border-radius: 8px;
        padding: 15px;
        border: 1px solid #000000 !important;
    }
    div.stExpander {
        border-radius: 8px;
        border: 1px solid #000000 !important;
    }
    div.stRadio > div {
        border-radius: 8px;
        border: 1px solid #000000 !important;
    }
    div.stForm {
        padding: 20px;
        border-radius: 8px;
        border: 1px solid #000000 !important;
    }
    div.stVideo {
        max-width: 600px;
        margin: 0 auto;
        display: block;
    }
    div.element-container {
        margin-bottom: 5px;
    }
    div.stProgress > div > div > div > div {
        background-color: #000000 !important;
    }
    div.stSpinner > div {
        border-top-color: #000000 !important;
        border-left-color: #000000 !important;
    }
    .custom-info {
        display: inline-block;
        padding: 5px 10px;
        border: 1px solid #000000 !important;
        border-radius: 5px;
        font-size: 14px;
        margin: 5px 0;
    }
    .upgrade-info {
        display: inline-block;
        padding: 10px;
        border: 1px solid #000000 !important;
        border-radius: 5px;
        font-size: 14px;
        margin: 10px 0;
    }
    .hidden-form {
        display: none;
    }
    .account-info {
        margin-top: 20px;
    }
    .account-info .name {
        font-weight: bold;
        cursor: pointer;
    }
    .account-info .details {
        display: none;
        margin-top: 5px;
    }
    .account-info.expanded .details {
        display: block;
    }
    /* Dark mode styles (default) */
    @media (prefers-color-scheme: dark) {
        .stApp {
            background-color: #121212 !important;
            color: #ffffff !important;
        }
        div.stButton > button {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #ffffff !important;
        }
        div.stButton > button:hover {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        div.stTextInput > div > div > input {
            background-color: #1E1E1E !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        div.stTextInput > div > div > input::placeholder {
            color: #CCCCCC !important;
            opacity: 1;
        }
        div.stSelectbox > div > div > select {
            background-color: #1E1E1E !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        div.stDownloadButton > button {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #ffffff !important;
        }
        div.stDownloadButton > button:hover {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        div[data-testid="stFileUploaderDropzone"] button {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #ffffff !important;
        }
        div[data-testid="stFileUploaderDropzone"] button:hover {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        section[data-testid="stSidebar"] {
            background-color: #1E1E1E !important;
            border-right-color: #ffffff !important;
        }
        div.stTabs [data-baseweb="tab"] {
            background-color: #1E1E1E !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        div.stTabs [data-baseweb="tab"]:hover {
            background-color: #333333 !important;
        }
        div.stTabs [aria-selected="true"] {
            background-color: #333333 !important;
        }
        div.stDataFrame {
            background-color: #1E1E1E !important;
            color: #ffffff !important;
        }
        div.stAlert {
            background-color: #333333 !important;
            color: #ffffff !important;
        }
        div.stExpander {
            background-color: #1E1E1E !important;
        }
        div.stRadio > div {
            background-color: #1E1E1E !important;
        }
        div.stCheckbox > label {
            color: #ffffff !important;
        }
        div.stForm {
            background-color: #1E1E1E !important;
        }
        div.stProgress > div > div > div > div {
            background-color: #000000 !important;
        }
        div.stSpinner > div {
            border-top-color: #000000 !important;
            border-left-color: #000000 !important;
        }
        .custom-info {
            background-color: #333333 !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        .upgrade-info {
            background-color: #444444 !important;
            color: #ffffff !important;
            border-color: #ffffff !important;
        }
        .account-info .name {
            color: #ffffff !important;
        }
        .account-info .details {
            color: #ffffff !important;
        }
    }
    /* Light mode styles */
    @media (prefers-color-scheme: light) {
        .stApp {
            background-color: #ffffff !important;
            color: #000000 !important;
            padding: 20px !important;
            max-width: 1200px !important;
            margin: 0 auto !important;
        }
        div.stButton > button {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #000000 !important;
        }
        div.stButton > button:hover {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        div.stTextInput > div > div > input {
            background-color: #f8f8f8 !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        div.stTextInput > div > div > input::placeholder {
            color: #666666 !important;
            opacity: 1;
        }
        div.stSelectbox > div > div > select {
            background-color: #f8f8f8 !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        div.stDownloadButton > button {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        div.stDownloadButton > button:hover {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #000000 !important;
        }
        div[data-testid="stFileUploaderDropzone"] button {
            background-color: #000000 !important;
            color: #ffffff !important;
            border-color: #000000 !important;
        }
        div[data-testid="stFileUploaderDropzone"] button:hover {
            background-color: #ffffff !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        section[data-testid="stSidebar"] {
            background-color: #f8f8f8 !important;
            border-right-color: #000000 !important;
        }
        div.stTabs [data-baseweb="tab"] {
            background-color: #f8f8f8 !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        div.stTabs [data-baseweb="tab"]:hover {
            background-color: #e0e0e0 !important;
        }
        div.stTabs [aria-selected="true"] {
            background-color: #e0e0e0 !important;
        }
        div.stDataFrame {
            background-color: #ffffff !important;
            color: #000000 !important;
        }
        div.stAlert {
            background-color: #f0f0f0 !important;
            color: #000000 !important;
        }
        div.stExpander {
            background-color: #f8f8f8 !important;
        }
        div.stRadio > div {
            background-color: #f8f8f8 !important;
        }
        div.stCheckbox > label {
            color: #000000 !important;
        }
        div.stForm {
            background-color: #f8f8f8 !important;
        }
        div.stProgress > div > div > div > div {
            background-color: #000000 !important;
        }
        div.stSpinner > div {
            border-top-color: #000000 !important;
            border-left-color: #000000 !important;
        }
        .custom-info {
            background-color: #f8f8f8 !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        .upgrade-info {
            background-color: #f0f0f0 !important;
            color: #000000 !important;
            border-color: #000000 !important;
        }
        .account-info .name {
            color: #000000 !important;
        }
        .account-info .details {
            color: #000000 !important;
        }
    }
    /* Wide Progress Bar Styling */
    div.stProgress {
        width: 100% !important;
        margin: 10px 0 !important;
    }
    div.stProgress > div {
        background-color: #333333 !important;
        border-radius: 8px !important;
    }
    div.stProgress > div > div {
        border-radius: 8px !important;
    }
</style>
""", unsafe_allow_html=True)

# -------------------------------
# Authentication Interface
# -------------------------------
# Auth Interface
if st.session_state.app_state == "auth":
    st.title("Walmart Product Scraper")
    st.markdown("**Professional-grade data extraction for market research and competitive analysis.**")
    
    tab1, tab2 = st.tabs(["Request License Key", "Existing User"])
    with tab1:
        st.subheader("Request License Key")
        with st.form("request_form"):
            col1, col2 = st.columns([3, 2])
            with col1:
                full_name = st.text_input("Full Name", placeholder="John Doe")
                email = st.text_input("Email Address", placeholder="john@example.com")
                firecrawl_api_key = st.text_input("Firecrawl API Key", placeholder="fc-...", type="password")
                bot_name = "Walmart Scraper"
                selected_plan = st.selectbox("Select Plan", [
                    "Free Plan - 50 URLs/Day (7 Days)",
                    "Basic Plan - 500 URLs/Day (1 Month)",
                    "Premium Plan - 2,500 URLs/Day (3 Months)",
                    "Enterprise Plan - 5,000 URLs/Day (1 Year)"
                ], help="Choose your subscription plan")
                base_plan = selected_plan.split(" - ")[0].replace(" Plan", "")
                
            with col2:
                registration_date = datetime.datetime.now().strftime("%Y-%m-%d")
                st.text_input("Registration Date", value=registration_date, disabled=True)

            agree_terms = st.checkbox("I agree to the Terms of Service and Privacy Policy")
            submitted = st.form_submit_button("Send Request")

            if submitted:
                if not agree_terms:
                    st.error("You must agree to the Terms of Service.")
                else:
                    # Simple flag to prevent double submission
                    submission_key = f"{full_name}_{email}_{base_plan}"
                    
                    if 'last_submission' not in st.session_state:
                        st.session_state.last_submission = None
                    
                    # Only process if it's a new submission
                    if st.session_state.last_submission != submission_key:
                        st.session_state.last_submission = submission_key
                        
                        # Immediately show success message
                        st.success(f"""
                        Request sent successfully!
                        - Name: {full_name}
                        - Email: {email}
                        - Bot: {bot_name}
                        - Plan: {base_plan}
                        
                        Your request has been submitted and will be processed shortly. 
                        You will receive an email confirmation once approved.
                        """)
                        
                        # Run the actual submission in the background (non-blocking)
                        try:
                            # This will run but we don't wait for it
                            import threading
                            
                            def background_submission():
                                try:
                                    handle_form_submission(full_name, email, firecrawl_api_key, base_plan)
                                except:
                                    pass  # Silently handle any errors
                            
                            thread = threading.Thread(target=background_submission)
                            thread.daemon = True
                            thread.start()
                            
                        except:
                            # If threading fails, just call it normally but don't wait
                            try:
                                handle_form_submission(full_name, email, firecrawl_api_key, base_plan)
                            except:
                                pass
                        
                        # Show previous requests if available
                        if hasattr(st.session_state, 'records_df') and not st.session_state.records_df.empty:
                            st.subheader("Previous Requests")
                            st.dataframe(st.session_state.records_df)
                    
                    else:
                        # Same submission - just show the success message again
                        st.info("Request has already been submitted. Please check your email for updates.")

        

        

    with tab2:
        st.subheader("Login to Your Account")
        license_key = st.text_input("License Key", type="password", placeholder="Enter your license key")
        if st.button("Validate License"):
            if license_key:
                with st.spinner("Validating license..."):
                    is_eligible, client_data, message = check_license_eligibility(license_key, "Walmart Scraper")
                    if is_eligible:
                        try:
                            device_id = get_device_id()
                            if not device_id:
                                st.error("Failed to generate device ID. Please try again.")
                            elif not FirebaseFunctions.bind_license_to_device_in_firebase(license_key, device_id):
                                st.error("Failed to bind license to device in Firebase. Please try again.")
                            else:
                                st.session_state.user_data = client_data
                                st.session_state.license_valid = True
                                st.session_state.app_state = "scraping"
                                st.session_state.firecrawl_api_key = client_data.get("FirecrawlApiKey", "")
                                plan = client_data.get("Plan", "Free").lower()
                                if any(word in plan for word in ["basic", "premium", "enterprise"]):
                                    st.session_state.user_tier = "premium"
                                else:
                                    st.session_state.user_tier = "free"
                                st.session_state.daily_urls_used = client_data.get("DailyUrlCount", 0)
                                st.session_state.local_scraped_count = 0
                                st.session_state.pending_quota_updates = []
                                if should_reset_daily_count(client_data):
                                    FirebaseFunctions.reset_daily_url_count(license_key)
                                    st.session_state.daily_urls_used = 0
                                    st.session_state.local_scraped_count = 0
                                st.success("License validated successfully!")
                                st.rerun()
                        except Exception as e:
                            st.error(f"Login failed: {e}")
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Login error: {e}")
                    else:
                        st.error(f"Login failed: {message}")
            else:
                st.error("Please enter your license key.")
# -------------------------------
# Main Scraper Interface
# -------------------------------
if st.session_state.app_state == "scraping":
    # Sidebar
    #st.sidebar.header("Scraper Settings")
    def sidebar_header(title, icon_path=None, subtitle=None, icon_width=24):
        html = '<div style="display: flex; align-items: center; margin-bottom: 10px;">'
        if icon_path and os.path.exists(icon_path):
            with open(icon_path, "rb") as f:
                data = f.read()
            #encoded = base64.b64encode(data).decode()
            #html += f"<img src='data:image/png;base64,{encoded}' width='{icon_width}' style='margin-right:8px;'>"
        html += f"<span style='font-size: 18px; font-weight: bold;'>{title}</span></div>"
        if subtitle:
            html += f"<div style='font-size: 12px; color: #666666; margin-bottom: 10px;'>{subtitle}</div>"
        st.sidebar.markdown(html, unsafe_allow_html=True)

    sidebar_header(
        title="Scraper Settings",
        icon_path="settings.png",
        subtitle="Customize your data extraction"
    )

    with st.sidebar.expander("Firecrawl API Settings", expanded=True):
        current_api_key = st.session_state.firecrawl_api_key
        new_api_key = st.text_input("Firecrawl API Key", value=current_api_key, type="password", placeholder="fc-...")
        st.session_state.rate_limit_delay = st.number_input("Rate Limit Delay (seconds)", min_value=0.1, max_value=2.0, value=st.session_state.rate_limit_delay, step=0.1)
        if st.button("Update API Key"):
            if new_api_key and new_api_key != current_api_key:
                try:
                    doc_ref = FirebaseFunctions._firestore_db.collection("licenses").document(st.session_state.user_data["id"])
                    doc_ref.update({"FirecrawlApiKey": new_api_key})
                    st.session_state.firecrawl_api_key = new_api_key
                    st.success("API Key updated successfully!")
                except Exception as e:
                    st.error(f"Error updating API key: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error updating API key: {e}")

    fields = [
        "Product Title", "Brand", "Price", "Availability", "Rating", "Review_count",
        "Description", "Highlights", "Specifications", "Variants", "Colors",
        "Sizes", "Seller", "Shipping", "Pickups", "Return_policy",
        "Images", "Videos", "Category", "Breadcrumbs", "Sourceurl"
    ]
    if "selected_fields" not in st.session_state:
        st.session_state.selected_fields = fields.copy()
    with st.sidebar.expander("Select Data Fields", expanded=True):
        for dc in fields:
            checked = st.checkbox(dc, value=dc in st.session_state.selected_fields, key=f"field_{dc}")
            if checked and dc not in st.session_state.selected_fields:
                st.session_state.selected_fields.append(dc)
            elif not checked and dc in st.session_state.selected_fields:
                st.session_state.selected_fields.remove(dc)
        st.info("💡 Unselect heavy fields (Images/Videos) for faster scraping.")

    # Sidebar Bottom: Account Info in Expander and Logout
    with st.sidebar.expander("📋 Account Info"):
        st.markdown(f"""
        **Account:** {st.session_state.user_data.get('ClientName', 'N/A')}  
        **Valid Until:** {st.session_state.user_data.get('ValidUntil', 'N/A')}  
        **Credits Used:** {st.session_state.daily_urls_used} / {st.session_state.user_data.get('DailyUrlLimit', 5000)}  
        **Local Scraped Count:** {st.session_state.local_scraped_count}  
        **Pending Quota Updates:** {len(st.session_state.pending_quota_updates)}
        """)
    if st.sidebar.button("Logout"):
        try:
            doc_ref = FirebaseFunctions._firestore_db.collection("licenses").document(st.session_state.user_data["id"])
            doc_ref.update({"DeviceID": ""})
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Logout error: {e}")
        st.session_state.app_state = "auth"
        st.session_state.user_data = None
        st.session_state.license_valid = False
        st.session_state.scraped_data = []
        st.session_state.daily_urls_used = 0
        st.session_state.local_scraped_count = 0
        st.session_state.pending_quota_updates = []
        st.session_state.firecrawl_api_key = ""
        st.session_state.error_log = []
        st.rerun()

    #st.title("Walmart Product Scraper")
    #st.markdown("**Extract structured product data with ease. Perfect for market research and catalog building.**")

    if not st.session_state.firecrawl_api_key:
        st.error("Firecrawl API key is required. Please enter it in the sidebar.")
        st.stop() 

    # Page Config
    image_path = "icon2.png"
    try:
        st.set_page_config(page_title="Walmart Product Scraper Powered by Umisoft",page_icon="logo.png", initial_sidebar_state="expanded")
    except:
        pass
        
    if os.path.exists(image_path):
        st.image(image_path, width=300)
    st.title("Walmart Product Scraper")
    st.markdown("**Extract structured product data with ease. Perfect for market research and catalog building.**")

    if not st.session_state.firecrawl_api_key:
        st.error("❌ Firecrawl API key is required. Please enter it in the sidebar.")
        st.stop()

    # Initialize Firecrawl
    try:
        firecrawl = Firecrawl(api_key=st.session_state.firecrawl_api_key)
    except Exception as e:
        st.error(f"❌ Error initializing scraper: {e}. Please check your Firecrawl API key.")
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error initializing Firecrawl: {e}")
        st.stop()

    # Main Page: URL Input
    st.subheader("Input Product URLs")
    max_urls = st.session_state.user_data.get("DailyUrlLimit", 5000)
    daily_urls_used = max(st.session_state.daily_urls_used, st.session_state.local_scraped_count)
    st.markdown(f'<div class="custom-info">Up to {max_urls} URLs/day (Used: {daily_urls_used}/{max_urls})</div>', unsafe_allow_html=True)

    input_method = st.radio("Input Method:", ("Upload CSV", "Manual Entry"), horizontal=True)
    urls = []
    if input_method == "Upload CSV":
        uploaded_file = st.file_uploader("Upload CSV with URLs", type=["csv"], help="CSV must have a 'url' column")
        if uploaded_file:
            with st.spinner("Loading CSV..."):
                try:
                    df = pd.read_csv(uploaded_file)
                    if 'url' in df.columns:
                        urls = df['url'].dropna().tolist()
                        if len(urls) > (max_urls - daily_urls_used):
                            st.error(f"❌ This would exceed the remaining {max_urls - daily_urls_used} URL limit (Daily used: {daily_urls_used}).")
                            urls = []
                        else:
                            st.markdown(f'<div class="custom-info">Successfully loaded {len(urls)} URLs</div>', unsafe_allow_html=True)
                    else:
                        st.error("❌ CSV must have a 'url' column")
                except Exception as e:
                    st.error(f"❌ Failed to read CSV: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error reading CSV: {e}")
    else:
        url_text = st.text_area("Enter URLs (one per line)", placeholder="https://www.walmart.com/ip/...", height=150)
        if url_text:
            with st.spinner("Processing input..."):
                try:
                    urls = [line.strip() for line in url_text.splitlines() if line.strip()]
                    if len(urls) > (max_urls - daily_urls_used):
                        st.error(f"❌ This would exceed the remaining {max_urls - daily_urls_used} URL limit (Daily used: {daily_urls_used}).")
                        urls = []
                    else:
                        st.markdown(f'<div class="custom-info">Successfully loaded {len(urls)} URLs</div>', unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"❌ Error processing URLs: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error processing URLs: {e}")

    if urls:
        st.write(f"URLs to scrape:", urls)

    # Scraping Button with Live Preview
    if st.button("Start Scraping", disabled=not urls) and not st.session_state.scraping_in_progress:
        st.session_state.scraping_in_progress = True
        st.session_state.current_scraping_index = 0
        st.session_state.total_urls = len(urls)
        st.session_state.scraped_count = 0
        st.session_state.error_count = 0
        st.session_state.local_scraped_count = 0
        st.session_state.pending_quota_updates = []
        st.session_state.all_data = []
        st.session_state.error_log = []
        st.session_state.progress_bar = st.progress(0)
        st.session_state.preview_df = st.empty()
        st.session_state.status_text = st.empty()
        st.session_state.start_time = time.time()
        #mac_address = get_mac_address()
        avg_time_per_url = 0

    if st.session_state.scraping_in_progress:
        i = st.session_state.current_scraping_index
        if i < st.session_state.total_urls:
            if st.session_state.daily_urls_used >= max_urls:
                st.error("❌ Daily URL limit reached. Cannot scrape more.")
                st.session_state.scraping_in_progress = False
                st.rerun()
            with st.spinner(f"Scraping URL {i+1}/{st.session_state.total_urls}: {urls[i]}"):
                scraped_success = False
                row = None
                try:
                    prompt = f"""
                    Extract structured product details from this page.
                    Return ONLY these fields as valid JSON: {', '.join(st.session_state.selected_fields)}.
                    """
                    res = firecrawl.extract(urls=[urls[i]], prompt=prompt)
                    if res and res.data:
                        data = res.data
                        data["Sourceurl"] = urls[i]
                        row = {
                            f: (data.get(f, "") if not isinstance(data.get(f, ""), list)
                                else "; ".join(map(str, data.get(f, ""))))
                            for f in st.session_state.selected_fields
                        }
                        scraped_success = True
                    else:
                        st.session_state.error_log.append(f"{datetime.datetime.now()}: Skipped URL {urls[i]}: No data returned")
                        st.warning(f"⚠️ Skipped URL {urls[i]}: No data returned.")
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Skipped URL {urls[i]}: Error - {str(e)}")
                    st.warning(f"⚠️ Skipped URL {urls[i]}: Error - {str(e)}.")

                if row is not None:
                    st.session_state.all_data.append(row)
                    st.session_state.scraped_count += 1
                    st.session_state.local_scraped_count += 1
                    # Queue quota update for batch processing
                    license_key = st.session_state.user_data.get("LicenseKey", "")
                    try:
                        updated = FirebaseFunctions.update_client_validation(license_key,  1)
                        if updated:
                            st.session_state.daily_urls_used += 1
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota updated successfully for URL {urls[i]}")
                        else:
                            st.session_state.pending_quota_updates.append(urls[i])
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota update failed for URL {urls[i]}: Update returned False")
                            st.warning(f"⚠️ Failed to update quota for URL {urls[i]}. Added to pending updates.")
                    except Exception as e:
                        st.session_state.pending_quota_updates.append(urls[i])
                        st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota update failed for URL {urls[i]}: {str(e)}")
                        st.warning(f"⚠️ Failed to update quota for URL {urls[i]}: {str(e)}. Added to pending updates.")
                else:
                    st.session_state.error_count += 1
                
                
            
            # Update live preview based on plan
            if st.session_state.all_data:
                try:
                    temp_df = pd.DataFrame(st.session_state.all_data)
                    plan_limits = {
                        "free": 50,
                        "basic": 500,
                        "premium": 2500,
                        "enterprise": 5000
                    }
                    display_limit = min(len(temp_df), plan_limits.get(st.session_state.user_tier, 50))
                    st.session_state.preview_df.dataframe(temp_df.head(display_limit), use_container_width=True, height=200)
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Preview update error: {str(e)}")
            
            # Update progress
            progress = (i + 1) / st.session_state.total_urls
            st.session_state.progress_bar.progress(progress)
            percentage = int(progress * 100)
            
            # Estimated time
            try:
                elapsed_time = time.time() - st.session_state.start_time
                if i + 1 > 0:
                    avg_time_per_url = elapsed_time / (i + 1)
                remaining_urls = st.session_state.total_urls - (i + 1)
                estimated_remaining = avg_time_per_url * remaining_urls
                eta_minutes = int(estimated_remaining // 60)
                eta_seconds = int(estimated_remaining % 60)
                eta_str = f"ETA: {eta_minutes}m {eta_seconds}s" if estimated_remaining > 0 else ""
            except Exception as e:
                eta_str = "ETA: Calculating..."
                st.session_state.error_log.append(f"{datetime.datetime.now()}: ETA calculation error: {str(e)}")
            
            st.session_state.status_text.text(f"Status: {percentage}% complete | {st.session_state.scraped_count} scraped | {st.session_state.error_count} errors | {eta_str}")
        
            st.session_state.current_scraping_index += 1
            st.rerun()

        if st.session_state.current_scraping_index >= st.session_state.total_urls:
            st.session_state.scraping_in_progress = False
            if st.session_state.all_data:
                try:
                    st.session_state.scraped_data = st.session_state.all_data
                    # Retry pending quota updates
                    if st.session_state.pending_quota_updates:
                        with st.spinner("Applying pending quota updates..."):
                            success = FirebaseFunctions.retry_pending_quota_updates(
                                st.session_state.user_data.get("LicenseKey", ""),
                                #get_mac_address(),
                                st.session_state.pending_quota_updates
                            )
                            if success:
                                st.session_state.daily_urls_used += len(st.session_state.pending_quota_updates)
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Successfully applied {len(st.session_state.pending_quota_updates)} pending quota updates")
                                st.session_state.pending_quota_updates = []
                            else:
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Failed to apply {len(st.session_state.pending_quota_updates)} pending quota updates")
                    # Refetch latest daily count
                    license_key = st.session_state.user_data.get("LicenseKey", "")
                    client_data = None
                    retry_delay = 1
                    max_db_retries = 7
                    db_retry_count = 0
                    while client_data is None and db_retry_count < max_db_retries:
                        try:
                            client_data = FirebaseFunctions.get_client_data_by_license_key(license_key)
                            if client_data:
                                st.session_state.daily_urls_used = client_data.get("DailyUrlCount", st.session_state.daily_urls_used)
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Final quota refetched successfully: {st.session_state.daily_urls_used}")
                            else:
                                raise ValueError("No client data found")
                        except Exception as e:
                            db_retry_count += 1
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Final quota fetch attempt {db_retry_count}: {str(e)}")
                            if db_retry_count < max_db_retries:
                                time.sleep(retry_delay)
                                retry_delay = min(retry_delay * 2, 20)
                            else:
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Failed to fetch final quota after {max_db_retries} retries: {str(e)}")
                    st.success(f"🎉 Scraping completed! Success: {st.session_state.scraped_count}, Errors: {st.session_state.error_count}, URLs Used: {st.session_state.daily_urls_used}, Local Scraped: {st.session_state.local_scraped_count}")
                    if st.session_state.error_log:
                        with st.expander("Debug: Error Log", expanded=False):
                            st.write(st.session_state.error_log)
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Finalization error: {str(e)}")
            else:
                st.warning("⚠️ No data extracted. Check URLs.")
            st.rerun()

    # Display Final Scraped Data
    if st.session_state.scraped_data:
        try:
            df = pd.DataFrame(st.session_state.scraped_data)
            plan_limits = {
                "free": 50,
                "basic": 500,
                "premium": 2500,
                "enterprise": 5000
            }
            display_limit = min(len(df), plan_limits.get(st.session_state.user_tier, 50))
            st.subheader("📊 Data Preview")
            st.dataframe(df.head(display_limit), use_container_width=True, height=400)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.download_button(
                    "⬇Download Sample",
                    df.head(50).to_csv(index=False).encode("utf-8"),
                    "sample_walmart_products.csv",
                    "text/csv",
                    help="Downloads up to 50 rows"
                )
            with col2:
                if st.session_state.user_tier == "premium":
                    st.download_button(
                        "⬇Download Full Data",
                        df.to_csv(index=False).encode("utf-8"),
                        "walmart_products.csv",
                        "text/csv"
                    )
                else:
                    st.button("⬇Download Full Data (Premium Only)",
                             help="Upgrade to premium for full datasets")
            with col3:
                if st.button("🗑️ Clear Data"):
                    st.session_state.scraped_data = []
                    st.success("🧹 Data cleared!")
                    st.rerun()
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Data display error: {str(e)}")

    # Tutorial and Footer
    st.markdown("---")
    st.subheader("📺 Tutorial Video")
    st.video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", format="video/mp4")
    st.markdown("---")
    st.markdown("""
    <div style="text-align: center; font-size: 14px; padding: 20px 0;">
        <strong>Umisoft Walmart Scraper</strong><br>
        Empower your business with real-time product insights.<br>
        Premium features include unlimited scraping and dedicated support.<br>
        Contact: <a href="mailto:support@umisoft.com" style="text-decoration: none;">support@umisoft.com</a> | © 2025 Umisoft Ltd. | Version 2.0
    </div>
    """, unsafe_allow_html=True)
