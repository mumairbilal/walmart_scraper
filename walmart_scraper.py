import base64
import datetime
import os
import time
import uuid
import string
import random
import streamlit as st
import requests
import pandas as pd
from firecrawl import Firecrawl
import firebase_admin
from firebase_admin import credentials, firestore
import json
from streamlit_option_menu import option_menu
from concurrent.futures import ThreadPoolExecutor, as_completed
import psutil
import hashlib
import platform
import subprocess

PLAN_LIMITS = {
    "Free": {"daily_limit": 50, "valid_days": 7},
    "Basic": {"daily_limit": 500, "valid_days": 30},
    "Premium": {"daily_limit": 2500, "valid_days": 90},
    "Enterprise": {"daily_limit": 5000, "valid_days": 365}
}

LOCAL_LICENSE_FILE = ".walmart_scraper_license"
DEVICE_ID_FILE = ".device_fingerprint"

# -------------------------------
# Enhanced Device Identification Functions
# -------------------------------
def get_hardware_info():
    """Get comprehensive hardware information for device fingerprinting"""
    try:
        # Get system information
        system_info = {
            'platform': platform.platform(),
            'processor': platform.processor(),
            'architecture': platform.architecture()[0],
            'machine': platform.machine(),
            'node': platform.node()
        }
        
        # Get disk serial numbers (most reliable identifier)
        disk_serials = []
        try:
            for disk in psutil.disk_partitions():
                if 'fixed' in disk.opts:  # Only physical drives
                    try:
                        if platform.system() == "Windows":
                            # Windows: Get disk serial using wmic
                            result = subprocess.run(
                                ['wmic', 'diskdrive', 'get', 'serialnumber', '/format:value'],
                                capture_output=True, text=True, timeout=10
                            )
                            for line in result.stdout.split('\n'):
                                if 'SerialNumber=' in line and line.strip() != 'SerialNumber=':
                                    serial = line.split('=')[1].strip()
                                    if serial and serial not in disk_serials:
                                        disk_serials.append(serial)
                        else:
                            # Linux/Mac: Use lsblk or system_profiler
                            if platform.system() == "Linux":
                                result = subprocess.run(
                                    ['lsblk', '-d', '-n', '-o', 'SERIAL'],
                                    capture_output=True, text=True, timeout=10
                                )
                                for serial in result.stdout.split('\n'):
                                    serial = serial.strip()
                                    if serial and serial not in disk_serials:
                                        disk_serials.append(serial)
                            elif platform.system() == "Darwin":  # macOS
                                result = subprocess.run(
                                    ['system_profiler', 'SPSerialATADataType'],
                                    capture_output=True, text=True, timeout=10
                                )
                                # Parse macOS output for serial numbers
                                lines = result.stdout.split('\n')
                                for line in lines:
                                    if 'Serial Number:' in line:
                                        serial = line.split(':', 1)[1].strip()
                                        if serial and serial not in disk_serials:
                                            disk_serials.append(serial)
                    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
                        continue
        except Exception:
            pass
        
        # Get CPU information
        cpu_info = ""
        try:
            if platform.system() == "Windows":
                result = subprocess.run(
                    ['wmic', 'cpu', 'get', 'ProcessorId', '/format:value'],
                    capture_output=True, text=True, timeout=10
                )
                for line in result.stdout.split('\n'):
                    if 'ProcessorId=' in line:
                        cpu_info = line.split('=')[1].strip()
                        break
            else:
                # For Linux/Mac, use /proc/cpuinfo or system_profiler
                if os.path.exists('/proc/cpuinfo'):
                    with open('/proc/cpuinfo', 'r') as f:
                        for line in f:
                            if 'serial' in line.lower():
                                cpu_info = line.split(':', 1)[1].strip()
                                break
        except Exception:
            pass
        
        return {
            'system': system_info,
            'disk_serials': disk_serials,
            'cpu_info': cpu_info
        }
        
    except Exception as e:
        if 'error_log' in st.session_state:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error getting hardware info: {str(e)}")
        return None

def get_real_mac_addresses():
    """Get all real MAC addresses from network interfaces"""
    mac_addresses = []
    try:
        # Method 1: Using psutil (most reliable)
        for interface_name, interface_addresses in psutil.net_if_addrs().items():
            for address in interface_addresses:
                if address.family == psutil.AF_LINK:  # MAC address family
                    mac = address.address
                    # Filter out virtual/invalid MACs
                    if (mac and mac != '00:00:00:00:00:00' and 
                        not mac.startswith('00:15:5d') and  # Hyper-V
                        not mac.startswith('00:50:56') and  # VMware
                        not mac.startswith('08:00:27') and  # VirtualBox
                        not mac.startswith('00:0c:29') and  # VMware
                        not mac.startswith('00:1c:42')):    # Parallels
                        mac_addresses.append(mac.upper().replace('-', ':'))
        
        # Method 2: Fallback using uuid.getnode() with validation
        if not mac_addresses:
            node = uuid.getnode()
            if node != 0x0000000000000000:  # Valid node
                mac = ':'.join(('%012X' % node)[i:i+2] for i in range(0, 12, 2))
                mac_addresses.append(mac)
        
        # Method 3: System-specific commands as last resort
        if not mac_addresses:
            try:
                if platform.system() == "Windows":
                    result = subprocess.run(['getmac', '/fo', 'csv', '/nh'], 
                                          capture_output=True, text=True, timeout=10)
                    for line in result.stdout.split('\n'):
                        if line.strip():
                            mac = line.split(',')[0].strip('"')
                            if mac and mac != 'N/A':
                                mac_addresses.append(mac.upper().replace('-', ':'))
                elif platform.system() in ["Linux", "Darwin"]:
                    result = subprocess.run(['ifconfig'], capture_output=True, text=True, timeout=10)
                    import re
                    macs = re.findall(r'([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})', result.stdout)
                    for mac_match in macs:
                        mac = ''.join(mac_match).upper().replace('-', ':')
                        if mac not in mac_addresses:
                            mac_addresses.append(mac)
            except Exception:
                pass
        
        return list(set(mac_addresses))  # Remove duplicates
        
    except Exception as e:
        if 'error_log' in st.session_state:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error getting MAC addresses: {str(e)}")
        return []

def generate_device_fingerprint():
    """Generate a unique device fingerprint combining multiple hardware identifiers"""
    try:
        # Get hardware information
        hardware_info = get_hardware_info()
        mac_addresses = get_real_mac_addresses()
        
        # Create fingerprint components
        fingerprint_components = []
        
        # Add MAC addresses (primary identifier)
        if mac_addresses:
            # Sort to ensure consistent ordering
            fingerprint_components.extend(sorted(mac_addresses))
        
        # Add hardware info if available
        if hardware_info:
            if hardware_info['disk_serials']:
                fingerprint_components.extend(sorted(hardware_info['disk_serials']))
            
            if hardware_info['cpu_info']:
                fingerprint_components.append(hardware_info['cpu_info'])
            
            # Add system info
            system = hardware_info['system']
            fingerprint_components.extend([
                system.get('processor', ''),
                system.get('machine', ''),
                system.get('node', '')
            ])
        
        # Filter out empty components
        fingerprint_components = [comp for comp in fingerprint_components if comp]
        
        if not fingerprint_components:
            raise ValueError("No hardware identifiers found")
        
        # Create hash of all components
        fingerprint_string = '|'.join(fingerprint_components)
        device_hash = hashlib.sha256(fingerprint_string.encode()).hexdigest()
        
        # Return both the primary MAC and the device fingerprint
        primary_mac = mac_addresses[0] if mac_addresses else "UNKNOWN"
        
        return {
            'primary_mac': primary_mac,
            'device_fingerprint': device_hash,
            'all_macs': mac_addresses,
            'components_count': len(fingerprint_components)
        }
        
    except Exception as e:
        if 'error_log' in st.session_state:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error generating device fingerprint: {str(e)}")
        return None

def get_stable_device_id():
    """Get a stable device ID that persists across sessions"""
    try:
        # Check if device fingerprint file exists
        if os.path.exists(DEVICE_ID_FILE):
            try:
                with open(DEVICE_ID_FILE, "r") as f:
                    saved_data = f.read().strip()
                    if saved_data:
                        # Parse saved data
                        lines = saved_data.split('\n')
                        saved_fingerprint = {}
                        for line in lines:
                            if '=' in line:
                                key, value = line.split('=', 1)
                                saved_fingerprint[key] = value
                        
                        # Verify current hardware matches saved fingerprint
                        current_fingerprint = generate_device_fingerprint()
                        if current_fingerprint:
                            # Check if primary identifiers match
                            if (saved_fingerprint.get('primary_mac') == current_fingerprint['primary_mac'] or
                                saved_fingerprint.get('device_fingerprint') == current_fingerprint['device_fingerprint']):
                                return saved_fingerprint.get('primary_mac', saved_fingerprint.get('device_fingerprint'))
                            else:
                                st.warning("Hardware change detected. Device fingerprint will be updated.")
            except Exception as e:
                if 'error_log' in st.session_state:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Error reading saved device ID: {str(e)}")
        
        # Generate new fingerprint
        current_fingerprint = generate_device_fingerprint()
        if not current_fingerprint:
            raise ValueError("Failed to generate device fingerprint")
        
        # Save the new fingerprint
        try:
            fingerprint_data = f"""primary_mac={current_fingerprint['primary_mac']}
device_fingerprint={current_fingerprint['device_fingerprint']}
all_macs={','.join(current_fingerprint['all_macs'])}
components_count={current_fingerprint['components_count']}
created_at={datetime.datetime.now().isoformat()}
"""
            with open(DEVICE_ID_FILE, "w") as f:
                f.write(fingerprint_data)
        except Exception as e:
            st.warning(f"Failed to save device fingerprint: {e}")
        
        return current_fingerprint['primary_mac']
        
    except Exception as e:
        if 'error_log' in st.session_state:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Critical error in get_stable_device_id: {str(e)}")
        # Ultimate fallback: generate a temporary UUID
        fallback_id = str(uuid.uuid4())
        st.error(f"Using temporary device ID due to hardware detection failure: {e}")
        return fallback_id

def get_mac_address():
    """Get stable, hardware-based device identifier"""
    return get_stable_device_id()

# -------------------------------
# Firebase License Functions
# -------------------------------
class FirebaseFunctions:
    _firestore_db = None
    
    @staticmethod
    def initialize_firebase():
        """Initialize Firebase connection"""
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
    def get_all_client_data():
        """Get all client data from Firebase"""
        all_client_data = []
        clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
        docs = clients_ref.stream()
        
        for doc in docs:
            client_data = doc.to_dict()
            client_data["id"] = doc.id
            all_client_data.append(client_data)
        
        return all_client_data
    
    @staticmethod
    def is_mac_already_registered(mac_address):
        """Check if MAC address is already registered"""
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientMacAddress", "==", mac_address)
            docs = list(query.stream())
            
            return len(docs) > 0
            
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error checking MAC registration: {e}")
            return False
    
    @staticmethod
    def get_registration_by_mac(mac_address):
        """Get existing registration by MAC address"""
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientMacAddress", "==", mac_address)
            docs = list(query.stream())
            
            if len(docs) > 0:
                client_data = docs[0].to_dict()
                client_data["id"] = docs[0].id
                return client_data
            return None
            
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error getting registration by MAC: {e}")
            return None
    
    @staticmethod
    def is_client_eligible(client_data, expected_bot_name, expected_valid_date, mac_address):
        """Enhanced client eligibility check with strict MAC validation"""
        if client_data is None:
            return False
        
        # Check tool name
        if str(client_data.get("ToolName", "")) != str(expected_bot_name):
            return False
        
        # Check access status
        if str(client_data.get("AccessStatus", "")) != "ON":
            return False
        
        # Check validity date
        try:
            date_string = client_data.get("ValidUntil")
            if not date_string:
                return False
            
            date_string = str(date_string)
            date_formats = ["%d-%b-%y", "%Y-%m-%d", "%d-%m-%Y"]
            
            valid_date = None
            for date_format in date_formats:
                try:
                    valid_date = datetime.datetime.strptime(date_string, date_format)
                    break
                except ValueError:
                    continue
            
            if valid_date is None:
                return False
            
            if valid_date < expected_valid_date:
                return False
            
        except Exception as e:
            st.error(f"Date validation error: {e}")
            return False
        
        # STRICT MAC address validation
        registered_mac = client_data.get("ClientMacAddress", "")
        if not registered_mac:
            st.error("No MAC address found in license data.")
            return False
        
        if registered_mac != mac_address:
            st.error(f"Device mismatch detected!\nRegistered device: {registered_mac}\nCurrent device: {mac_address}\nThis license is tied to a different device.")
            return False
        
        return True
    
    @staticmethod
    def get_client_data_by_license_key(license_key):
        """Get client data by license key"""
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
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error fetching client data by license key: {e}")
            return None
    
    @staticmethod
    def get_client_data_by_email(email):
        """Get client data by email"""
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
            st.error(f"Error fetching client data by email: {e}")
            return None
    
    @staticmethod
    def get_client_data_by_mac(mac_address):
        """Get client data by MAC address"""
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            query = clients_ref.where("ClientMacAddress", "==", mac_address)
            docs = list(query.stream())
            
            if len(docs) > 0:
                client_data = docs[0].to_dict()
                client_data["id"] = docs[0].id
                return client_data
            return None
                
        except Exception as e:
            st.error(f"Error fetching client data by MAC: {e}")
            return None
    
    @staticmethod
    def add_new_client(client_data):
        """Add new client to Firebase with dynamic URL limit and validity based on plan"""
        try:
            if FirebaseFunctions._firestore_db is None:
                FirebaseFunctions.initialize_firebase()
            
            license_key = FirebaseFunctions.generate_license_key()
            client_data["LicenseKey"] = license_key
            client_data["RegistrationDate"] = datetime.datetime.now().strftime("%Y-%m-%d")
            client_data["LastValidated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            client_data["DailyUrlCount"] = 0
            
            # Dynamic limit and validity based on plan
            plan = client_data.get("Plan", "Free")
            plan_config = PLAN_LIMITS.get(plan, PLAN_LIMITS["Free"])
            client_data["DailyUrlLimit"] = plan_config["daily_limit"]
            client_data["ValidUntil"] = (datetime.datetime.now() + datetime.timedelta(days=plan_config["valid_days"])).strftime("%Y-%m-%d")
            
            mac_address = get_mac_address()
            client_data["ClientMacAddress"] = mac_address
            
            clients_ref = FirebaseFunctions._firestore_db.collection("licenses")
            new_doc_ref = clients_ref.document()
            new_doc_ref.set(client_data)
            
            return license_key, new_doc_ref.id
            
        except Exception as e:
            st.error(f"Error adding new client: {e}")
            return None, None
    
    @staticmethod
    def update_client_validation(license_key, mac_address, url_count):
        """Update last validated time, MAC address, and daily URL count in Firebase using a batch"""
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
                    "ClientMacAddress": mac_address,
                    "DailyUrlCount": firestore.Increment(url_count)
                })
                batch.commit()
                return True
            return False
                
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error updating client validation: {str(e)}")
            return False
    
    @staticmethod
    def retry_pending_quota_updates(license_key, mac_address, pending_updates):
        """Retry pending quota updates in a batch"""
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
                    "ClientMacAddress": mac_address,
                    "DailyUrlCount": firestore.Increment(len(pending_updates))
                })
                batch.commit()
                return True
            return False
                
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Error retrying pending quota updates: {str(e)}")
            return False
    
    @staticmethod
    def reset_daily_url_count(license_key):
        """Reset daily URL count at midnight"""
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
            st.error(f"Error resetting daily URL count: {e}")
            return False
    
    @staticmethod
    def generate_license_key(length=20):
        """Generate a random license key"""
        chars = string.ascii_uppercase + string.digits
        return ''.join(random.choice(chars) for _ in range(length))

# -------------------------------
# Helper Functions
# -------------------------------
def check_license_eligibility(license_key, bot_name, mac_address):
    """Check if license is eligible with security checks"""
    try:
        expected_valid_date = datetime.datetime.now()
        client_data = FirebaseFunctions.get_client_data_by_license_key(license_key)
        if not client_data:
            return False, None
        
        is_eligible = FirebaseFunctions.is_client_eligible(client_data, bot_name, expected_valid_date, mac_address)
        if not is_eligible:
            return False, client_data
        
        return True, client_data
        
    except Exception as e:
        st.error(f"License validation error: {e}")
        return False, None

def should_reset_daily_count(client_data):
    """Check if daily URL count should be reset"""
    last_validated = client_data.get("LastValidated", "")
    if not last_validated:
        return True
    try:
        last_validated_dt = datetime.datetime.strptime(last_validated, "%Y-%m-%d %H:%M:%S")
        return last_validated_dt.day != datetime.datetime.now().day
    except ValueError:
        return True

def validate_new_registration(email, mac_address):
    """Validate new registration attempt"""
    # Check if email already exists
    existing_email = FirebaseFunctions.get_client_data_by_email(email)
    if existing_email:
        return False, "An account with this email already exists. Please login with your existing license key."
    
    # Check if MAC address already registered
    existing_mac_registration = FirebaseFunctions.get_registration_by_mac(mac_address)
    if existing_mac_registration:
        existing_email = existing_mac_registration.get("ClientEmail", "Unknown")
        return False, f"This device is already registered with email: {existing_email}. Please login with your existing license key or contact support if this is an error."
    
    return True, "Registration allowed"

# -------------------------------
# Initialize Firebase
# -------------------------------
try:
    FirebaseFunctions.initialize_firebase()
except Exception as e:
    st.error(f"Failed to initialize Firebase: {e}")
    st.stop()

# -------------------------------
# Application State Management
# -------------------------------
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

# -------------------------------
# Enhanced Auto-Login with Device Validation
# -------------------------------
if st.session_state.app_state == "auth" and st.session_state.user_data is None:
    if os.path.exists(LOCAL_LICENSE_FILE):
        with open(LOCAL_LICENSE_FILE, "r") as f:
            saved_key = f.read().strip()
        if saved_key:
            with st.spinner("Auto-validating saved license..."):
                try:
                    mac = get_mac_address()
                    is_eligible, client_data = check_license_eligibility(saved_key, "walmart_scraper", mac)
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
                        st.warning("Saved license key is invalid or tied to a different device. Please re-enter your license key.")
                        # Remove invalid saved license
                        os.remove(LOCAL_LICENSE_FILE)
                except Exception as e:
                    st.warning(f"Auto-login failed: {e}")
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Auto-login error: {e}")

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
# Enhanced Authentication Interface
# -------------------------------
if st.session_state.app_state == "auth":
    st.title("Walmart Product Scraper")
    st.markdown("**Professional-grade data extraction for market research and competitive analysis.**")
    
    tab1, tab2 = st.tabs(["New Registration", "Existing User"])
    
    with tab1:
        st.subheader("Create Your Account")
        
        # Get device ID early for validation
        try:
            device_id = get_mac_address()
            
            # Check if this device is already registered
            existing_registration = FirebaseFunctions.get_registration_by_mac(device_id)
            if existing_registration:
                existing_email = existing_registration.get("ClientEmail", "Unknown")
                st.error(f"""
                Device Already Registered
                
                This device is already registered with email: **{existing_email}**
                
                Please use the "Existing User" tab to login with your license key.
                
                If you believe this is an error, contact support.
                """)
            else:
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
                        st.text_input("Device ID", value=device_id, disabled=True, 
                                     help="Unique identifier for this device")
                        registration_date = datetime.datetime.now().strftime("%Y-%m-%d")
                        st.text_input("Registration Date", value=registration_date, disabled=True)
                        
                        # Display device info for transparency
                        with st.expander("Device Information", expanded=False):
                            try:
                                fingerprint_data = generate_device_fingerprint()
                                if fingerprint_data:
                                    st.write(f"Primary MAC: {fingerprint_data['primary_mac']}")
                                    st.write(f"All MACs: {', '.join(fingerprint_data['all_macs'])}")
                                    st.write(f"Hardware Components: {fingerprint_data['components_count']}")
                            except Exception:
                                st.write("Device fingerprint not available")
                    
                    agree_terms = st.checkbox("I agree to the [Terms of Service](https://example.com/terms) and [Privacy Policy](https://example.com/privacy)")
                    dont_ask = st.checkbox("Don't ask again on this device", value=True)
                    
                    submitted = st.form_submit_button("Create Account")
                    
                    if submitted:
                        with st.spinner("Creating account and validating device..."):
                            # Validate required fields
                            if not all([full_name, email, firecrawl_api_key]):
                                st.error("Please fill in all required fields including Firecrawl API Key.")
                            elif not agree_terms:
                                st.error("You must agree to the Terms of Service.")
                            else:
                                # Enhanced validation
                                is_valid, message = validate_new_registration(email, device_id)
                                if not is_valid:
                                    st.error(f"{message}")
                                else:
                                    # Create account
                                    try:
                                        client_data = {
                                            "ClientName": full_name,
                                            "ClientEmail": email,
                                            "ClientMacAddress": device_id,
                                            "RegistrationDate": registration_date,
                                            "Plan": base_plan,
                                            "ToolName": "walmart_scraper",
                                            "AccessStatus": "ON",
                                            "DailyUrlCount": 0,
                                            "FirecrawlApiKey": firecrawl_api_key
                                        }
                                        
                                        license_key, doc_id = FirebaseFunctions.add_new_client(client_data)
                                        
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
                                            **Device:** {device_id}
                                            
                                            Important: Save your license key securely. This device is now permanently linked to your account.
                                            """)
                                            
                                            # Log successful registration
                                            st.session_state.error_log.append(f"{datetime.datetime.now()}: New account registered - Email: {email}, Device: {device_id}, Plan: {base_plan}")
                                            st.rerun()
                                        else:
                                            st.error("Failed to create account. Please try again or contact support.")
                                            
                                    except Exception as e:
                                        st.error(f"Account creation failed: {e}")
                                        st.session_state.error_log.append(f"{datetime.datetime.now()}: Account creation error: {e}")
                
        except Exception as e:
            st.error(f"Device validation failed: {e}")
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Device validation error: {e}")
    
    with tab2:
        st.subheader("Login to Your Account")
        
        # Get device ID for validation
        try:
            device_id = get_mac_address()
        except Exception as e:
            st.error(f"Device validation failed: {e}")
            st.stop()
        
        license_key = st.text_input("License Key", type="password", placeholder="Enter your license key")
        st.text_input("Device ID", value=device_id, disabled=True, 
                     help="This must match the device ID used during registration")
        dont_ask = st.checkbox("Don't ask again on this device")
        
        if st.button("Validate License"):
            if license_key:
                with st.spinner("Validating license and device..."):
                    is_eligible, client_data = check_license_eligibility(license_key, "walmart_scraper", device_id)
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
                        st.error("Invalid license key or device mismatch.")
                        # Show additional help for device mismatch
                        if client_data:
                            registered_mac = client_data.get("ClientMacAddress", "")
                            if registered_mac and registered_mac != device_id:
                                st.info(f"""
                                **Device Mismatch Information:**
                                - Registered Device: `{registered_mac}`
                                - Current Device: `{device_id}`
                                
                                This license is tied to a different device. Contact support if you've replaced your hardware.
                                """)
            else:
                st.error("Please enter your license key.")

    st.stop()

# -------------------------------
# Main Scraper Interface
# -------------------------------
if st.session_state.app_state == "scraping":
    # Sidebar
    st.sidebar.header("Scraper Settings")
    def sidebar_header(title, icon_path=None, subtitle=None, icon_width=24):
        html = '<div style="display: flex; align-items: center; margin-bottom: 10px;">'
        if icon_path and os.path.exists(icon_path):
            with open(icon_path, "rb") as f:
                data = f.read()
            encoded = base64.b64encode(data).decode()
            html += f"<img src='data:image/png;base64,{encoded}' width='{icon_width}' style='margin-right:8px;'>"
        html += f"<span style='font-size: 18px; font-weight: bold;'>{title}</span></div>"
        if subtitle:
            html += f"<div style='font-size: 12px; color: #666666; margin-bottom: 10px;'>{subtitle}</div>"
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
    if "selected_fields" not in st.session_state:
        st.session_state.selected_fields = fields.copy()
    with st.sidebar.expander("Select Data Fields", expanded=True):
        for dc in fields:
            checked = st.checkbox(dc, value=dc in st.session_state.selected_fields, key=f"field_{dc}")
            if checked and dc not in st.session_state.selected_fields:
                st.session_state.selected_fields.append(dc)
            elif not checked and dc in st.session_state.selected_fields:
                st.session_state.selected_fields.remove(dc)
        st.info("Unselect heavy fields (Images/Videos) for faster scraping.")

    # Sidebar Bottom: Account Info in Expander and Logout
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
        if os.path.exists(DEVICE_ID_FILE):
            os.remove(DEVICE_ID_FILE)
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

    # Page Config
    image_path = "D:/icon2.png"
    try:
        st.set_page_config(page_title="Walmart Product Scraper", layout="wide", initial_sidebar_state="expanded")
    except:
        pass
        
    if os.path.exists(image_path):
        st.image(image_path, width=300)
    st.title("Walmart Product Scraper")
    st.markdown("**Extract structured product data with ease. Perfect for market research and catalog building.**")

    if not st.session_state.firecrawl_api_key:
        st.error("Firecrawl API key is required. Please enter it in the sidebar.")
        st.stop()

    # Initialize Firecrawl
    try:
        firecrawl = Firecrawl(api_key=st.session_state.firecrawl_api_key)
    except Exception as e:
        st.error(f"Error initializing scraper: {e}. Please check your Firecrawl API key.")
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
        mac_address = get_mac_address()
        avg_time_per_url = 0

    if st.session_state.scraping_in_progress:
        i = st.session_state.current_scraping_index
        if i < st.session_state.total_urls:
            if st.session_state.daily_urls_used >= max_urls:
                st.error("Daily URL limit reached. Cannot scrape more.")
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
                        st.warning(f"Skipped URL {urls[i]}: No data returned.")
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Skipped URL {urls[i]}: Error - {str(e)}")
                    st.warning(f"Skipped URL {urls[i]}: Error - {str(e)}.")

                if row is not None:
                    st.session_state.all_data.append(row)
                    st.session_state.scraped_count += 1
                    st.session_state.local_scraped_count += 1
                    license_key = st.session_state.user_data.get("LicenseKey", "")
                    try:
                        updated = FirebaseFunctions.update_client_validation(license_key, mac_address, 1)
                        if updated:
                            st.session_state.daily_urls_used += 1
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota updated successfully for URL {urls[i]}")
                        else:
                            st.session_state.pending_quota_updates.append(urls[i])
                            st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota update failed for URL {urls[i]}: Update returned False")
                            st.warning(f"Failed to update quota for URL {urls[i]}. Added to pending updates.")
                    except Exception as e:
                        st.session_state.pending_quota_updates.append(urls[i])
                        st.session_state.error_log.append(f"{datetime.datetime.now()}: Quota update failed for URL {urls[i]}: {str(e)}")
                        st.warning(f"Failed to update quota for URL {urls[i]}: {str(e)}. Added to pending updates.")
                else:
                    st.session_state.error_count += 1
                
                time.sleep(st.session_state.rate_limit_delay)
            
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
            
            progress = (i + 1) / st.session_state.total_urls
            st.session_state.progress_bar.progress(progress)
            percentage = int(progress * 100)
            
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
                    if st.session_state.pending_quota_updates:
                        with st.spinner("Applying pending quota updates..."):
                            success = FirebaseFunctions.retry_pending_quota_updates(
                                st.session_state.user_data.get("LicenseKey", ""),
                                get_mac_address(),
                                st.session_state.pending_quota_updates
                            )
                            if success:
                                st.session_state.daily_urls_used += len(st.session_state.pending_quota_updates)
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Successfully applied {len(st.session_state.pending_quota_updates)} pending quota updates")
                                st.session_state.pending_quota_updates = []
                            else:
                                st.session_state.error_log.append(f"{datetime.datetime.now()}: Failed to apply {len(st.session_state.pending_quota_updates)} pending quota updates")
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
                    st.success(f"Scraping completed! Success: {st.session_state.scraped_count}, Errors: {st.session_state.error_count}, URLs Used: {st.session_state.daily_urls_used}, Local Scraped: {st.session_state.local_scraped_count}")
                    if st.session_state.error_log:
                        with st.expander("Debug: Error Log", expanded=False):
                            st.write(st.session_state.error_log)
                except Exception as e:
                    st.session_state.error_log.append(f"{datetime.datetime.now()}: Finalization error: {str(e)}")
            else:
                st.warning("No data extracted. Check URLs.")
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
            st.subheader("Data Preview")
            st.dataframe(df.head(display_limit), use_container_width=True, height=400)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.download_button(
                    "Download Sample",
                    df.head(50).to_csv(index=False).encode("utf-8"),
                    "sample_walmart_products.csv",
                    "text/csv",
                    help="Downloads up to 50 rows"
                )
            with col2:
                if st.session_state.user_tier == "premium":
                    st.download_button(
                        "Download Full Data",
                        df.to_csv(index=False).encode("utf-8"),
                        "walmart_products.csv",
                        "text/csv"
                    )
                else:
                    st.button("Download Full Data (Premium Only)",
                             help="Upgrade to premium for full datasets")
            with col3:
                if st.button("Clear Data"):
                    st.session_state.scraped_data = []
                    st.success("Data cleared!")
                    st.rerun()
        except Exception as e:
            st.session_state.error_log.append(f"{datetime.datetime.now()}: Data display error: {str(e)}")

    # Tutorial and Footer
    st.markdown("---")
    st.subheader("Tutorial Video")
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
