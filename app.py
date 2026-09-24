import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import folium
from streamlit_folium import st_folium
import requests
import json
import os
import re

st.set_page_config(page_title="전국 송전여유용량 실시간 파악 APP", page_icon="⚡", layout="wide")

# 💡 V-World API 키 및 기본 설정
VWORLD_KEY = "FE2792CF-B7DF-4F61-8768-FE0D843209E2"
DOMAIN = "http://localhost"

# ==========================================
# 💡 1. 구글 시트 데이터 로드 (한전 크롤링 데이터)
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
        
        # 선생님의 구글 시트 주소
        client = gspread.authorize(creds)
        sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1QsHxBwA40ElWl9AAMf1HrKXjXRI_QyhHdgTOqWnZQQk/edit?gid=2075043511#gid=2075043511").get_worksheet(0)
        df = pd.DataFrame(sheet.get_all_records())
        
        for col in ["DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변압기 누적 연계용량"]:
            if col in df.columns: 
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
        return df
    except: return pd.DataFrame()

# ==========================================
# 💡 2. V-World 지도 엔진 함수 (농지/산지 포함)
# ==========================================
def get_vworld_coord(address):
    url = "http://api.vworld.kr/req/address"
    params = {
        "service": "address", "request": "getcoord", "version": "2.0",
        "crs": "epsg:4326", "address": address, "refine": "true",
        "simple": "false", "format": "json", "type": "parcel", "key": VWORLD_KEY
    }
    try:
        res = requests.get(url, params=params).json()
        if res.get('response', {}).get('status') == 'OK':
            p = res['response']['result']['point']
            return float(p['y']), float(p['x'])
    except: pass
    return None

def get_vworld_admin_polygon(lat, lon, address_text):
    layer = "lt_c_adri_info" if "리" in address_text.split()[-1] or "리 " in address_text else "lt_c_ademd_info"
    url = "http://api.vworld.kr/req/data"
    params = {
        "service": "data", "request": "GetFeature", "data": layer,
        "key": VWORLD_KEY, "domain": DOMAIN, "geomFilter": f"POINT({lon} {lat})",
        "crs": "EPSG:4326", "geometry": "true", "size": "1"
    }
    try:
        res = requests.get(url, params=params).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection']
    except: pass
    return None

def get_vworld_parcel_polygon(lat, lon):
    url = "http://api.vworld.kr/req/data"
    params = {
        "service": "data", "request": "GetFeature", "data": "lp_pa_cbnd_bubun",
        "key": VWORLD_KEY, "domain": DOMAIN, "geomFilter": f"POINT({lon} {lat})",
        "crs": "EPSG:4326", "geometry": "true", "size": "1"
    }
    try:
        res = requests.get(url, params=params).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection']
    except: pass
    return None

# ==========================================
# 🖥️ 앱 화면 구성 시작
# ==========================================
st.title("⚡ 전국 송전여유용량 실시간 파악 APP")
st.markdown("매일 1회 로컬에서 업데이트된 **구글 시트 랭킹**과 국토부 **V-World 정밀 지적도(산지/농지 포함)**를 결합한 대시보드입니다.")

df_gs = load_gsheets_data()

if not df_gs.empty:
    st.header("📊 1. 한전 여유용량 시/도별 순위 랭킹")
    col1, col2 = st.columns(2)
    with col1: 
        gs_sido = st.selectbox("📍 시/도를 선택하세요", sorted(df_gs['시/도'].unique().tolist()))
    with col2:
        gs_filtered = df_gs[df_gs['시/도'] == gs_sido]
        gs_sigg = st.selectbox("🏢 시/구/군을 선택하세요 (전체 보기 가능)", ["전체"] + sorted(gs_filtered['시/구/군'].unique().tolist()))
        
    gs_target_df = gs_filtered.copy() if gs_sigg == "전체" else gs_filtered[gs_filtered['시/구/군'] == gs_sigg].copy()
    gs_target_df['연계가능여부'] = (gs_target_df['변압기 여유용량'] > 0) & (gs_target_df['변전소 여유용량'] > 0)
    gs_target_df['연계상태'] = gs_target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    
    # 💡 최대 용량 많은 곳부터 순서대로 보여주기 (선생님 요청 4번 완벽 구현)
    gs_target_df = gs_target_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False])
    gs_target_df['고유DL명'] = gs_target_df['시/구/군'] + " " + gs_target_df['DL명(읍면동)']
    gs_target_df = gs_target_df.drop_duplicates(subset=['고유DL명'], keep='first').reset_index(drop=True)
    gs_target_df["순위"] = range(1, len(gs_target_df) + 1)
    
    gs_cols = ["순위", "연계상태", "시/구/군", "DL명(읍면동)", "DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
    st.dataframe(gs_target_df[[c for c in gs_cols if c in gs_target_df.columns]], use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    st.header("🗺️ 2. 국토부 V-World 정밀 지적도 분석 (농지/산지 포함)")
    
    dong_list = [d for d in gs_target_df['고유DL명'].dropna().unique().tolist() if str(d).strip()]
    if dong_list:
        col3, col4 = st.columns(2)
        with col3: 
            sel_dong = st.selectbox("🎯 지도에서 확인할 배전선로 선택 (기본 이동)", dong_list)
        with col4: 
            # 산지 검색 시 "산12" 형태로 입력 안내 추가
            search_addr = st.text_input("🔍 정밀 지번 검색 (산지는 '산'을 붙이세요)", placeholder="예: 아포읍 대성리 산348")

        # 💡 주소 위치 결정 로직
        info = gs_target_df[gs_target_df['고유DL명'] == sel_dong].iloc[0]
        # 기본 위치: 선택한 배전선로 동네
        base_target = info['DL명(읍면동)'] + ("동" if not any(info['DL명(읍면동)'].endswith(s) for s in ['동', '읍', '면', '리']) else "")
        target_address = f"{gs_sido} {info['시/구/군']} {base_target}"
        
        lat, lon = 36.6573, 128.4528 # 기본값
        is_detail_search = False
        
        # 검색창에 주소를 쳤다면 그 주소를 우선순위로 변환
        if search_addr.strip():
            coord = get_vworld_coord(f"{gs_sido} {info['시/구/군']} {search_addr}")
            if coord:
                lat, lon = coord
                target_address = search_addr
                is_detail_search = True
        else:
            coord = get_vworld_coord(target_address)
            if coord: lat, lon = coord

        admin_geojson = get_vworld_admin_polygon(lat, lon, target_address)
        parcel_geojson = None
        
        # 번지수(숫자)가 포함되어 있다면 산지/농지 포함 상세 지적도 호출
        has_jibun = bool(re.search(r'\d', target_address))
        if has_jibun and is_detail_search:
            parcel_geojson = get_vworld_parcel_polygon(lat, lon)
            st.success("✅ 상세 번지가 검색되었습니다. **농지나 산지의 지번**은 지도 우측 상단 레이어에서 **'📐 주변 전체 지적도 선 & 지번'**이 체크되어 있어야 보입니다!")

        # 농지/산지 지번이 잘 보이도록 줌 레벨을 19로 당김
        start_zoom = 19 if parcel_geojson else 15
        m = folium.Map(location=[lat, lon], zoom_start=start_zoom, max_zoom=22, tiles=None)

        # 1. 🛰️ 브이월드 위성지도
        folium.TileLayer(
            tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg',
            attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22
        ).add_to(m)

        # 2. 🗺️ 브이월드 일반지도 (농지 지번이 위성지도에서 잘 안 보일 때 켜면 흰 배경에 매우 뚜렷함)
        folium.TileLayer(
            tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{{z}}/{{y}}/{{x}}.png',
            attr='VWorld Base', name='🗺️ 브이월드 일반지도', overlay=False, control=True, show=False, max_zoom=22
        ).add_to(m)

        # 3. 🏷️ 하이브리드 (건물명, 도로명)
        folium.TileLayer(
            tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Hybrid/{{z}}/{{y}}/{{x}}.png',
            attr='VWorld Hybrid', name='🏷️ 주소/도로명 글자 오버레이', overlay=True, control=True, show=True, max_zoom=22
        ).add_to(m)

        # 4. 📐 주변 전체 지적도 및 농지/산지 지번 (WMS)
        folium.WmsTileLayer(
            url="http://api.vworld.kr/req/wms",
            layers="lp_pa_cbnd_bubun",
            fmt="image/png",
            transparent=True,
            version="1.3.0",
            name="📐 주변 전체 지적도 선 & 지번 (농지/산지 포함)",
            overlay=True,
            control=True,
            show=True, # 💡 상세 지번을 찾았든 안 찾았든 무조건 기본으로 켜지게 설정 (선생님 요청)
            key=VWORLD_KEY,
            domain=DOMAIN
        ).add_to(m)

        # 5. 행정구역 핑크색 테두리
        if admin_geojson:
            folium.GeoJson(
                admin_geojson,
                style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#FF00FF', 'weight': 3, 'fillOpacity': 0.1},
                name="🎯 행정구역 경계선"
            ).add_to(m)

        # 6. 내 땅 황금색 테두리
        if parcel_geojson:
            folium.GeoJson(
                parcel_geojson,
                style_function=lambda x: {'fillColor': '#FF0000', 'color': '#FFD700', 'weight': 6, 'fillOpacity': 0.3},
                name="👑 내 땅 지적도 (황금색 띠)"
            ).add_to(m)

        if is_detail_search:
            folium.Marker([lat, lon], popup=f"<b>{target_address}</b><br>해당 지역 선로: {info['DL명(읍면동)']}<br>여유용량: {info['DL 여유용량']} MW", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)

        folium.LayerControl(position='topright').add_to(m)
        st_folium(m, width=1300, height=750, returned_objects=[], key=f"vworld_main_map_{lat}_{lon}")

else: 
    st.warning("⚠️ 데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러(`api_crawler.py`)를 실행하여 구글 시트를 업데이트해 주세요.")
