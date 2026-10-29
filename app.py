import streamlit as st
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
import json
import os
import time
import re
import math
import gspread
import base64
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="전국 송전여유용량 및 태양광 수지분석 APP", page_icon="⚡", layout="wide")

# ==========================================
# 💡 스크롤바 영구 고정 패치
# ==========================================
st.markdown("""
    <style>
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"], * {
        scrollbar-width: auto !important;
        scrollbar-color: #FF0000 #f1f1f1 !important;
    }
    *::-webkit-scrollbar {
        width: 26px !important;  
        height: 26px !important;
        background-color: #f1f1f1 !important;
        -webkit-appearance: none !important; 
        display: block !important; 
        opacity: 1 !important;
        visibility: visible !important;
    }
    *::-webkit-scrollbar-track {
        background-color: #f1f1f1 !important; 
        border-left: 1px solid #dcdcdc !important; 
        display: block !important; 
        opacity: 1 !important;
        visibility: visible !important;
    }
    *::-webkit-scrollbar-thumb {
        background-color: #FF0000 !important; 
        border-radius: 12px !important; 
        border: 4px solid #f1f1f1 !important; 
        display: block !important; 
        opacity: 1 !important;
        visibility: visible !important;
    }
    *::-webkit-scrollbar-thumb:hover, *::-webkit-scrollbar-thumb:active {
        background-color: #CC0000 !important; 
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 💡 API Keys & Settings
# ==========================================
VWORLD_KEY = "013A53E5-52AB-4FD2-AC9D-B7D4A85D5667"
KEPCO_KEY = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
KAKAO_KEY = "b5908f52b46962e646bf12c6f70a364d" 
DOMAIN = "https://kepcoapp-biwrxqmgtjrbamcm48ikmr.streamlit.app"
GOOGLE_SHEET_URL = "https://docs.google.com/spreadsheets/d/1QsHxBwA40ElWl9AAMf1HrKXjXRI_QyhHdgTOqWnZQQk/edit"

# ==========================================
# 🔐 사이드바 설정 (디버그 & 관리자 로그인)
# ==========================================
st.sidebar.title("🛠️ 시스템 도구")
DEBUG_MODE = st.sidebar.checkbox("🔧 통신 디버그 모드 켜기", value=False)

st.sidebar.markdown("---")
st.sidebar.subheader("🔐 관리자 모드")
admin_pw_input = st.sidebar.text_input("비밀번호를 입력하세요", type="password")
IS_ADMIN = (admin_pw_input == st.secrets.get("ADMIN_PASSWORD", "1234"))

if IS_ADMIN:
    st.sidebar.success("✅ 관리자 인증 완료! 숨겨진 5번 금고가 개방되었습니다.")

def trigger_search():
    st.session_state['do_kepco_search'] = True

# ==========================================
# 💡 구글 시트 2 이름표(Header) 자동 복구 모듈
# ==========================================
def get_bookmark_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        if "GCP_CREDENTIALS" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(st.secrets["GCP_CREDENTIALS"]), scope)
        elif os.path.exists("google_key.json"):
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_key.json", scope)
        else: return None

        client = gspread.authorize(creds)
        doc = client.open_by_url(GOOGLE_SHEET_URL)
        
        try:
            sheet = doc.worksheet("시트2")
        except:
            sheet = doc.get_worksheet(1)
            
        vals = sheet.get_all_values()
        expected_headers = ["full_address", "metroCd", "cityCd", "lidong", "li", "jibun", "저장시간"]
        
        if not vals:
            sheet.append_row(expected_headers)
        elif "full_address" not in vals[0]:
            sheet.insert_row(expected_headers, 1)
            
        return sheet
    except:
        return None

def load_bookmarks():
    sheet = get_bookmark_sheet()
    if sheet:
        vals = sheet.get_all_values()
        if len(vals) > 1:
            headers = vals[0]
            bms = []
            for row in vals[1:]:
                bm = {}
                for i, h in enumerate(headers):
                    bm[h.strip()] = row[i] if i < len(row) else ""
                bms.append(bm)
            return bms
    return []

def save_bookmark(bm_data):
    sheet = get_bookmark_sheet()
    if sheet:
        bms = load_bookmarks()
        if not any(str(b.get('full_address', '')) == str(bm_data['full_address']) for b in bms):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [
                bm_data['full_address'], str(bm_data['metroCd']), str(bm_data['cityCd']), 
                bm_data['lidong'], bm_data['li'], bm_data['jibun'], now
            ]
            sheet.append_row(row)

def delete_bookmark(idx):
    sheet = get_bookmark_sheet()
    if sheet:
        sheet.delete_rows(idx + 2)

# ==========================================
# 📊 기획 1번: 전체 랭킹 연동 (시트 1) 데이터 로드
# ==========================================
@st.cache_data(ttl=600)
def load_gsheets_data():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        if "GCP_CREDENTIALS" in st.secrets:
            creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(st.secrets["GCP_CREDENTIALS"]), scope)
        elif os.path.exists("google_key.json"):
            creds = ServiceAccountCredentials.from_json_keyfile_name("google_key.json", scope)
        else: return pd.DataFrame()

        client = gspread.authorize(creds)
        sheet = client.open_by_url(GOOGLE_SHEET_URL).get_worksheet(0)
        df = pd.DataFrame(sheet.get_all_records())

        for col in ["DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변압기 누적 연계용량"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
        return df
    except: return pd.DataFrame()

# ==========================================
# 💡 2번 검색 시 1번 랭킹 자동 동기화 엔진
# ==========================================
def sync_ranking_dropdown(rt_sido, rt_sigg, df_gs):
    if df_gs.empty: return
    
    SIDO_NORMALIZE = {
        "서울": "서울", "서울특별시": "서울", "부산": "부산", "부산광역시": "부산",
        "대구": "대구", "대구광역시": "대구", "인천": "인천", "인천광역시": "인천",
        "광주": "광주", "광주광역시": "광주", "대전": "대전", "대전광역시": "대전",
        "울산": "울산", "울산광역시": "울산", "세종": "세종", "세종특별자치시": "세종",
        "경기": "경기", "경기도": "경기", "강원": "강원", "강원특별자치도": "강원", "강원도": "강원",
        "충북": "충북", "충청북도": "충북", "충남": "충남", "충청남도": "충남",
        "전북": "전북", "전라북도": "전북", "전북특별자치도": "전북",
        "전남": "전남", "전라남도": "전남", "경북": "경북", "경상북도": "경북",
        "경남": "경남", "경상남도": "경남", "제주": "제주", "제주특별자치도": "제주"
    }
    
    norm_rt = SIDO_NORMALIZE.get(rt_sido, rt_sido)
    matched_sido = None
    
    for s in df_gs['시/도'].unique():
        if SIDO_NORMALIZE.get(s, s) == norm_rt:
            matched_sido = s
            break
            
    if matched_sido:
        st.session_state['gs_sido'] = matched_sido 
        
        clean_rt_sigg = rt_sigg.replace(" ", "")
        matched_sigg = "전체"
        for g in df_gs[df_gs['시/도'] == matched_sido]['시/구/군'].unique():
            clean_g = str(g).replace(" ", "")
            if clean_rt_sigg in clean_g or clean_g in clean_rt_sigg:
                matched_sigg = g
                break
        st.session_state['gs_sigg'] = matched_sigg 

# ==========================================
# 🎯 한전 API 실시간 코어 기능
# ==========================================
@st.cache_data(ttl=86400)
def get_region_codes():
    url = "https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern=*00000"
    try:
        reg_data = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json().get("regcodes", [])
        regions = []
        for r in reg_data:
            code = r["code"]
            name_parts = r["name"].split()
            if code[2:5] != "000" and len(name_parts) >= 2:
                regions.append({"metroCd": code[:2], "cityCd": code[2:5], "시도": name_parts[0], "시군구": " ".join(name_parts[1:])})
        return pd.DataFrame(regions)
    except: return pd.DataFrame()

def fetch_kepco_realtime(metroCd, cityCd, lidong="", li="", jibun=""):
    url = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"
    params = {"apiKey": KEPCO_KEY, "returnType": "json", "metroCd": metroCd, "cityCd": cityCd}
    if lidong: params["addrLidong"] = lidong
    if li: params["addrLi"] = li
    if jibun: params["addrJibun"] = jibun

    for attempt in range(2):
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data: return data
        except: pass
        time.sleep(0.5)
    return []

def match_kepco_region_code(sido, sigg, df_regions):
    if df_regions is None or df_regions.empty or '시도' not in df_regions.columns:
        return None, None
        
    SIDO_MAP = {
        "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
        "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종특별자치시": "세종특별자치시", "세종": "세종특별자치시",
        "경기": "경기도", "강원": "강원특별자치도", "강원특별자치도": "강원특별자치도",
        "충북": "충청북도", "충남": "충청남도", "전북": "전라북도", "전북특별자치도": "전라북도", "전라북도":"전라북도",
        "전남": "전라남도", "경북": "경상북도", "경남": "경상남도", "제주": "제주특별자치도", "제주특별자치도": "제주특별자치도"
    }
    mapped_sido = SIDO_MAP.get(sido, sido)
    clean_sigg = sigg.replace(" ", "")
    
    sido_df = df_regions[df_regions['시도'] == mapped_sido]
    if sido_df.empty:
        sido_df = df_regions[df_regions['시도'].str.contains(mapped_sido[:2])]
        
    for idx, row in sido_df.iterrows():
        if row['시군구'].replace(" ", "") == clean_sigg:
            return row['metroCd'], row['cityCd']
            
    for idx, row in sido_df.iterrows():
        if clean_sigg in row['시군구'].replace(" ", "") or row['시군구'].replace(" ", "") in clean_sigg:
            return row['metroCd'], row['cityCd']
            
    return None, None

def display_kepco_dataframe(raw_data):
    rt_df = pd.DataFrame(raw_data)
    rt_df = rt_df.rename(columns={"dlNm": "DL명(읍면동)", "vol3": "DL 여유용량", "vol2": "변압기 여유용량", "vol1": "변전소 여유용량", "substNm": "변전소명"})
    for col in ["DL 여유용량", "변압기 여유용량", "변전소 여유용량"]:
        if col in rt_df.columns: rt_df[col] = pd.to_numeric(rt_df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
    rt_df['연계가능여부'] = (rt_df['변압기 여유용량'] > 0) & (rt_df['변전소 여유용량'] > 0)
    rt_df['연계상태'] = rt_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    rt_df = rt_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False]).drop_duplicates(subset=['DL명(읍면동)']).reset_index(drop=True)
    rt_df["순위"] = range(1, len(rt_df) + 1)
    st.dataframe(rt_df[["순위", "연계상태", "DL명(읍면동)", "DL 여유용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]], use_container_width=True, hide_index=True)
    return rt_df

def smart_fetch_kepco(metroCd, cityCd, lidong="", li="", jibun=""):
    raw_data = fetch_kepco_realtime(metroCd, cityCd, lidong, li, jibun)
    msg = None
    
    if not raw_data and jibun:
        is_san = "산" in jibun
        clean_jibun = jibun.replace("산", "").strip()
        if "-" in clean_jibun:
            base_num_str = clean_jibun.split("-")[0]
        else:
            base_num_str = clean_jibun
            
        if base_num_str.isdigit():
            base_num = int(base_num_str)
            offsets = [0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8, -9, 9, -10, 10]
            
            for offset in offsets:
                if offset == 0 and "-" not in clean_jibun:
                    continue
                nearby_j = str(base_num + offset)
                if is_san: nearby_j = "산" + nearby_j
                
                raw_data = fetch_kepco_realtime(metroCd, cityCd, lidong, li, nearby_j)
                if raw_data:
                    if offset == 0:
                        msg = f"💡 한전 DB에 '{jibun}'가 누락되어, 부번을 뗀 대표 지번 **[{nearby_j}번지]** 기준으로 찾았습니다!"
                    else:
                        msg = f"💡 한전 DB에 해당 번지가 누락되어, 인접 지번 **[{nearby_j}번지]** 기준으로 연결 가능한 선로를 추적했습니다!"
                    return raw_data, msg
                    
    if not raw_data:
        raw_data = fetch_kepco_realtime(metroCd, cityCd, lidong, li, "")
        if raw_data and jibun:
            msg = f"💡 반경 10개 지번까지 탐색했으나 미등록된 지역입니다. 대신 **'{lidong} {li}' 전체 데이터**를 불러왔습니다."
            
    return raw_data, msg

# ==========================================
# 🗺️ 카카오 주소 및 변전소 위치 자동 번역기 
# ==========================================
def get_jibun_from_kakao(address):
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    url_addr = "https://dapi.kakao.com/v2/local/search/address.json"
    try:
        res = requests.get(url_addr, headers=headers, params={"query": address}, timeout=5).json()
        if res.get('documents'):
            addr_obj = res['documents'][0].get('address')
            if addr_obj:
                sido = addr_obj.get('region_1depth_name', '')
                sigg = addr_obj.get('region_2depth_name', '')
                region_3 = addr_obj.get('region_3depth_name', '')
                parts = region_3.split()
                lidong = parts[0] if len(parts) > 0 else ''
                li = parts[1] if len(parts) > 1 else ''
                
                main_no = addr_obj.get('main_address_no', '')
                sub_no = addr_obj.get('sub_address_no', '')
                jibun = main_no
                if sub_no: jibun += f"-{sub_no}"
                if addr_obj.get('mountain_yn') == 'Y': jibun = "산" + jibun
                
                full_jibun = f"{sido} {sigg} {lidong} {li} {jibun}".strip()
                full_jibun = re.sub(r'\s+', ' ', full_jibun)
                
                b_code = addr_obj.get('b_code', '')
                m_cd = b_code[:2] if len(b_code) >= 2 else ""
                c_cd = b_code[2:5] if len(b_code) >= 5 else ""
                
                return full_jibun, sido, sigg, lidong, li, jibun, m_cd, c_cd
    except: pass
    
    url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
    try:
        res2 = requests.get(url_kw, headers=headers, params={"query": address}, timeout=5).json()
        if res2.get('documents'):
            return res2['documents'][0]['address_name'], "", "", "", "", "", "", ""
    except: pass
    
    return None, None, None, None, None, None, None, None

def get_kakao_coord(address):
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    url_addr = "https://dapi.kakao.com/v2/local/search/address.json"
    try:
        res = requests.get(url_addr, headers=headers, params={"query": address}, timeout=5).json()
        if res.get('documents'): return float(res['documents'][0]['y']), float(res['documents'][0]['x'])
    except: pass
    url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
    try:
        res2 = requests.get(url_kw, headers=headers, params={"query": address}, timeout=5).json()
        if res2.get('documents'): return float(res2['documents'][0]['y']), float(res2['documents'][0]['x'])
    except: pass
    return None, None

def get_all_nearby_facilities(lat, lon, radius=20000):
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
    facilities = {}
    
    for kw in ["변전소", "전력소", "변환소"]:
        try:
            res = requests.get(url_kw, headers=headers, params={"query": kw, "y": lat, "x": lon, "radius": radius}, timeout=5).json()
            if res.get('documents'):
                for doc in res['documents']:
                    facilities[doc['place_name']] = (float(doc['y']), float(doc['x']))
        except: pass
    return facilities

def get_substation_coord(subst_name, region_hint):
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
    clean_name = re.sub(r'(S/S|s/s|변전소|전력소)', '', str(subst_name)).strip()
    queries = [f"{region_hint} {clean_name}변전소", f"{clean_name}변전소"]
    for q in queries:
        try:
            res = requests.get(url_kw, headers=headers, params={"query": q}, timeout=5).json()
            if res.get('documents'):
                return float(res['documents'][0]['y']), float(res['documents'][0]['x'])
        except: pass
    return None, None

def get_image_base64():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        for ext in ['png', 'jpg', 'jpeg', 'PNG', 'JPG', 'JPEG']:
            file_path = os.path.join(current_dir, f"header_bg.{ext}")
            if os.path.exists(file_path):
                with open(file_path, "rb") as img_file:
                    mime_type = "image/jpeg" if ext.lower() in ["jpg", "jpeg"] else "image/png"
                    b64_string = base64.b64encode(img_file.read()).decode('utf-8')
                    return f"data:{mime_type};base64,{b64_string}"
        return ""
    except: return ""

# ==========================================
# ☀️ 4부: 730W 용량 및 PVGIS 계절별 일조량·수익금 계산 함수 (에러 원천 방어 패치)
# ==========================================
def calculate_max_capacity(area_m2, usable_pct, lat_deg, tilt_deg, mod_w, mod_len, mod_wid):
    winter_noon_altitude = 90.0 - lat_deg - 23.45
    effective_sun_angle = max(15.0, winter_noon_altitude * 0.72)
    
    tilt_rad = math.radians(tilt_deg)
    sun_rad = math.radians(effective_sun_angle)
    
    pitch_ratio = math.cos(tilt_rad) + (math.sin(tilt_rad) / math.tan(sun_rad))
    
    single_module_area = mod_len * mod_wid
    ground_footprint_per_module = (mod_len * pitch_ratio) * (mod_wid + 0.02)
    
    effective_land_m2 = area_m2 * (usable_pct / 100.0)
    
    max_modules = int(effective_land_m2 // ground_footprint_per_module)
    max_capacity_kw = round((max_modules * mod_w) / 1000.0, 2)
    
    py_per_100kw = (area_m2 / 3.305785) / (max_capacity_kw / 100.0) if max_capacity_kw > 0 else 0
    
    return {
        "max_modules": max_modules,
        "max_capacity_kw": max_capacity_kw,
        "pitch_ratio": round(pitch_ratio, 2),
        "ground_footprint_per_module": round(ground_footprint_per_module, 2),
        "single_module_area": round(single_module_area, 2),
        "effective_land_m2": round(effective_land_m2, 1),
        "py_per_100kw": round(py_per_100kw, 1)
    }

@st.cache_data(show_spinner=False)
def get_seasonal_solar_generation(lat, lon, peakpower_kw, angle, loss):
    url = "https://re.jrc.ec.europa.eu/api/v5_2/PVcalc"
    params = {
        "lat": lat,
        "lon": lon,
        "peakpower": peakpower_kw,
        "loss": loss,
        "mountingplace": "free",
        "angle": angle,
        "aspect": 0,
        "outputformat": "json"
    }
    
    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    
    monthly_list = data["outputs"]["monthly"]["fixed"]
    df = pd.DataFrame(monthly_list)
    
    df["일평균_발전시간_hr"] = df["E_d"] / peakpower_kw
    
    season_map = {
        3: "🌸 봄 (3~5월)", 4: "🌸 봄 (3~5월)", 5: "🌸 봄 (3~5월)",
        6: "☀️ 여름 (6~8월)", 7: "☀️ 여름 (6~8월)", 8: "☀️ 여름 (6~8월)",
        9: "🍁 가을 (9~11월)", 10: "🍁 가을 (9~11월)", 11: "🍁 가을 (9~11월)",
        12: "❄️ 겨울 (12~2월)", 1: "❄️ 겨울 (12~2월)", 2: "❄️ 겨울 (12~2월)"
    }
    df["계절"] = df["month"].map(season_map)
    
    season_order = ["🌸 봄 (3~5월)", "☀️ 여름 (6~8월)", "🍁 가을 (9~11월)", "❄️ 겨울 (12~2월)"]
    df["계절"] = pd.Categorical(df["계절"], categories=season_order, ordered=True)
    
    seasonal_df = df.groupby("계절", observed=True).agg(
        하루_경사면_일사량=("H(i)_d", "mean"),
        하루_순수_발전시간=("일평균_발전시간_hr", "mean"),
        하루_평균_발전량_kWh=("E_d", "mean"),
        계절_총_발전량_MWh=("E_m", lambda x: x.sum() / 1000.0)
    ).reset_index()
    
    annual_total_mwh = df["E_m"].sum() / 1000.0
    annual_avg_hr = (df["E_m"].sum() / 365.0) / peakpower_kw
    
    return seasonal_df.round(2), df.round(2), round(annual_total_mwh, 2), round(annual_avg_hr, 2)

# ==========================================
# 🖥️ 앱 화면 구성 시작
# ==========================================
img_base64 = get_image_base64()

if img_base64:
    bg_style = f"background: linear-gradient(rgba(0, 0, 0, 0.1), rgba(0, 0, 0, 0.3)), url('{img_base64}') center bottom/cover no-repeat;"
else:
    bg_style = "background: linear-gradient(rgba(0, 0, 0, 0.1), rgba(0, 0, 0, 0.3)); background-color: #2c3e50;"

st.markdown(f"""
    <div style="
        {bg_style}
        padding: 130px 20px;
        border-radius: 15px;
        margin-bottom: 30px;
        text-align: center;
        box-shadow: 0 6px 10px rgba(0,0,0,0.4);
    ">
        <h1 style="color: white; font-weight: 900; margin: 0; text-shadow: 2px 2px 8px rgba(0,0,0,1.0); font-size: 2.8rem;">
            ⚡ 전국 송전여유용량 종합 분석 APP
        </h1>
    </div>
""", unsafe_allow_html=True)

df_gs = load_gsheets_data()

sec1_container = st.container()
st.markdown("---")
sec2_container = st.container()
st.markdown("---")
sec3_container = st.container()
st.markdown("---")
sec4_solar_container = st.container()
sec5_admin_container = st.container()

# ---------------------------------------------------------
# 🎯 2. 한전 API 실시간 스마트 다이렉트 조회
# ---------------------------------------------------------
with sec2_container:
    st.header("🎯 2. 상세 주소 실시간 1건 조회 (한전 여유용량 확인)")
    st.markdown("신주소(도로명)를 넣으셔도 **구주소(지번)로 자동 변환**하여 주변 10개 번지까지 지능적으로 탐색합니다. (조회 시 1번 랭킹, 3번 지도, 4번 일조량 계산기가 자동 연동됩니다)")

    df_regions = get_region_codes()
    if df_regions.empty:
        st.info("💡 지역 코드 서버 지연 중입니다. '통합 주소 검색'을 이용하시면 정상 작동합니다.")

    search_mode = st.radio("검색 방식 선택", ["📝 통합 주소 검색 (신주소 자동변환 추천)", "🛠️ 직접 선택 (수동 구주소)"], horizontal=True)

    integrated_addr = ""
    rt_sido, rt_sigg, in_lidong, in_li, in_jibun = "", "", "", "", ""

    if "통합" in search_mode:
        integrated_addr = st.text_input("🔍 조회할 주소를 입력하고 '엔터'를 누르세요", placeholder="예: 김천시 아포대로 123 또는 아포읍 대성리 348", on_change=trigger_search)
    else:
        if not df_regions.empty:
            rt_col1, rt_col2 = st.columns(2)
            with rt_col1: rt_sido = st.selectbox("📍 시/도", sorted(df_regions['시도'].unique().tolist()), key="rt_sido_bot")
            with rt_col2: rt_sigg = st.selectbox("🏢 시/군/구", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg_bot")

            rt_col3, rt_col4, rt_col5 = st.columns(3)
            with rt_col3: in_lidong = st.text_input("읍/면/동 (필수)", placeholder="예: 아포읍", on_change=trigger_search)
            with rt_col4: in_li = st.text_input("리 (선택)", placeholder="예: 대성리", on_change=trigger_search)
            with rt_col5: in_jibun = st.text_input("상세번지 (선택)", placeholder="예: 348 또는 산12", on_change=trigger_search)
        else:
            st.error("수동 선택 기능을 일시적으로 사용할 수 없습니다. 위의 '통합 주소 검색'을 이용해주세요.")

    search_btn_clicked = st.button("🚀 한전 실시간 여유용량 조회하기", use_container_width=True)

    if search_btn_clicked or st.session_state.get('do_kepco_search', False):
        st.session_state['do_kepco_search'] = False 
        
        with st.spinner("주소를 분석하고 한전 서버를 탐색 중입니다..."):
            auto_address = ""
            target_metroCd, target_cityCd = None, None
            
            if "통합" in search_mode:
                if not integrated_addr:
                    st.warning("주소를 입력해주세요.")
                    st.stop()
                    
                full_jibun, p_sido, p_sigg, p_lidong, p_li, p_jibun, target_metroCd, target_cityCd = get_jibun_from_kakao(integrated_addr)
                
                if not p_sido or not target_metroCd:
                    st.error("⚠️ 입력하신 주소의 구주소(지번) 변환에 실패했습니다. 올바른 주소인지 확인해주세요.")
                    st.stop()
                    
                st.success(f"🔄 **구주소 자동 번역 완료:** {full_jibun}")
                rt_sido, rt_sigg, in_lidong, in_li, in_jibun = p_sido, p_sigg, p_lidong, p_li, p_jibun
                auto_address = full_jibun 
            else:
                if not in_lidong:
                    st.warning("읍/면/동을 입력해주세요.")
                    st.stop()
                address_parts = [rt_sido, rt_sigg, in_lidong.strip(), in_li.strip(), in_jibun.strip()]
                auto_address = " ".join([p for p in address_parts if p])
                
                target_metroCd, target_cityCd = match_kepco_region_code(rt_sido, rt_sigg, df_regions)
                if not target_metroCd:
                    st.error(f"⚠️ 지역 코드 서버가 지연 중입니다. 위의 '통합 주소 검색'을 이용해 주세요.")
                    st.stop()

            st.session_state['auto_map_address'] = auto_address
            
            st.session_state['solar_addr_memo'] = auto_address
            s_lat, s_lon = get_kakao_coord(auto_address)
            if s_lat is not None and s_lon is not None:
                st.session_state['solar_lat'] = float(s_lat)
                st.session_state['solar_lon'] = float(s_lon)

            sync_ranking_dropdown(rt_sido, rt_sigg, df_gs)

            raw_data, msg = smart_fetch_kepco(target_metroCd, target_cityCd, in_lidong.strip(), in_li.strip(), in_jibun.strip())
            
            if msg:
                if "대신" in msg: st.info(msg)
                else: st.success(msg)

            if raw_data:
                rt_df = display_kepco_dataframe(raw_data)
                
                if '변전소명' in rt_df.columns:
                    unique_substations = rt_df['변전소명'].dropna().unique().tolist()
                    st.session_state['found_substations'] = unique_substations
                
                if rt_df['연계가능여부'].any():
                    auto_save_data = {
                        "full_address": auto_address,
                        "metroCd": target_metroCd,
                        "cityCd": target_cityCd,
                        "lidong": in_lidong.strip(),
                        "li": in_li.strip(),
                        "jibun": in_jibun.strip()
                    }
                    save_bookmark(auto_save_data)
            else:
                st.warning("⚠️ 한전 실시간 API에 해당 지역의 데이터가 아예 존재하지 않습니다.")
                st.session_state['found_substations'] = []

# ---------------------------------------------------------
# 📊 1. 구글 시트 기반 시도별 여유용량 랭킹
# ---------------------------------------------------------
with sec1_container:
    st.header("📊 1. 전체 지역 랭킹 조회 (매일 자동 업데이트 엑셀 연동)")
    
    if not df_gs.empty:
        gs_col1, gs_col2 = st.columns(2)
        with gs_col1: gs_sido = st.selectbox("📍 랭킹 조회 시/도", sorted(df_gs['시/도'].unique().tolist()), key="gs_sido")
        with gs_col2:
            gs_filtered = df_gs[df_gs['시/도'] == gs_sido]
            gs_sigg = st.selectbox("🏢 랭킹 조회 시/구/군", ["전체"] + sorted(gs_filtered['시/구/군'].unique().tolist()), key="gs_sigg")

        gs_target_df = gs_filtered.copy() if gs_sigg == "전체" else gs_filtered[gs_filtered['시/구/군'] == gs_sigg].copy()
        gs_target_df['연계가능여부'] = (gs_target_df['변압기 여유용량'] > 0) & (gs_target_df['변전소 여유용량'] > 0)
        gs_target_df['연계상태'] = gs_target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')

        gs_target_df = gs_target_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False])
        gs_target_df['고유DL명'] = gs_target_df['시/구/군'] + " " + gs_target_df['DL명(읍면동)']
        gs_target_df = gs_target_df.drop_duplicates(subset=['고유DL명'], keep='first').reset_index(drop=True)
        gs_target_df["순위"] = range(1, len(gs_target_df) + 1)

        gs_cols = ["순위", "연계상태", "시/구/군", "DL명(읍면동)", "DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
        st.dataframe(gs_target_df[[c for c in gs_cols if c in gs_target_df.columns]], use_container_width=True, hide_index=True)
    else: 
        st.warning("데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러를 실행해 주세요.")

# ---------------------------------------------------------
# 🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱
# ---------------------------------------------------------
with sec3_container:
    st.header("🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱")
    st.markdown("위 2번 항목에서 **검색 버튼을 누르는 순간 아래 지도에 자동으로 지적도가 완성**되며, 반경 **20km 이내의 모든 변전소/전력소(파란색 ⚡)가 함께 표시**됩니다.")

    default_map_addr = st.session_state.get('auto_map_address', "")
    search_addr_map = st.text_input("🔍 지도 주소 직접 검색 (2번에서 검색 시 자동 완성됨)", value=default_map_addr, placeholder="예: 김천시 아포읍 대성리 348")

    if search_addr_map:
        with st.spinner("해당 지번의 위성지도와 주변 전력 인프라 위치를 추적 중입니다..."):
            lat_map, lon_map = get_kakao_coord(search_addr_map)
            
            if lat_map is not None:
                st.session_state['solar_addr_memo'] = search_addr_map
                st.session_state['solar_lat'] = float(lat_map)
                st.session_state['solar_lon'] = float(lon_map)

                has_jibun = bool(re.search(r'\d', search_addr_map))
                start_zoom = 13
                m = folium.Map(location=[lat_map, lon_map], zoom_start=start_zoom, max_zoom=22, tiles=None)

                folium.TileLayer(
                    tiles=f'https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg',
                    attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22
                ).add_to(m)

                folium.TileLayer(
                    tiles=f'https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Hybrid/{{z}}/{{y}}/{{x}}.png',
                    attr='VWorld Hybrid', name='🏷️ 글자 오버레이', overlay=True, control=True, show=True, max_zoom=22
                ).add_to(m)

                if has_jibun:
                    folium.WmsTileLayer(
                        url=f"https://api.vworld.kr/req/wms?key={VWORLD_KEY}&domain={DOMAIN}",
                        layers="lp_pa_cbnd_bubun",
                        fmt="image/png",
                        transparent=True,
                        version="1.3.0",
                        name="📐 주변 전체 지적도 선 & 지번",
                        overlay=True,
                        control=True,
                        show=True
                    ).add_to(m)
                else:
                    layer_name = "lt_c_adri_info" if "리" in search_addr_map.split()[-1] or "리 " in search_addr_map else "lt_c_ademd_info"
                    folium.WmsTileLayer(
                        url=f"https://api.vworld.kr/req/wms?key={VWORLD_KEY}&domain={DOMAIN}",
                        layers=layer_name,
                        fmt="image/png",
                        transparent=True,
                        version="1.3.0",
                        name="🎯 행정구역 경계선",
                        overlay=True,
                        control=True,
                        show=True
                    ).add_to(m)

                folium.Marker([lat_map, lon_map], popup=f"<b>{search_addr_map}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)
                
                nearby_facilities = get_all_nearby_facilities(lat_map, lon_map, radius=20000)
                for f_name, (f_lat, f_lon) in nearby_facilities.items():
                    folium.Marker(
                        [f_lat, f_lon], 
                        popup=f"<b>⚡ {f_name}</b>", 
                        tooltip=f"{f_name}",
                        icon=folium.Icon(color='blue', icon='bolt', prefix='fa')
                    ).add_to(m)
                
                found_substations = st.session_state.get('found_substations', [])
                region_hint = " ".join(search_addr_map.split()[:2]) if search_addr_map else ""
                
                for sub_name in found_substations:
                    if str(sub_name).strip() and str(sub_name).strip() != "nan":
                        clean_sub = re.sub(r'(S/S|s/s|변전소|전력소)', '', str(sub_name)).strip()
                        if not any(clean_sub in k for k in nearby_facilities.keys()):
                            sub_lat, sub_lon = get_substation_coord(str(sub_name), region_hint)
                            if sub_lat is not None:
                                folium.Marker(
                                    [sub_lat, sub_lon], 
                                    popup=f"<b>⚡ {sub_name} 변전소 (관할)</b>", 
                                    tooltip=f"{sub_name} (한전 지정)",
                                    icon=folium.Icon(color='purple', icon='bolt', prefix='fa')
                                ).add_to(m)

                folium.LayerControl(position='topright').add_to(m)
                st_folium(m, width=1300, height=750, returned_objects=[], key=f"wms_map_{lat_map}_{lon_map}")
            else:
                st.error("⚠️ 주소를 변환할 수 없어 지도를 띄우지 못했습니다.")

# ---------------------------------------------------------
# ☀️ 4. 태양광 730W 용량 및 계절별 일조량·월별 예상 수익금 통합 분석기
# ---------------------------------------------------------
with sec4_solar_container:
    st.header("☀️ 4. 토지 평수 기반 730W 최대 설치용량 및 계절별 일조량·월별 수익금 분석기")
    st.markdown("위 2번·3번에서 검색한 지번의 **위치(위·경도)**가 자동 연동되며, **토지 면적(평)**과 **설치 용량·모듈 수량**을 자유롭게 커스텀하여 발전량과 **예상 월수익금**을 산출할 수 있습니다.")

    if 'solar_addr_memo' not in st.session_state:
        st.session_state['solar_addr_memo'] = "경북 영주시 안정면 일원"
    if 'solar_lat' not in st.session_state:
        st.session_state['solar_lat'] = 36.8057
    if 'solar_lon' not in st.session_state:
        st.session_state['solar_lon'] = 128.6240

    st.subheader("1️⃣ 토지 정보 및 730W 어레이 설계 조건 입력")

    scol1, scol2, scol3, scol4 = st.columns(4)

    with scol1:
        address_memo = st.text_input("검토 대상 지번 (검색 시 자동 연동)", key="solar_addr_memo")
        input_unit = st.radio("면적 입력 단위", ["평 (py)", "제곱미터 (㎡)"], horizontal=True)
        if input_unit == "평 (py)":
            area_py = st.number_input("토지 면적 (평)", value=1200.0, min_value=10.0, step=50.0)
            area_m2 = area_py * 3.305785
            st.caption(f"환산 면적: **{area_m2:,.1f} ㎡**")
        else:
            area_m2 = st.number_input("토지 면적 (㎡)", value=3967.0, min_value=33.0, step=100.0)
            area_py = area_m2 / 3.305785
            st.caption(f"환산 면적: **{area_py:,.1f} 평**")

    with scol2:
        lat_solar = st.number_input("위도 (Latitude - 자동연동)", format="%.4f", key="solar_lat")
        lon_solar = st.number_input("경도 (Longitude - 자동연동)", format="%.4f", key="solar_lon")
        usable_ratio = st.slider(
            "토지 유효 활용률 (%)",
            min_value=40, max_value=80, value=68, step=1,
            help="펜스 이격, 배수로, 작업 통로, 수배전반 자리 및 토지 모양(부정형 여부)을 감안한 실제 어레이 배치 가능 비율입니다."
        )

    with scol3:
        module_watt = st.number_input("모듈 단일 출력 (W)", value=730, step=5, help="최신 N-Type 대면적 양면모듈 730W 기본 세팅")
        module_length = st.number_input("모듈 세로 길이 (m)", value=2.384, format="%.3f")
        module_width = st.number_input("모듈 가로 폭 (m)", value=1.303, format="%.3f")

    with scol4:
        tilt_angle = st.number_input("모듈 설치 경사각 (도)", value=25, min_value=10, max_value=40)
        loss_rate = st.number_input("시스템 종합 손실률 (%)", value=12.0, min_value=5.0, max_value=25.0, step=0.5)
        unit_price_krw = st.number_input("예상 전력 판매 단가 (SMP+REC, 원/kWh)", value=160, step=5, help="평균적인 SMP와 REC 현물/고정가격 계약 합산 단가 (기본 160원 설정)")

    auto_cap_result = calculate_max_capacity(
        area_m2, usable_ratio, lat_solar, tilt_angle, module_watt, module_length, module_width
    )

    st.divider()
    st.subheader("2️⃣ 설계 용량 및 모듈 수량 커스텀 조정 (수정 가능)")
    st.markdown("자동 산출된 최대 기준값을 참고하되, **설비 용량(kW)**이나 **설치 모듈 매수**를 직접 변경하여 시뮬레이션해 보세요.")

    ccol1, ccol2 = st.columns(2)
    with ccol1:
        custom_capacity_kw = st.number_input(
            "설비 목표 용량 (kW) - 직접 수정 가능",
            value=float(auto_cap_result['max_capacity_kw']),
            min_value=1.0,
            step=10.0,
            format="%.2f",
            help="자동 산출된 용량보다 작거나 크게 자유롭게 입력하여 수지 분석을 다르게 시뮬레이션할 수 있습니다."
        )
    with ccol2:
        derived_modules = int((custom_capacity_kw * 1000.0) / module_watt)
        st.metric(
            label="🟦 적용되는 모듈 설치 매수",
            value=f"{derived_modules:,} 장",
            delta=f"선택 용량 {custom_capacity_kw:,.2f} kW 기준"
        )

    st.divider()
    st.subheader(f"3️⃣ 최종 적용 용량({custom_capacity_kw:,.2f} kW) 기준 계절별 일조량 및 월별 예상 수익금")

    if custom_capacity_kw > 0:
        with st.spinner("해당 좌표의 위성 일사량 및 산악 지형 음영 데이터를 계산 중입니다..."):
            try:
                seasonal_df, monthly_df, annual_mwh, annual_hr = get_seasonal_solar_generation(
                    lat_solar, lon_solar, custom_capacity_kw, tilt_angle, loss_rate
                )
                
                monthly_df["월별_예상수익금_원"] = monthly_df["E_m"] * unit_price_krw
                annual_total_revenue = monthly_df["월별_예상수익금_원"].sum()
                monthly_avg_revenue = annual_total_revenue / 12.0

                k1, k2, k3 = st.columns(3)
                k1.success(f"⏱️ **연평균 하루 순수 발전시간:** **{annual_hr:.2f} 시간/일**")
                k2.success(f"🔋 **연간 총 예상 발전량:** **{annual_mwh:,.1f} MWh/년**")
                k3.success(f"💰 **월 평균 예상 수익금:** **{monthly_avg_revenue:,.0f} 원/월** (연 약 {annual_total_revenue:,.0f}원)")
                
                # 💡 안전한 계절별 수익금 매핑 및 병합 처리
                season_map_func = lambda m: {
                    3: "🌸 봄 (3~5월)", 4: "🌸 봄 (3~5월)", 5: "🌸 봄 (3~5월)",
                    6: "☀️ 여름 (6~8월)", 7: "☀️ 여름 (6~8월)", 8: "☀️ 여름 (6~8월)",
                    9: "🍁 가을 (9~11월)", 10: "🍁 가을 (9~11월)", 11: "🍁 가을 (9~11월)",
                    12: "❄️ 겨울 (12~2월)", 1: "❄️ 겨울 (12~2월)", 2: "❄️ 겨울 (12~2월)"
                }.get(m, "기타")

                monthly_df["계절"] = monthly_df["month"].map(season_map_func)
                seasonal_revenue_mwh = monthly_df.groupby("계절", observed=True)["월별_예상수익금_원"].sum().reset_index()
                
                if "계절" not in seasonal_df.columns and "계절구분명" in seasonal_df.columns:
                    seasonal_df = seasonal_df.rename(columns={"계절구분명": "계절"})

                seasonal_merged = pd.merge(seasonal_df, seasonal_revenue_mwh, on="계절", how="left")
                
                st.dataframe(
                    seasonal_merged.rename(columns={
                        "계절": "계절 구분",
                        "하루_경사면_일사량": "하루 평균 일조/일사량 (kWh/m²/일)",
                        "하루_순수_발전시간": "하루 실제 발전시간 (시간/일)",
                        "하루_평균_발전량_kWh": "하루 예상 발전량 (kWh/일)",
                        "계절_총_발전량_MWh": "계절 누적 발전량 (MWh)",
                        "월별_예상수익금_원": "계절 누적 예상 수익금 (원)"
                    }),
                    use_container_width=True,
                    hide_index=True
                )
                
                with st.expander("📅 1월~12월 월별 상세 발전량 및 평균 수익금 표 열기"):
                    st.dataframe(
                        monthly_df[["month", "H(i)_d", "일평균_발전시간_hr", "E_d", "E_m", "월별_예상수익금_원"]].rename(columns={
                            "month": "월 (Month)",
                            "H(i)_d": "일평균 경사면 일사량 (kWh/m²)",
                            "일평균_발전시간_hr": "일평균 발전시간 (hr)",
                            "E_d": "하루 발전량 (kWh)",
                            "E_m": "월간 총 발전량 (kWh)",
                            "월별_예상수익금_원": "월별 예상 수익금 (원)"
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
            except Exception as e:
                st.error(f"일사량 API 통신 중 오류가 발생했습니다: {e}")

# ---------------------------------------------------------
# 📚 5. 나의 관심 주소 보관함 (관리자 전용 구글 시트 연동)
# ---------------------------------------------------------
with sec5_admin_container:
    if IS_ADMIN:
        st.markdown("---")
        st.header("⭐ 5. 알짜배기 관심 주소 자동 수집 보관함 (관리자 전용 구글 시트)")
        st.markdown("일반 사용자들이 검색한 주소 중, **'🟢 가능' 상태가 뜨는 곳만 은밀하게 구글 시트('시트2')에 자동 저장**되어 영구 보존됩니다.")

        bookmarks = load_bookmarks()

        if not bookmarks:
            st.info("💡 아직 수집된 주소가 없습니다. 일반 사용자가 '가능' 지역을 검색하면 이곳에 나타납니다.")
        else:
            for idx, bm in enumerate(bookmarks):
                with st.container():
                    col1, col2, col3 = st.columns([6, 3, 1])
                    with col1:
                        st.markdown(f"#### 📍 {bm.get('full_address', '주소 정보 없음')}")
                    with col2:
                        check_btn = st.button("🚀 여유용량 실시간 다시조회", key=f"re_chk_{idx}", use_container_width=True)
                    with col3:
                        del_btn = st.button("❌ 삭제", key=f"del_bm_{idx}", use_container_width=True)
                    
                    if del_btn:
                        delete_bookmark(idx)
                        st.rerun()
                        
                    if check_btn:
                        with st.spinner("저장된 주소의 데이터를 한전 서버에서 다시 가져옵니다..."):
                            m_cd = str(bm.get('metroCd', '00')).zfill(2)
                            c_cd = str(bm.get('cityCd', '000')).zfill(3)
                            lidong = bm.get('lidong', '')
                            li = bm.get('li', '')
                            jibun = str(bm.get('jibun', ''))
                            
                            raw_data, msg = smart_fetch_kepco(m_cd, c_cd, lidong, li, jibun)
                            if msg:
                                if "대신" in msg: st.info(msg)
                                else: st.success(msg)
                                
                            if raw_data:
                                display_kepco_dataframe(raw_data)
                                st.success(f"✅ 위 3번 지도의 검색창에 **'{bm.get('full_address', '')}'**를 입력하시면 즉시 지적도와 일조량·수익금을 확인하실 수 있습니다!")
                            else:
                                st.warning("⚠️ 해당 지역의 한전 데이터가 없습니다.")
                st.markdown("---")
