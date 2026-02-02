import streamlit as st
import pandas as pd
import requests
import math
import os
import time
from datetime import datetime

# Set page configuration
st.set_page_config(page_title="Positional Stock Option Scanner", layout="wide")

# Custom CSS for compact layout and button-like tabs
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem !important;
            padding-bottom: 1rem !important;
        }
        h1 {
            font-size: 1.8rem !important;
            margin-bottom: 0rem !important;
            white-space: nowrap !important;
        }
        h2 {
            font-size: 1.1rem !important;
            padding-top: 0.2rem !important;
            margin-bottom: 0.1rem !important;
        }
        h3 {
            font-size: 1.0rem !important;
            padding-top: 0.1rem !important;
            margin-bottom: 0.1rem !important;
        }
        
        /* Tab Styling */
        .stTabs [data-baseweb="tab-list"] {
            gap: 10px;
        }
        .stTabs [data-baseweb="tab"] {
            height: 45px;
            white-space: pre-wrap;
            background-color: #f0f2f6;
            border-radius: 5px;
            padding: 10px 20px;
            font-size: 1.1rem;
            font-weight: 600;
            border: 1px solid #d6d6d6;
        }
        .stTabs [aria-selected="true"] {
            background-color: #007bff;
            color: white !important;
            border-color: #007bff;
        }
        
        /* Prevent graying out during refresh */
        .stApp {
            transition: none !important;
        }
        [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
            opacity: 1 !important;
            transition: none !important;
        }
        
        /* Hide File Uploader Instructions */
        [data-testid="stFileUploaderDropzone"] div div span {
           display: none !important;
        }
        [data-testid="stFileUploaderDropzone"] div div small {
           display: none !important;
        }
    </style>
""", unsafe_allow_html=True)

import json

# Paths for persistent storage
DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

BLACKLIST_FILE = os.path.join(DATA_DIR, 'blacklist.json')
TOKEN_FILE = os.path.join(DATA_DIR, 'token.json')

FILES = {
    'Monthly': os.path.join(DATA_DIR, 'monthly.csv'),
    'Weekly': os.path.join(DATA_DIR, 'weekly.csv'),
    'Intraday': os.path.join(DATA_DIR, 'intraday.csv')
}

def load_token():
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                data = json.load(f)
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    return data.get('token', '')
        except:
            pass
    return ''

def save_token(token):
    try:
        data = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'token': token
        }
        with open(TOKEN_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass

def load_blacklist():
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, 'r') as f:
                data = json.load(f)
                if data.get('date') == datetime.now().strftime('%Y-%m-%d'):
                    return set(data.get('keys', []))
        except:
            pass
    return set()

def save_blacklist(keys):
    try:
        data = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'keys': list(keys)
        }
        with open(BLACKLIST_FILE, 'w') as f:
            json.dump(data, f)
    except:
        pass

# Constant for NSE JSON
NSE_JSON_PATH = 'NSE.json'

@st.cache_data
def load_nse_json():
    if os.path.exists(NSE_JSON_PATH):
        try:
            df = pd.read_json(NSE_JSON_PATH)
            # Pre-process JSON
            if 'segment' in df.columns:
                df = df[df['segment'] == 'NSE_FO']
            df['expiry_dt'] = pd.to_datetime(df['expiry'], unit='ms').dt.normalize()
            return df
        except Exception as e:
            st.error(f"Error loading NSE.json: {e}")
            return pd.DataFrame()
    else:
        st.error(f"NSE.json not found at {NSE_JSON_PATH}")
        return pd.DataFrame()

def process_bhavcopy(bhav_file, df_json):
    try:
        df_bhav = pd.read_csv(bhav_file)
        
        # Check required columns
        required_cols = ['FinInstrmTp', 'TckrSymb', 'XpryDt', 'ClsPric', 'StrkPric', 'OptnTp', 'HghPric', 'LwPric', 'LastPric']
        if not all(col in df_bhav.columns for col in required_cols):
            st.error(f"Uploaded file missing required columns: {required_cols}")
            return pd.DataFrame()

        # --- Process Bhavcopy Futures ---
        futures = df_bhav[df_bhav['FinInstrmTp'].isin(['STF', 'IDF'])].copy()
        if futures.empty:
            st.warning("No Futures data found in uploaded file.")
            return pd.DataFrame()

        futures['XpryDt'] = pd.to_datetime(futures['XpryDt'])
        futures = futures.sort_values('XpryDt')
        
        # Find nearest expiry per symbol
        near_futures = futures.groupby('TckrSymb').first().reset_index()
        near_futures = near_futures[['TckrSymb', 'ClsPric', 'XpryDt']]
        near_futures = near_futures.rename(columns={'ClsPric': 'FuturePrice', 'XpryDt': 'FutureExpiryDate'})

        # --- Process Bhavcopy Options ---
        options = df_bhav[df_bhav['OptnTp'].isin(['CE', 'PE'])].copy()
        if options.empty:
            st.warning("No Options data found in uploaded file.")
            return pd.DataFrame()

        options['XpryDt'] = pd.to_datetime(options['XpryDt'])

        # Merge Options with Near Futures
        merged = pd.merge(options, near_futures, on='TckrSymb')
        merged = merged[merged['XpryDt'] == merged['FutureExpiryDate']]
        
        # Calculate ATM
        merged['Diff'] = abs(merged['StrkPric'] - merged['FuturePrice'])
        
        # Find best strike per symbol (Minimize Diff, then tie-break with StrikePrice)
        # This ensures only ONE strike is selected per symbol, eliminating duplicates
        best_strikes = merged[['TckrSymb', 'StrkPric', 'Diff']].drop_duplicates()
        best_strikes = best_strikes.sort_values(by=['TckrSymb', 'Diff', 'StrkPric'])
        best_strikes = best_strikes.groupby('TckrSymb').first().reset_index()
        
        atm_options = pd.merge(merged, best_strikes[['TckrSymb', 'StrkPric']], on=['TckrSymb', 'StrkPric'])
        atm_rows = atm_options[['TckrSymb', 'XpryDt', 'StrkPric', 'OptnTp', 'FuturePrice', 'ClsPric', 'FinInstrmNm', 'HghPric', 'LwPric', 'LastPric']].copy()
        
        # Normalize dates for merging
        atm_rows['XpryDt'] = atm_rows['XpryDt'].dt.normalize()

        # Merge with Upstox JSON
        result = pd.merge(
            atm_rows,
            df_json,
            left_on=['TckrSymb', 'StrkPric', 'OptnTp', 'XpryDt'],
            right_on=['underlying_symbol', 'strike_price', 'instrument_type', 'expiry_dt'],
            how='inner'
        )

        final_df = result[[
            'TckrSymb', 'XpryDt', 'StrkPric', 'OptnTp', 
            'FuturePrice', 'ClsPric', 'instrument_key',
            'HghPric', 'LwPric', 'LastPric'
        ]]

        final_df = final_df.rename(columns={
            'TckrSymb': 'Symbol',
            'XpryDt': 'ExpiryDate',
            'StrkPric': 'StrikePrice',
            'OptnTp': 'OptionType',
            'ClsPric': 'Trigger',
            'HghPric': 'HighPrice',
            'LwPric': 'LowPrice',
            'LastPric': 'LastPrice'
        })
        
        # Calculate Camarilla R4
        # Formula: Close + (High - Low) * 1.1 / 2
        final_df['Camarilla_R4'] = final_df['Trigger'] + (final_df['HighPrice'] - final_df['LowPrice']) * 1.1 / 2

        # Multiply Trigger by 2 (User Rule)
        if 'Trigger' in final_df.columns:
            final_df['Trigger'] = final_df['Trigger'] * 2
            
        return final_df

    except Exception as e:
        st.error(f"Error processing file: {e}")
        return pd.DataFrame()

def fetch_ltp(instrument_keys, token):
    if not token:
        return {}
    
    url = "https://api.upstox.com/v3/market-quote/ltp"
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    batch_size = 50
    ltp_map = {}
    
    batches = [instrument_keys[i:i + batch_size] for i in range(0, len(instrument_keys), batch_size)]
    
    for batch in batches:
        params = {'instrument_key': ','.join(batch)}
        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success':
                    quotes = data.get('data', {})
                    for key, details in quotes.items():
                        inst_token = details.get('instrument_token')
                        last_price = details.get('last_price')
                        if inst_token:
                            ltp_map[inst_token] = last_price
        except Exception:
            pass
            
    return ltp_map

def display_option_chain(df, access_token, key_suffix):
    if df.empty:
        st.info("No data to display. Please upload a valid Bhavcopy in the sidebar.")
        return

    # Fetch LTP if token provided
    if access_token:
        all_keys = df['instrument_key'].dropna().unique().tolist()
        with st.spinner(f'Fetching LTP for {key_suffix}...'):
            ltp_data = fetch_ltp(all_keys, access_token)
        df['ltp'] = df['instrument_key'].map(ltp_data).fillna(0.0)
    else:
        df['ltp'] = 0.0
        st.warning("Enter Access Token in sidebar to see live LTP.")

    # If Intraday, replace Trigger with Camarilla_R4
    if key_suffix == 'Intraday' and 'Camarilla_R4' in df.columns:
        df['Trigger'] = df['Camarilla_R4']

    # Calculate Change %
    def calculate_numeric_change(row):
        try:
            ocp = row['Trigger']
            ltp = row['ltp']
            if ocp > 0 and ltp > 0:
                return (ltp / ocp * 100)
            return 0.0
        except:
            return 0.0

    df['change_val'] = df.apply(calculate_numeric_change, axis=1)
    df['change %'] = df['change_val']

    # --- Intraday Blacklist Logic ---
    if key_suffix == 'Intraday':
        # Load existing blacklist
        blacklist = load_blacklist()
        
        # Check time condition (before 09:30)
        current_time = datetime.now().time()
        cutoff_time = datetime.strptime("09:30", "%H:%M").time()
        
        if current_time < cutoff_time:
            # Identify new violators
            violators = df[df['change %'] >= 100]['instrument_key'].tolist()
            if violators:
                blacklist.update(violators)
                save_blacklist(blacklist)
        
        # Filter out blacklisted keys
        if blacklist:
            original_count = len(df)
            df = df[~df['instrument_key'].isin(blacklist)]
            filtered_count = len(df)
            diff = original_count - filtered_count
            # if diff > 0:
            #     st.caption(f"ℹ️ {diff} symbols hidden (Change % >= 100 before 09:30)")

    # Split Calls/Puts
    calls_df = df[df['OptionType'] == 'CE'].copy()
    puts_df = df[df['OptionType'] == 'PE'].copy()

    # Sort
    calls_df = calls_df.sort_values(by='change %', ascending=False)
    puts_df = puts_df.sort_values(by='change %', ascending=False)

    display_cols = ['Symbol', 'StrikePrice', 'Trigger', 'ltp', 'change %']
    
    # Styling
    def color_change(val):
        if isinstance(val, (int, float)):
            if val >= 100:
                return 'background-color: darkgreen; color: white'
            elif val >= 90:
                return 'background-color: lightgreen; color: black'
        return ''

    format_dict = {
        'change %': '{:.2f}%',
        'Trigger': '{:.2f}',
        'ltp': '{:.2f}',
        'StrikePrice': '{:.2f}'
    }

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Calls (CE)")
        st.dataframe(
            calls_df[display_cols].style
            .map(color_change, subset=['change %'])
            .format(format_dict),
            hide_index=True, 
            use_container_width=True,
            height=1800
        )

    with col2:
        st.subheader("Puts (PE)")
        st.dataframe(
            puts_df[display_cols].style
            .map(color_change, subset=['change %'])
            .format(format_dict),
            hide_index=True, 
            use_container_width=True,
            height=1800
        )

# --- Sidebar ---
with st.sidebar:
    st.header("Configuration")
    
    # Persistent Token Logic
    saved_token = load_token()
    access_token = st.text_input("Upstox Access Token", value=saved_token, type="password")
    
    if access_token and access_token != saved_token:
        save_token(access_token)
    
    st.markdown("---")
    st.header("Data Management")
    
    # NSE JSON Uploader
    st.subheader("NSE Instrument JSON")
    up_json = st.file_uploader("Upload NSE.json", type=['json'], key='json_up')
    if up_json is not None:
        with open(NSE_JSON_PATH, "wb") as f:
            f.write(up_json.getbuffer())
        st.cache_data.clear()
        st.success("NSE.json updated and cache cleared!")
    
    # Monthly Uploader
    st.subheader("Monthly")
    up_m = st.file_uploader("Upload Monthly Bhavcopy", type=['csv'], key='m_up')
    if up_m is not None:
        with open(FILES['Monthly'], "wb") as f:
            f.write(up_m.getbuffer())
        st.success("Monthly file updated!")
    
    # Weekly Uploader
    st.subheader("Weekly")
    up_w = st.file_uploader("Upload Weekly Bhavcopy", type=['csv'], key='w_up')
    if up_w is not None:
        with open(FILES['Weekly'], "wb") as f:
            f.write(up_w.getbuffer())
        st.success("Weekly file updated!")
    
    # Intraday Uploader
    st.subheader("Intraday")
    up_i = st.file_uploader("Upload Intraday Bhavcopy", type=['csv'], key='i_up')
    if up_i is not None:
        with open(FILES['Intraday'], "wb") as f:
            f.write(up_i.getbuffer())
        st.success("Intraday file updated!")
        
    st.markdown("---")
    st.header("Auto Refresh")
    auto_refresh = st.checkbox("Enable Auto-Refresh", value=False)
    refresh_interval = st.slider("Refresh Interval (seconds)", min_value=5, max_value=60, value=15)

# --- Main Page ---
st.title("Positional Stock Option Scanner")
st.caption(f"Last Updated: {datetime.now().strftime('%H:%M:%S')}")

nse_json_df = load_nse_json()

if not nse_json_df.empty:
    tab1, tab2, tab3 = st.tabs(["Monthly", "Weekly", "Intraday"])

    with tab1:
        st.header("Monthly Options")
        if os.path.exists(FILES['Monthly']):
            df_m = process_bhavcopy(FILES['Monthly'], nse_json_df)
            display_option_chain(df_m, access_token, "Monthly")
        else:
            st.info("Please upload a Monthly Bhavcopy in the sidebar to view data.")

    with tab2:
        st.header("Weekly Options")
        if os.path.exists(FILES['Weekly']):
            df_w = process_bhavcopy(FILES['Weekly'], nse_json_df)
            display_option_chain(df_w, access_token, "Weekly")
        else:
            st.info("Please upload a Weekly Bhavcopy in the sidebar to view data.")

    with tab3:
        st.header("Intraday Options")
        if os.path.exists(FILES['Intraday']):
            df_i = process_bhavcopy(FILES['Intraday'], nse_json_df)
            display_option_chain(df_i, access_token, "Intraday")
        else:
            st.info("Please upload an Intraday Bhavcopy in the sidebar to view data.")

else:
    st.error("Critical Error: NSE.json could not be loaded.")

# Auto-Refresh Logic
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
