import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
import os, json, requests, re

st.set_page_config(page_title="전국 분산전원 연계 여유용량 실시간 맵", page_icon="⚡", layout="wide")

@st.cache_data(ttl=86400)
def get_location_data(address, fallback_address):
    geolocator = Nominatim(user_agent="kepco_app_hwang")
    try:
        loc = geolocator.geocode(address, geometry='geojson')
        if loc: return loc.latitude, loc.longitude, loc.raw.get('geojson')
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

@st.cache_data(ttl=600)
def load_data():
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

df = load_data()
st.title("⚡ 전국 분산전원 연계 여유용량 실시간 맵")

if not df.empty:
    col1, col2 = st.columns(2)
    with col1: selected_sido = st.selectbox("📍 1. 시/도를 선택하세요", sorted(df['시/도'].unique().tolist()))
    with col2:
        filtered = df[df['시/도'] == selected_sido]
        selected_sigg = st.selectbox("🏢 2. 시/구/군을 선택하세요 (전체 보기 가능)", ["전체"] + sorted(filtered['시/구/군'].unique().tolist()))
        
    target_df = filtered.copy() if selected_sigg == "전체" else filtered[filtered['시/구/군'] == selected_sigg].copy()
    
    target_df['연계가능여부'] = (target_df['변압기 여유용량'] > 0) & (target_df['변전소 여유용량'] > 0)
    target_df['연계상태'] = target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    
    target_df = target_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False])
    target_df['고유DL명'] = target_df['시/구/군'] + " " + target_df['DL명(읍면동)']
    target_df = target_df.drop_duplicates(subset=['고유DL명'], keep='first').reset_index(drop=True)
    target_df["순위"] = range(1, len(target_df) + 1)
    
    cols = ["순위", "연계상태", "시/구/군", "DL명(읍면동)", "DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
    st.dataframe(target_df[[c for c in cols if c in target_df.columns]], use_container_width=True, hide_index=True)
    st.markdown("---")
    
    dong_list = [d for d in target_df['고유DL명'].dropna().unique().tolist() if str(d).strip()]
    if dong_list:
        col3, col4 = st.columns(2)
        with col3: sel_dong = st.selectbox("🎯 3. 지도에서 확인할 배전선로(읍/면/동) 선택", dong_list)
        with col4: search_addr = st.text_input("🔍 내 땅 지번 검색 (선택)", placeholder="예: 예천군 호명읍 산합리 1123")

        info = target_df[target_df['고유DL명'] == sel_dong].iloc[0]
        if info['연계가능여부']: st.success(f"✅ [{info['DL명(읍면동)']}] 권역 내의 지번은 상위 계통 여유가 충분합니다.")
        else: st.error(f"⚠️ [{info['DL명(읍면동)']}] 권역 내의 지번은 배전선로 여유는 있으나 상위 계통 용량이 부족합니다.")
        
        search_target = info['DL명(읍면동)'] + ("동" if not any(info['DL명(읍면동)'].endswith(s) for s in ['동', '읍', '면', '리']) else "")
        lat, lon, nom_geo = get_location_data(f"{selected_sido} {info['시/구/군']} {search_target}", f"{selected_sido} {info['시/구/군']}")
        
        user_lat, user_lon = None, None
        if search_addr.strip():
            try:
                user_loc = Nominatim(user_agent="kepco_app_hwang").geocode(search_addr)
                if user_loc: 
                    user_lat, user_lon = user_loc.latitude, user_loc.longitude
                else:
                    st.toast("⚠️ 글로벌 지도 엔진 한계로 상세 지번을 찾지 못했습니다. 번지수를 빼고 검색해 보세요.")
            except: pass

        m = folium.Map(location=[lat, lon], zoom_start=13)
        folium.TileLayer(tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google Satellite', name='위성지도', overlay=False).add_to(m)
        
        dong_poly = get_dong_polygon(selected_sido, info['시/구/군'], info['DL명(읍면동)'], get_korea_geojson())
        if dong_poly: folium.GeoJson(dong_poly, style_function=lambda x: {'fillColor': '#FFFF00', 'color': '#FF0000', 'weight': 3, 'fillOpacity': 0.25}).add_to(m)
        elif nom_geo and nom_geo.get('type') in ['Polygon', 'MultiPolygon']: folium.GeoJson(nom_geo, style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#0000FF', 'weight': 3, 'fillOpacity': 0.25}).add_to(m)
            
        folium.Marker([lat, lon], popup=f"{info['DL명(읍면동)']}<br>DL 여유: {info['DL 여유용량']} MW").add_to(m)
        if user_lat and user_lon:
            folium.Marker([user_lat, user_lon], popup=f"{search_addr} (권역 용량: {info['DL 여유용량']} MW)", icon=folium.Icon(color='red', icon='star')).add_to(m)
            m.fit_bounds([[lat, lon], [user_lat, user_lon]])
        
        map_key = f"map_{selected_sido}_{selected_sigg}_{sel_dong}_{search_addr}"
        st_folium(m, width=1200, height=600, returned_objects=[], key=map_key)
else: st.warning("데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러를 1회 실행하여 구글 시트를 업데이트해 주세요.")
