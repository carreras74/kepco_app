import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import os, json, requests, re

st.set_page_config(page_title="전국 송전여유용량 실시간 파악 APP", page_icon="⚡", layout="wide")

# ==========================================
# 💡 공통 캐시 및 헬퍼 함수
# ==========================================
@st.cache_data(ttl=86400)
def get_location_data(address, fallback_address=""):
    geolocator = Nominatim(user_agent="kepco_app_hwang")
    try:
        loc = geolocator.geocode(address, geometry='geojson')
        if loc: return loc.latitude, loc.longitude, loc.raw.get('geojson')
        if fallback_address:
            loc = geolocator.geocode(fallback_address, geometry='geojson')
            if loc: return loc.latitude, loc.longitude, loc.raw.get('geojson')
        return 36.5, 127.5, None
    except: return 36.5, 127.5, None

@st.cache_data(ttl=86400 * 7)
def get_korea_geojson():
    try: return requests.get("https://raw.githubusercontent.com/vuski/admdongkor/master/ver20230701/HangJeongDong_ver20230701.geojson", timeout=20).json()
    except: return None

def get_dong_polygon(sido, sigg, dl_name, geojson_data):
    if not geojson_data: return None
    clean_name = re.sub(r'[0-9]+$', '', re.sub(r'\(.*?\)', '', dl_name)).strip()
    search_names = [clean_name, f"{clean_name}동", f"{clean_name}읍", f"{clean_name}면", f"{clean_name}리"]
    matched = []
    for f in geojson_data.get('features', []):
        adm = f.get('properties', {}).get('adm_nm', '') or ''
        if sido[:2] in adm and sigg.replace(" ", "") in adm.replace(" ", ""):
            if any(v in adm for v in search_names):
                matched.append(f)
                break
    return {"type": "FeatureCollection", "features": matched} if matched else None

# ==========================================
# 💡 Part 1: 구글 시트 데이터 로드
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
        
        df = pd.DataFrame(gspread.authorize(creds).open_by_url("https://docs.google.com/spreadsheets/d/1QsHxBwA40ElWl9AAMf1HrKXjXRI_QyhHdgTOqWnZQQk/edit?gid=2075043511#gid=2075043511").get_worksheet(0).get_all_records())
        
        for col in ["DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변압기 누적 연계용량"]:
            if col in df.columns: df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
        return df
    except: return pd.DataFrame()

# ==========================================
# 💡 Part 2: 실시간 API 호출 데이터 로드
# ==========================================
@st.cache_data(ttl=86400)
def get_region_codes():
    url = "https://grpc-proxy-server-mkvo6j4wsq-du.a.run.app/v1/regcodes?regcode_pattern=*00000"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        reg_data = requests.get(url, headers=headers, timeout=10).json().get("regcodes", [])
        regions = []
        for r in reg_data:
            code = r["code"]
            name_parts = r["name"].split()
            if code[2:5] != "000" and len(name_parts) >= 2:
                regions.append({
                    "metroCd": code[:2],
                    "cityCd": code[2:5],
                    "시도": name_parts[0],
                    "시군구": " ".join(name_parts[1:])
                })
        return pd.DataFrame(regions)
    except: return pd.DataFrame()

def fetch_kepco_realtime(metroCd, cityCd, lidong="", li="", jibun=""):
    api_key = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
    url = "https://bigdata.kepco.co.kr/openapi/v1/dispersedGeneration.do"
    params = {"apiKey": api_key, "returnType": "json", "metroCd": metroCd, "cityCd": cityCd}
    if lidong: params["addrLidong"] = lidong
    if li: params["addrLi"] = li
    if jibun: params["addrJibun"] = jibun
    
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            return res.json().get("data", [])
    except: pass
    return []

# ==========================================
# 🖥️ 앱 화면 구성 시작
# ==========================================
st.title("⚡ 전국 송전여유용량 종합 분석 APP")

# ---------------------------------------------------------
# 1부: 구글 시트 기반 전체 랭킹 (기존 로직)
# ---------------------------------------------------------
st.header("📊 1. 구글시트 기반 시/군/구 전체 랭킹 조회")
df_gs = load_gsheets_data()

if not df_gs.empty:
    gs_col1, gs_col2 = st.columns(2)
    with gs_col1: gs_sido = st.selectbox("📍 시/도를 선택하세요 (구글시트)", sorted(df_gs['시/도'].unique().tolist()), key="gs_sido")
    with gs_col2:
        gs_filtered = df_gs[df_gs['시/도'] == gs_sido]
        gs_sigg = st.selectbox("🏢 시/구/군을 선택하세요 (전체 보기 가능)", ["전체"] + sorted(gs_filtered['시/구/군'].unique().tolist()), key="gs_sigg")
        
    gs_target_df = gs_filtered.copy() if gs_sigg == "전체" else gs_filtered[gs_filtered['시/구/군'] == gs_sigg].copy()
    gs_target_df['연계가능여부'] = (gs_target_df['변압기 여유용량'] > 0) & (gs_target_df['변전소 여유용량'] > 0)
    gs_target_df['연계상태'] = gs_target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    gs_target_df = gs_target_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False])
    gs_target_df['고유DL명'] = gs_target_df['시/구/군'] + " " + gs_target_df['DL명(읍면동)']
    gs_target_df = gs_target_df.drop_duplicates(subset=['고유DL명'], keep='first').reset_index(drop=True)
    gs_target_df["순위"] = range(1, len(gs_target_df) + 1)
    
    gs_cols = ["순위", "연계상태", "시/구/군", "DL명(읍면동)", "DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
    st.dataframe(gs_target_df[[c for c in gs_cols if c in gs_target_df.columns]], use_container_width=True, hide_index=True)
    
    dong_list = [d for d in gs_target_df['고유DL명'].dropna().unique().tolist() if str(d).strip()]
    if dong_list:
        gs_col3, gs_col4 = st.columns(2)
        with gs_col3: sel_dong = st.selectbox("🎯 지도에서 확인할 배전선로 선택", dong_list, key="gs_dong")
        with gs_col4: search_addr = st.text_input("🔍 참고용 지번 검색 (구글지도 기반)", placeholder="예: 1123", key="gs_addr")

        info = gs_target_df[gs_target_df['고유DL명'] == sel_dong].iloc[0]
        search_target = info['DL명(읍면동)'] + ("동" if not any(info['DL명(읍면동)'].endswith(s) for s in ['동', '읍', '면', '리']) else "")
        lat, lon, nom_geo = get_location_data(f"{gs_sido} {info['시/구/군']} {search_target}", f"{gs_sido} {info['시/구/군']}")
        
        user_lat, user_lon = None, None
        if search_addr.strip():
            user_lat, user_lon, _ = get_location_data(f"{gs_sido} {info['시/구/군']} {search_addr}")

        m1 = folium.Map(location=[lat, lon], zoom_start=13, tiles=None)
        folium.TileLayer(tiles='OpenStreetMap', name='🗺️ 일반지도', overlay=False, control=True, show=True).add_to(m1)
        folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='🛰️ 위성지도', overlay=False, control=True, show=False).add_to(m1)
        
        dong_poly = get_dong_polygon(gs_sido, info['시/구/군'], info['DL명(읍면동)'], get_korea_geojson())
        if dong_poly: folium.GeoJson(dong_poly, style_function=lambda x: {'fillColor': '#FFFF00', 'color': '#FF0000', 'weight': 3, 'fillOpacity': 0.25}).add_to(m1)
            
        folium.Marker([lat, lon], popup=f"{info['DL명(읍면동)']}<br>DL 여유: {info['DL 여유용량']} MW").add_to(m1)
        if user_lat and user_lon:
            folium.Marker([user_lat, user_lon], popup=search_addr, icon=folium.Icon(color='red', icon='star')).add_to(m1)
            m1.fit_bounds([[lat, lon], [user_lat, user_lon]])
        
        folium.LayerControl(position='topright').add_to(m1)
        st_folium(m1, width=1200, height=500, returned_objects=[], key=f"gs_map_{gs_sido}_{gs_sigg}_{sel_dong}")
else: st.warning("데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러를 실행해 주세요.")

st.markdown("---")

# ---------------------------------------------------------
# 2부: 한전 API 실시간 지번 조회 (신규 로직)
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 조회 (한전 API 직접 호출)")
df_regions = get_region_codes()

if not df_regions.empty:
    rt_col1, rt_col2 = st.columns(2)
    with rt_col1:
        rt_sido = st.selectbox("📍 시/도를 선택하세요", sorted(df_regions['시도'].unique().tolist()), key="rt_sido")
    with rt_col2:
        rt_sigg = st.selectbox("🏢 시/군/구를 선택하세요", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg")
    
    target_reg = df_regions[(df_regions['시도'] == rt_sido) & (df_regions['시군구'] == rt_sigg)].iloc[0]
    m_cd, c_cd = target_reg['metroCd'], target_reg['cityCd']

    rt_col3, rt_col4, rt_col5 = st.columns(3)
    with rt_col3: in_lidong = st.text_input("읍/면/동", placeholder="예: 아포읍", key="rt_dong")
    with rt_col4: in_li = st.text_input("리 (선택)", placeholder="예: 대성리", key="rt_li")
    with rt_col5: in_jibun = st.text_input("상세번지 (선택)", placeholder="예: 348", key="rt_jibun")

    if st.button("🚀 실시간 여유용량 조회하기", use_container_width=True):
        with st.spinner("한전 서버에서 실시간 데이터를 가져오는 중입니다..."):
            raw_data = fetch_kepco_realtime(m_cd, c_cd, in_lidong, in_li, in_jibun)
            
            if raw_data:
                rt_df = pd.DataFrame(raw_data)
                rt_df = rt_df.rename(columns={"dlNm": "DL명(읍면동)", "vol3": "DL 여유용량", "vol2": "변압기 여유용량", "vol1": "변전소 여유용량", "substNm": "변전소명"})
                
                for col in ["DL 여유용량", "변압기 여유용량", "변전소 여유용량"]:
                    if col in rt_df.columns: rt_df[col] = pd.to_numeric(rt_df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
                
                rt_df['연계가능여부'] = (rt_df['변압기 여유용량'] > 0) & (rt_df['변전소 여유용량'] > 0)
                rt_df['연계상태'] = rt_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
                rt_df = rt_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False]).drop_duplicates(subset=['DL명(읍면동)']).reset_index(drop=True)
                rt_df["순위"] = range(1, len(rt_df) + 1)
                
                st.success(f"✅ 총 {len(rt_df)}개의 실시간 선로 데이터를 성공적으로 불러왔습니다.")
                rt_cols = ["순위", "연계상태", "DL명(읍면동)", "DL 여유용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
                st.dataframe(rt_df[[c for c in rt_cols if c in rt_df.columns]], use_container_width=True, hide_index=True)
                
                full_addr = f"{rt_sido} {rt_sigg} {in_lidong} {in_li} {in_jibun}".strip()
                rt_lat, rt_lon, _ = get_location_data(full_addr, f"{rt_sido} {rt_sigg}")
                
                m2 = folium.Map(location=[rt_lat, rt_lon], zoom_start=14, tiles=None)
                folium.TileLayer('OpenStreetMap', name='🗺️ 일반지도', show=True).add_to(m2)
                folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='🛰️ 위성지도', show=False).add_to(m2)
                
                best = rt_df.iloc[0]
                folium.Marker(
                    [rt_lat, rt_lon], 
                    popup=f"<b>{full_addr}</b><br>배전선로: {best['DL명(읍면동)']}<br>여유용량: {best['DL 여유용량']} MW", 
                    icon=folium.Icon(color='red', icon='star')
                ).add_to(m2)
                
                folium.LayerControl(position='topright').add_to(m2)
                st_folium(m2, width=1200, height=500, returned_objects=[], key=f"rt_map_{rt_lat}_{rt_lon}")
            else: st.error("⚠️ 해당 조건에 맞는 데이터가 없습니다. 번지수를 비우고 다시 검색해 보세요.")
