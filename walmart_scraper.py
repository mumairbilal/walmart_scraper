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

# Plan limits configuration
PLAN_LIMITS = {
    "Free": {"daily_limit": 50, "valid_days": 7},
    "Basic": {"daily_limit": 500, "valid_days": 30},
    "Premium": {"daily_limit": 2500, "valid_days": 90},
    "Enterprise": {"daily_limit": 5000, "valid_days": 365}
}

LOCAL_LICENSE_FILE = ".walmart_scraper_license"

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
    def is_client_eligible(client_data, expected_bot_name, expected_valid_date):
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
        # Check if the license key is already in use
        if client_data.get("ActiveSession", False):
            return False
        return True
    
    @staticmethod
    def add_new_client(client_data):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            license_key = FirebaseFunctions.generate_license_key()
            client_data["LicenseKey"] = license_key
            client_data["RegistrationDate"] = datetime.datetime.now().strftime("%Y-%m-%d")
            client_data["LastValidated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            client_data["DailyUrlCount"] = 0
            plan = client_data.get("Plan", "Free")
            plan_config = PLAN_LIMITS.get(plan, PLAN_LIMITS["Free"])
            client_data["DailyUrlLimit"] = plan_config["daily_limit"]
            client_data["ValidUntil"] = (datetime.datetime.now() + datetime.timedelta(days=plan_config["valid_days"])).strftime("%Y-%m-%d")
            client_data["ActiveSession"] = False
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            new_doc_ref = clients_ref.document()
            new_doc_ref.set(client_data)
            return license_key, new_doc_ref.id
        except Exception as e:
            st.error(f"Add client error: {e}")
            return None, None
    
    @staticmethod
    def set_active_session(license_key, active=True):
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("LicenseKey", "==", license_key)
            docs = list(query.stream())
            if len(docs) > 0:
                doc_ref = clients_ref.document(docs[0].id)
                doc_ref.update({"ActiveSession": active})
                return True
            return False
        except Exception as e:
            if 'error_log' in st.session_state:
                st.session_state.error_log.append(f"{datetime.datetime.now()}: Set active session error: {e}")
            return False
    
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
        expected_valid_date = datetime.datetime.now()
        client_data = FirebaseFunctions.get_client_data_by_license_key(license_key)
        if not client_data:
            return False, None
        is_eligible = FirebaseFunctions.is_client_eligible(client_data, bot_name, expected_valid_date)
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

def validate_new_registration(email):
    existing_email = FirebaseFunctions.get_client_data_by_email(email)
    if existing_email:
        return False, "Email already exists. Login with existing key."
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
                        FirebaseFunctions.set_active_session(saved_key, True)
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

# CSS
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
                        is_valid, message = validate_new_registration(email)
                        if not is_valid:
                            st.error(f"{message}")
                        else:
                            try:
                                client_data = {
                                    "ClientName": full_name,
                                    "ClientEmail": email,
                                    "RegistrationDate": registration_date,
                                    "Plan": base_plan,
                                    "ToolName": "walmart_scraper",
                                    "AccessStatus": "ON",
                                    "DailyUrlCount": 0,
                                    "FirecrawlApiKey": firecrawl_api_key,
                                    "ActiveSession": False
                                }
                                license_key, doc_id = FirebaseFunctions.add_new_client(client_data)
                                if license_key:
                                    client_data["LicenseKey"] = license_key
                                    client_data["id"] = doc_id
                                    FirebaseFunctions.set_active_session(license_key, True)
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
                                    
                                    Important: Save your license key securely. Only one device can use this license at a time.
                                    """)
                                    st.session_state.error_log.append(f"{datetime.datetime.now()}: New account registered - Email: {email}, Plan: {base_plan}")
                                    st.rerun()
                                else:
                                    st.error("Failed to create account. Please try again or contact support.")
                            except Exception as e:
                                st.error(f"Account creation failed: {e}")
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Account creation error: {e}")
    
    with tab2:
        st.subheader("Login to Your Account")
        license_key = st.text_input("License Key", type="password", placeholder="Enter your license key")
        dont_ask = st.checkbox("Don't ask again on this device")
        if st.button("Validate License"):
            if license_key:
                with st.spinner("Validating license..."):
                    is_eligible, client_data = check_license_eligibility(license_key, "walmart_scraper")
                    if is_eligible:
                        FirebaseFunctions.set_active_session(license_key, True)
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
                        st.error("Invalid license key or license already in use on another device.")
            else:
                st.error("Please enter your license key.")
    
    st.stop()

# Scraper Interface
if st.session_state.app_state == "scraping":
    st.sidebar.header("Scraper Settings")
    def sidebar_header(title, subtitle=None):
        html = f'<div style="display: flex; align-items: center; margin-bottom: 10px;"><span style="font-size: 18px; font-weight: bold;">{title}</span></div>'
        if subtitle:
            html += f'<div style="font-size: 12px; color: #666666; margin-bottom: 10px;">{subtitle}</div>'
        st.sidebar.markdown(html, unsafe_allow_html=True)

    sidebar_header(
        title="Scraper Settings",
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
    with st.sidebar.expander("Select Data Fields", expanded=True):
        for dc in fields:
            checked = st.checkbox(dc, value=dc in st.session_state.selected_fields, key=f"field_{dc}")
            if checked and dc not in st.session_state.selected_fields:
                st.session_state.selected_fields.append(dc)
            elif not checked and dc in st.session_state.selected_fields:
                st.session_state.selected_fields.remove(dc)
        st.info("Unselect heavy fields (Images/Videos) for faster scraping.")

    with st.sidebar.expander("Account Info"):
        st.markdown(f"""
        **Account:** {st.session_state.user_data.get('ClientName', 'N/A')}  
        **Valid Until:** {st.session_state.user_data.get('ValidUntil', 'N/A')}  
        **Credits Used:** {st.session_state.daily_urls_used} / {st.session_state.user_data.get('DailyUrlLimit', 5000)}  
        **Local Scraped Count:** {st.session_state.local_scraped_count}  
        **Pending Quota Updates:** {len(st.session_state.pending_quota_updates)}
        """)
    
    if st.sidebar.button("Logout"):
        if os.path.exists(LOCAL_LICENSE_FILE):
            os.remove(LOCAL_LICENSE_FILE)
        FirebaseFunctions.set_active_session(st.session_state.user_data.get("LicenseKey", ""), False)
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

    try:
        st.set_page_config(page_title="Walmart Product Scraper", layout="wide", initial_sidebar_state="expanded")
    except:
        pass
    st.title("Walmart Product Scraper")
    st.markdown("**Extract structured product data with ease. Perfect for market research and catalog building.**")

    if not st.session_state.firecrawl_api_key:
        st.error("Firecrawl API key is required. Please enter it in the sidebar.")
        st.stop()

    try:
        firecrawl = Firecrawl(api_key=st.session_state.firecrawl_api_key)
    except Exception as e:
        st.error(f"Error initializing scraper: {e}. Please check your Firecrawl API key.")
        st.session_state.error_log.append(f"{datetime.datetime.now()}: Error initializing Firecrawl: {e}")
        st.stop()

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
                            st.error(f"This would exceed the remaining {max_urls - daily_urls_used} URL limit (Daily used: {daily_urls_used}).")
                            urls = []
                        else:
                            st.markdown(f'<div class="custom-info">Successfully loaded {len(urls)} URLs</div>', unsafe_allow_html=True)
                    else:
                        st.error("CSV must have a 'url' column")
                except Exception as e:
                    st.error(f"Failed to read CSV: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error reading CSV: {e}")
    else:
        url_text = st.text_area("Enter URLs (one per line)", placeholder="https://www.walmart.com/ip/...", height=150)
        if url_text:
            with st.spinner("Processing input..."):
                try:
                    urls = [line.strip() for line in url_text.splitlines() if line.strip()]
                    if len(urls) > (max_urls - daily_urls_used):
                        st.error(f"This would exceed the remaining {max_urls - daily_urls_used} URL limit (Daily used: {daily_urls_used}).")
                        urls = []
                    else:
                        st.markdown(f'<div class="custom-info">Successfully loaded {len(urls)} URLs</div>', unsafe_allow_html=True)
                except Exception as e:
                    st.error(f"Error processing URLs: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error processing URLs: {e}")

    if urls:
        st.write(f"URLs to scrape: {len(urls)} URLs")

    if st.button("Start Scraping", disabled=not urls or st.session_state.scraping_in_progress):
        st.session_state.scraping_in_progress = True
        st.session_state.current_scraping_index = 0
        st.session_state.total_urls = len(urls)
        st.session_state.scraped_count = 0
        st.session_state.error_count = 0
        st.session_state.local_scraped_count = 0
        st.session_state.all_data = []
        license_key = st.session_state.user_data.get("LicenseKey", "")

        progress_bar = st.progress(0)
        status_text = st.empty()
        error_container = st.empty()

        for i in range(len(urls)):
            if not st.session_state.scraping_in_progress:
                break
            st.session_state.current_scraping_index = i
            url = urls[i]
            status_text.text(f"Scraping URL {i + 1}/{len(urls)}: {url}")
            try:
                if st.session_state.local_scraped_count + len(st.session_state.pending_quota_updates) >= max_urls:
                    st.error(f"Daily URL limit of {max_urls} reached.")
                    break
                response = firecrawl.scrape_url(url, params={"pageOptions": {"onlyMainContent": False}})
                if response.get("success") and response.get("data"):
                    data = response["data"]
                    scraped_item = {}
                    for field in st.session_state.selected_fields:
                        if field == "Product Title":
                            scraped_item[field] = data.get("title", "")
                        elif field == "Brand":
                            scraped_item[field] = data.get("metadata", {}).get("brand", "")
                        elif field == "Price":
                            scraped_item[field] = data.get("metadata", {}).get("price", "")
                        elif field == "Availability":
                            scraped_item[field] = data.get("metadata", {}).get("availability", "")
                        elif field == "Rating":
                            scraped_item[field] = data.get("metadata", {}).get("rating", "")
                        elif field == "Review_count":
                            scraped_item[field] = data.get("metadata", {}).get("reviewCount", "")
                        elif field == "Description":
                            scraped_item[field] = data.get("content", "")
                        elif field == "Highlights":
                            scraped_item[field] = data.get("metadata", {}).get("highlights", [])
                        elif field == "Specifications":
                            scraped_item[field] = data.get("metadata", {}).get("specifications", {})
                        elif field == "Variants":
                            scraped_item[field] = data.get("metadata", {}).get("variants", [])
                        elif field == "Colors":
                            scraped_item[field] = data.get("metadata", {}).get("colors", [])
                        elif field == "Sizes":
                            scraped_item[field] = data.get("metadata", {}).get("sizes", [])
                        elif field == "Seller":
                            scraped_item[field] = data.get("metadata", {}).get("seller", "")
                        elif field == "Shipping":
                            scraped_item[field] = data.get("metadata", {}).get("shipping", "")
                        elif field == "Pickups":
                            scraped_item[field] = data.get("metadata", {}).get("pickups", "")
                        elif field == "Return_policy":
                            scraped_item[field] = data.get("metadata", {}).get("returnPolicy", "")
                        elif field == "Images":
                            scraped_item[field] = data.get("metadata", {}).get("images", [])
                        elif field == "Videos":
                            scraped_item[field] = data.get("metadata", {}).get("videos", [])
                        elif field == "Category":
                            scraped_item[field] = data.get("metadata", {}).get("category", "")
                        elif field == "Breadcrumbs":
                            scraped_item[field] = data.get("metadata", {}).get("breadcrumbs", [])
                        elif field == "Sourceurl":
                            scraped_item[field] = url
                    st.session_state.all_data.append(scraped_item)
                    st.session_state.local_scraped_count += 1
                    st.session_state.scraped_count += 1
                    st.session_state.pending_quota_updates.append(url)
                    progress_bar.progress((i + 1) / len(urls))
                    try:
                        if FirebaseFunctions.update_client_validation(license_key, 1):
                            st.session_state.daily_urls_used += 1
                            st.session_state.pending_quota_updates.pop()
                        else:
                            st.warning(f"Failed to update quota for URL {url}. Will retry later.")
                    except Exception as e:
                        st.warning(f"Quota update error for {url}: {e}. Will retry later.")
                        st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota update error for {url}: {e}")
                    time.sleep(st.session_state.rate_limit_delay)
                else:
                    st.session_state.error_count += 1
                    error_container.error(f"Failed to scrape {url}: {response.get('error', 'Unknown error')}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Scrape error for {url}: {response.get('error', 'Unknown error')}")
            except Exception as e:
                st.session_state.error_count += 1
                error_container.error(f"Error scraping {url}: {e}")
                st.session_state.error_log.append(f"{datetime.datetime.now()}: Scrape error for {url}: {e}")
                time.sleep(st.session_state.rate_limit_delay)

        if st.session_state.pending_quota_updates:
            try:
                if FirebaseFunctions.retry_pending_quota_updates(license_key, st.session_state.pending_quota_updates):
                    st.session_state.daily_urls_used += len(st.session_state.pending_quota_updates)
                    st.session_state.pending_quota_updates = []
                    st.success("Successfully synced all pending quota updates.")
                else:
                    st.warning("Failed to sync some quota updates. They will be retried on next run.")
            except Exception as e:
                st.warning(f"Error syncing pending quota updates: {e}")
                st.session_state.error_log.append(f"{datetime.datetime.now()}: Error syncing pending quota updates: {e}")

        st.session_state.scraping_in_progress = False
        status_text.text(f"Scraping complete: {st.session_state.scraped_count} successful, {st.session_state.error_count} failed")
        progress_bar.empty()

    if st.session_state.all_data:
        st.subheader("Scraped Data")
        df = pd.DataFrame(st.session_state.all_data)
        st.dataframe(df)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download CSV",
            data=csv,
            file_name=f"walmart_scraped_data_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
        if st.button("Clear Data"):
            st.session_state.all_data = []
            st.session_state.scraped_data = []
            st.session_state.scraped_count = 0
            st.session_state.error_count = 0
            st.session_state.local_scraped_count = 0
            st.rerun()

    if st.session_state.error_log:
        with st.expander("Error Log", expanded=False):
            for log in st.session_state.error_log[-10:]:
                st.write(log)
