import datetime
import os
import time
import string
import random
import streamlit as st
import pandas as pd
from firecrawl import Firecrawl
import firebase_admin
from firebase_admin import credentials, firestore
import json
import streamlit.components.v1 as components

# Plan limits configuration
PLAN_LIMITS = {
    "Free": {"daily_limit": 50, "valid_days": 7},
    "Basic": {"daily_limit": 500, "valid_days": 30},
    "Premium": {"daily_limit": 2500, "valid_days": 90},
    "Enterprise": {"daily_limit": 5000, "valid_days": 365}
}

LOCAL_LICENSE_FILE = ".walmart_scraper_license"

def get_remote_ip() -> str:
    """Get client's public IP using JavaScript and api.ipify.org."""
    if "client_ip" not in st.session_state:
        # Inject JavaScript to fetch IP
        components.html(
            """
            <script>
            fetch('https://api.ipify.org?format=json')
                .then(response => response.json())
                .then(data => {
                    // Store IP in sessionStorage to persist across reruns
                    sessionStorage.setItem('client_ip', data.ip);
                    // Send IP to Streamlit via postMessage
                    parent.window.postMessage({type: 'streamlit:setComponentValue', value: data.ip}, '*');
                })
                .catch(err => {
                    console.error('IP fetch error:', err);
                    parent.window.postMessage({type: 'streamlit:setComponentValue', value: null}, '*');
                });
            </script>
            """,
            height=0,
            width=0
        )
        # Fallback: Check sessionStorage if available
        ip_from_storage = st_javascript("sessionStorage.getItem('client_ip')")
        if ip_from_storage:
            st.session_state.client_ip = ip_from_storage
            return ip_from_storage
        return None
    return st.session_state.client_ip

def st_javascript(script):
    """Execute JavaScript and return result."""
    components.html(f"<script>parent.window.postMessage({{type: 'streamlit:setComponentValue', value: {script}}}, '*')</script>", height=0)
    # Wait briefly for message to process
    time.sleep(0.1)
    return st.session_state.get("client_ip", None)

class FirebaseFunctions:
    _firestore_db = None
    
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
            if 'error_log' in st.session_state:
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
            st.error(f"Get client by email error: {e}")
            return None
    
    @staticmethod
    def has_existing_free_account(ip_address):
        if not ip_address:
            return False
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientIP", "==", ip_address).where("Plan", "==", "Free")
            docs = list(query.stream())
            return len(docs) > 0
        except Exception as e:
            if 'error_log' in st.session_state:
                st.session_state.error_log.append(f"{datetime.datetime.now()}: Check existing free account error: {e}")
            return False
    
    @staticmethod
    def is_client_eligible(client_data, expected_bot_name, expected_valid_date, current_ip):
        if client_data is None:
            return False
        if str(client_data.get("ToolName", "")) != str(expected_bot_name):
            return False
        if str(client_data.get("AccessStatus", "")) != "ON":
            return False
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
                return False
        except Exception as e:
            st.error(f"Date validation error: {e}")
            return False
        registered_ip = client_data.get("ClientIP", "")
        if registered_ip and current_ip and registered_ip != current_ip:
            return False
        return True
    
    @staticmethod
    def add_new_client(client_data, ip_address):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            license_key = FirebaseFunctions.generate_license_key()
            client_data["LicenseKey"] = license_key
            client_data["ClientIP"] = ip_address if ip_address else ""
            client_data["RegistrationDate"] = datetime.datetime.now().strftime("%Y-%m-%d")
            client_data["LastValidated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            client_data["DailyUrlCount"] = 0
            plan = client_data.get("Plan", "Free")
            plan_config = PLAN_LIMITS.get(plan, PLAN_LIMITS["Free"])
            client_data["DailyUrlLimit"] = plan_config["daily_limit"]
            client_data["ValidUntil"] = (datetime.datetime.now() + datetime.timedelta(days=plan_config["valid_days"])).strftime("%Y-%m-%d")
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            new_doc_ref = clients_ref.document()
            new_doc_ref.set(client_data)
            return license_key, new_doc_ref.id
        except Exception as e:
            st.error(f"Add client error: {e}")
            return None, None
    
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
            if 'error_log' in st.session_state:
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
            if 'error_log' in st.session_state:
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
            st.error(f"Reset count error: {e}")
            return False
    
    @staticmethod
    def generate_license_key(length=20):
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

def check_license_eligibility(license_key, bot_name):
    try:
        current_ip = get_remote_ip()
        expected_valid_date = datetime.datetime.now()
        client_data = FirebaseFunctions.get_client_data_by_license_key(license_key)
        if not client_data:
            return False, None
        is_eligible = FirebaseFunctions.is_client_eligible(client_data, bot_name, expected_valid_date, current_ip)
        return is_eligible, client_data
    except Exception as e:
        st.error(f"License check error: {e}")
        return False, None

def should_reset_daily_count(client_data):
    last_validated = client_data.get("LastValidated", "")
    if not last_validated:
        return True
    try:
        last_validated_dt = datetime.datetime.strptime(last_validated, "%Y-%m-%d %H:%M:%S")
        return last_validated_dt.day != datetime.datetime.now().day
    except ValueError:
        return True

def validate_new_registration(email, plan, ip_address):
    existing_email = FirebaseFunctions.get_client_data_by_email(email)
    if existing_email:
        return False, "Email already exists. Login with existing key."
    if ip_address and plan == "Free" and FirebaseFunctions.has_existing_free_account(ip_address):
        return False, "You already have a free account registered from this IP. Please use your existing account or upgrade to a paid plan."
    return True, "OK"

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
if "client_ip" not in st.session_state:
    st.session_state.client_ip = None

# Auto-login with saved license key
if st.session_state.app_state == "auth" and st.session_state.user_data is None:
    if os.path.exists(LOCAL_LICENSE_FILE):
        with open(LOCAL_LICENSE_FILE, "r") as f:
            saved_key = f.read().strip()
        if saved_key:
            with st.spinner("Auto-validating saved license..."):
                try:
                    is_eligible, client_data = check_license_eligibility(saved_key, "walmart_scraper")
                    if is_eligible:
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
                            FirebaseFunctions.reset_daily_url_count(saved_key)
                            st.session_state.daily_urls_used = 0
                            st.session_state.local_scraped_count = 0
                        st.rerun()
                    else:
                        os.remove(LOCAL_LICENSE_FILE)
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Auto-login error: {e}")

# CSS (same as before)
st.markdown("""
<style>
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

# Auth Interface
if st.session_state.app_state == "auth":
    st.title("Walmart Product Scraper")
    st.markdown("**Professional-grade data extraction for market research and competitive analysis.**")
    
    tab1, tab2 = st.tabs(["New Registration", "Existing User"])
    
    with tab1:
        st.subheader("Create Your Account")
        current_ip = get_remote_ip()
        if not current_ip:
            st.warning("Unable to detect your IP address. Registration will proceed without IP restriction, but multiple free accounts may be blocked later. Contact support if issues persist.")
            st.session_state.error_log.append(f"{datetime.datetime.now()}: IP detection failed during registration")
        with st.form("registration_form"):
            col1, col2 = st.columns([3, 2])
            with col1:
                full_name = st.text_input("Full Name", placeholder="John Doe")
                email = st.text_input("Email Address", placeholder="john@example.com")
                firecrawl_api_key = st.text_input("Firecrawl API Key", placeholder="fc-...", type="password")
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
                st.text_input("Detected IP", value=current_ip or "Not detected", disabled=True, help="Used to prevent multiple free accounts")
            agree_terms = st.checkbox("I agree to the Terms of Service and Privacy Policy")
            dont_ask = st.checkbox("Don't ask again on this device", value=True)
            submitted = st.form_submit_button("Create Account")
            if submitted:
                with st.spinner("Creating account..."):
                    if not all([full_name, email, firecrawl_api_key]):
                        st.error("Please fill in all required fields including Firecrawl API Key.")
                    elif not agree_terms:
                        st.error("You must agree to the Terms of Service.")
                    else:
                        is_valid, message = validate_new_registration(email, base_plan, current_ip)
                        if not is_valid:
                            st.error(f"{message}")
                        else:
                            try:
                                client_data = {
                                    "ClientName": full_name,
                                    "ClientEmail": email,
                                    "ClientIP": current_ip if current_ip else "",
                                    "RegistrationDate": registration_date,
                                    "Plan": base_plan,
                                    "ToolName": "walmart_scraper",
                                    "AccessStatus": "ON",
                                    "DailyUrlCount": 0,
                                    "FirecrawlApiKey": firecrawl_api_key
                                }
                                license_key, doc_id = FirebaseFunctions.add_new_client(client_data, current_ip)
                                if license_key:
                                    client_data["LicenseKey"] = license_key
                                    client_data["id"] = doc_id
                                    st.session_state.user_data = client_data
                                    st.session_state.license_valid = True
                                    st.session_state.app_state = "scraping"
                                    st.session_state.firecrawl_api_key = firecrawl_api_key
                                    if dont_ask:
                                        with open(LOCAL_LICENSE_FILE, "w") as f:
                                            f.write(license_key)
                                    st.success(f"""
                                    Account Created Successfully!
                                    
                                    **Plan:** {selected_plan}  
                                    **License Key:** `{license_key}`
                                    
                                    Important: Save your license key securely. This license is {'bound to your current IP address' if current_ip else 'not bound to an IP address due to detection issues'}.
                                    """)
                                    st.session_state.error_log.append(f"{datetime.datetime.now()}: New account registered - Email: {email}, Plan: {base_plan}, IP: {current_ip or 'None'}")
                                    st.rerun()
                                else:
                                    st.error("Failed to create account. Please try again or contact support.")
                            except Exception as e:
                                st.error(f"Account creation failed: {e}")
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Account creation error: {e}")
    
    with tab2:
        st.subheader("Login to Your Account")
        current_ip = get_remote_ip()
        if not current_ip:
            st.warning("Unable to detect your IP address. Login will proceed, but contact support if you encounter issues.")
            st.session_state.error_log.append(f"{datetime.datetime.now()}: IP detection failed during login")
        license_key = st.text_input("License Key", type="password", placeholder="Enter your license key")
        st.text_input("Detected IP", value=current_ip or "Not detected", disabled=True, help="Used for security")
        dont_ask = st.checkbox("Don't ask again on this device")
        if st.button("Validate License"):
            if license_key:
                with st.spinner("Validating license..."):
                    is_eligible, client_data = check_license_eligibility(license_key, "walmart_scraper")
                    if is_eligible:
                        if dont_ask:
                            with open(LOCAL_LICENSE_FILE, "w") as f:
                                f.write(license_key)
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
                    else:
                        st.error("Invalid license key or IP address mismatch.")
            else:
                st.error("Please enter your license key.")
    
    st.stop()

# Scraper Interface (unchanged from previous version - add your existing scraping code here)
if st.session_state.app_state == "scraping":
    st.sidebar.header("Scraper Settings")
    # ... (your existing sidebar and scraping code here)
    pass
