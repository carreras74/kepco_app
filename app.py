import streamlit as st
import pandas as pd
import requests
import folium
from streamlit_folium import st_folium
import json
import os
import time
import re
import gspread
import base64
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

st.set_page_config(page_title="전국 송전여유용량 대시보드", page_icon="⚡", layout="wide")

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
IS_ADMIN = (admin_pw_input == st.secrets.get("ADMIN_PASSWORD", "9119"))

if IS_ADMIN:
    st.sidebar.success("✅ 관리자 인증 완료! 숨겨진 4번 금고가 개방되었습니다.")

def trigger_search():
    st.session_state['do_kepco_search'] = True

# ==========================================
# 💡 구글 시트 2 (비밀 금고) 연동 모듈
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
        if not vals or not vals[0] or str(vals[0][0]).strip() == "":
            sheet.clear()
            sheet.append_row(["full_address", "metroCd", "cityCd", "lidong", "li", "jibun", "저장시간"])
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
# 📊 기획 1번: 전체 랭킹 연동 (시트 1)
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
# 🎯 기획 2번: 한전 실시간 코어 기능
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

def smart_fetch_kepco(metroCd, cityCd, lidong, li, jibun):
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
# 🗺️ 3부: 카카오 주소 자동 번역기
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

# 💡 [핵심 패치] 확장자 대/소문자, png/jpg 상관없이 무조건 샅샅이 뒤져서 찾아냅니다!
def get_image_base64():
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # header_bg 라는 이름을 가진 모든 확장자 탐색
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
# 🖥️ 앱 화면 구성 시작
# ==========================================

# 알아서 찾으므로 파일명을 따로 지정할 필요가 없습니다.
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

# ---------------------------------------------------------
# 📊 1. 구글 시트 기반 시도별 여유용량 랭킹 
# ---------------------------------------------------------
st.header("📊 1. 전체 지역 랭킹 조회 (매일 자동 업데이트 엑셀 연동)")
df_gs = load_gsheets_data()

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
else: st.warning("데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러를 실행해 주세요.")

st.markdown("---")

# ---------------------------------------------------------
# 🎯 2. 한전 API 실시간 스마트 다이렉트 조회
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 1건 조회 (한전 여유용량 확인)")
st.markdown("신주소(도로명)를 넣으셔도 **구주소(지번)로 자동 변환**하여 주변 10개 번지까지 지능적으로 탐색합니다. (조회 시 아래 3번 지도가 자동 완성됩니다)")

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

        raw_data, msg = smart_fetch_kepco(target_metroCd, target_cityCd, in_lidong.strip(), in_li.strip(), in_jibun.strip())
        
        if msg:
            if "대신" in msg: st.info(msg)
            else: st.success(msg)

        if raw_data:
            rt_df = display_kepco_dataframe(raw_data)
            
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

st.markdown("---")

# ==========================================
# 🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱
# ==========================================
st.header("🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱")
st.markdown("위 2번 항목에서 **검색 버튼을 누르는 순간 아래 지도에 자동으로 지적도가 완성**됩니다. 원하시면 지번을 직접 타이핑해서 검색하실 수도 있습니다.")

default_map_addr = st.session_state.get('auto_map_address', "")
search_addr_map = st.text_input("🔍 지도 주소 직접 검색 (2번에서 검색 시 자동 완성됨)", value=default_map_addr, placeholder="예: 김천시 아포읍 대성리 348")

if search_addr_map:
    with st.spinner("해당 지번의 위성지도와 지적도를 불러옵니다..."):
        lat, lon = get_kakao_coord(search_addr_map)
        
        if lat is not None:
            has_jibun = bool(re.search(r'\d', search_addr_map))
            start_zoom = 19 if has_jibun else 15
            m = folium.Map(location=[lat, lon], zoom_start=start_zoom, max_zoom=22, tiles=None)

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

            folium.Marker([lat, lon], popup=f"<b>{search_addr_map}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)
            folium.LayerControl(position='topright').add_to(m)
            
            st_folium(m, width=1300, height=750, returned_objects=[], key=f"wms_map_{lat}_{lon}")
        else:
            st.error("⚠️ 주소를 변환할 수 없어 지도를 띄우지 못했습니다.")

# ==========================================
# 📚 4. 나의 관심 주소 보관함 (관리자 전용 구글 시트 연동)
# ==========================================
if IS_ADMIN:
    st.markdown("---")
    st.header("⭐ 4. 알짜배기 관심 주소 자동 수집 보관함 (관리자 전용 구글 시트)")
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
                            st.success(f"✅ 위 3번 지도의 검색창에 **'{bm.get('full_address', '')}'**를 입력하시면 즉시 지적도를 확인하실 수 있습니다!")
                        else:
                            st.warning("⚠️ 해당 지역의 한전 데이터가 없습니다.")
            st.markdown("---")
