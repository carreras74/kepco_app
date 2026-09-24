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
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="전국 송전여유용량 대시보드", page_icon="⚡", layout="wide")

# ==========================================
# 💡 API Keys & Settings
# ==========================================
# 새로 발급받은 웹사이트 전용 인증키 적용
vworld_key = "013A53E5-52AB-4FD2-AC9D-B7D4A85D5667"
KEPCO_KEY = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
# 브이월드 서버 승인용 실제 스트림릿 도메인
domain = "https://kepcoapp-biwrxqmgtjrbamcm48ikmr.streamlit.app"

# ==========================================
# 📊 1부: 구글 시트 데이터 로드 (전체 크롤링 연동)
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
        sheet = client.open_by_url("https://docs.google.com/spreadsheets/d/1QsHxBwA40ElWl9AAMf1HrKXjXRI_QyhHdgTOqWnZQQk/edit?gid=2075043511#gid=2075043511").get_worksheet(0)
        df = pd.DataFrame(sheet.get_all_records())

        for col in ["DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변압기 누적 연계용량"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
        return df
    except: return pd.DataFrame()

# ==========================================
# 🎯 2부: 한전 실시간 API 호출 (상세조회용)
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

    for attempt in range(3):
        try:
            res = requests.get(url, params=params, timeout=10)
            if res.status_code == 200:
                data = res.json().get("data", [])
                if data: return data
        except: pass
        time.sleep(1)
    return []

# ==========================================
# 🖥️ 앱 화면 구성 시작
# ==========================================
st.title("⚡ 전국 송전여유용량 종합 분석 APP")

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
# 🎯 2. 한전 API 실시간 다이렉트 지번 조회
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 1건 조회 (한전 여유용량 확인)")
df_regions = get_region_codes()

if not df_regions.empty:
    rt_col1, rt_col2 = st.columns(2)
    with rt_col1: rt_sido = st.selectbox("📍 실시간 조회 시/도", sorted(df_regions['시도'].unique().tolist()), key="rt_sido_bot")
    with rt_col2: rt_sigg = st.selectbox("🏢 실시간 조회 시/군/구", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg_bot")
    
    target_reg = df_regions[(df_regions['시도'] == rt_sido) & (df_regions['시군구'] == rt_sigg)].iloc[0]
    
    rt_col3, rt_col4, rt_col5 = st.columns(3)
    with rt_col3: in_lidong = st.text_input("읍/면/동 (필수)", placeholder="예: 아포읍")
    with rt_col4: in_li = st.text_input("리 (선택)", placeholder="예: 대성리")
    with rt_col5: in_jibun = st.text_input("상세번지 (선택)", placeholder="예: 348 또는 산12")

    if st.button("🚀 한전 실시간 여유용량 조회하기", use_container_width=True):
        if not in_lidong: st.warning("읍/면/동을 입력해주세요.")
        else:
            with st.spinner("한전 서버에서 해당 지번 1건을 실시간 조회 중입니다..."):
                raw_data = fetch_kepco_realtime(target_reg['metroCd'], target_reg['cityCd'], in_lidong.strip(), in_li.strip(), in_jibun.strip())
                if raw_data:
                    rt_df = pd.DataFrame(raw_data)
                    rt_df = rt_df.rename(columns={"dlNm": "DL명(읍면동)", "vol3": "DL 여유용량", "vol2": "변압기 여유용량", "vol1": "변전소 여유용량", "substNm": "변전소명"})
                    for col in ["DL 여유용량", "변압기 여유용량", "변전소 여유용량"]:
                        if col in rt_df.columns: rt_df[col] = pd.to_numeric(rt_df[col].astype(str).str.replace(",", ""), errors='coerce').fillna(0) / 1000.0
                    rt_df['연계가능여부'] = (rt_df['변압기 여유용량'] > 0) & (rt_df['변전소 여유용량'] > 0)
                    rt_df['연계상태'] = rt_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
                    rt_df = rt_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False]).drop_duplicates(subset=['DL명(읍면동)']).reset_index(drop=True)
                    rt_df["순위"] = range(1, len(rt_df) + 1)
                    st.dataframe(rt_df[["순위", "연계상태", "DL명(읍면동)", "DL 여유용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]], use_container_width=True, hide_index=True)
                else: st.warning("⚠️ 한전 실시간 API에서 해당 번지/동네에 대한 여유용량 데이터를 찾지 못했습니다.")

st.markdown("---")

# ==========================================
# 🗺️ 3. 브이월드 정밀 지도 (스트림릿 도메인 인증 + 장소 검색 내장)
# ==========================================
st.header("🗺️ 3. 국토부 브이월드(V-World) 통합 경계 분석 앱")
st.markdown("동네 이름만 검색하면 **행정구역 경계**를, 번지수까지 검색하면 **내 땅의 상세 지적도(황금색 띠)**를 그리며, 주변 지번을 뚜렷하게 확인합니다.")

search_addr = st.text_input("🔍 지도 주소 직접 검색 (읍/면/동/리 또는 상세 번지)", placeholder="예: 김천시 아포읍 또는 예천군 호명읍 산합리 1123")

def get_vworld_coord(address, api_key):
    # 💡 1차: 정확한 지번이 있을 때의 정밀 주소 검색
    url_addr = "http://api.vworld.kr/req/address"
    params_addr = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "address": address, "refine": "true", "simple": "false", "format": "json", "type": "parcel", "key": api_key}
    try:
        res = requests.get(url_addr, params=params_addr, headers={"Referer": domain}).json()
        if res.get('response', {}).get('status') == 'OK':
            point = res['response']['result']['point']
            return float(point['y']), float(point['x'])
    except: pass

    # 💡 2차: "김천시 아포읍" 처럼 지번이 없어 1차에서 튕겼을 때 동네 중앙을 찾아주는 장소 검색
    url_search = "http://api.vworld.kr/req/search"
    params_search = {"service": "search", "request": "search", "version": "2.0", "crs": "epsg:4326", "query": address, "type": "place", "format": "json", "key": api_key}
    try:
        res2 = requests.get(url_search, params=params_search, headers={"Referer": domain}).json()
        if res2.get('response', {}).get('status') == 'OK':
            items = res2['response']['result']['items']
            if items:
                point = items[0]['point']
                return float(point['y']), float(point['x'])
    except: pass
    
    return None

def get_vworld_admin_polygon(lat, lon, address_text, api_key):
    layer = "lt_c_adri_info" if "리" in address_text.split()[-1] or "리 " in address_text else "lt_c_ademd_info"
    url = "http://api.vworld.kr/req/data"
    params = {
        "service": "data", "request": "GetFeature", "data": layer, "key": api_key, "domain": domain,
        "geomFilter": f"POINT({lon} {lat})", "crs": "EPSG:4326", "geometry": "true", "size": "1"
    }
    try:
        res = requests.get(url, params=params, headers={"Referer": domain}).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection'], layer
    except: pass
    return None, None

def get_vworld_parcel_polygon(lat, lon, api_key):
    url = "http://api.vworld.kr/req/data"
    params = {
        "service": "data", "request": "GetFeature", "data": "lp_pa_cbnd_bubun", "key": api_key, "domain": domain,
        "geomFilter": f"POINT({lon} {lat})", "crs": "EPSG:4326", "geometry": "true", "size": "1"
    }
    try:
        res = requests.get(url, params=params, headers={"Referer": domain}).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection']
    except: pass
    return None

lat, lon = 36.6573, 128.4528
admin_geojson = None
parcel_geojson = None
layer_used = None

if search_addr:
    coord = get_vworld_coord(search_addr, vworld_key)
    if coord:
        lat, lon = coord
        admin_geojson, layer_used = get_vworld_admin_polygon(lat, lon, search_addr, vworld_key)
        
        has_jibun = bool(re.search(r'\d', search_addr))
        if has_jibun:
            parcel_geojson = get_vworld_parcel_polygon(lat, lon, vworld_key)
            st.success(f"✅ 상세 번지 감지됨! 글씨가 잘 안 보일 경우 우측 상단 레이어에서 **'🗺️ 브이월드 일반지도'**를 켜보세요. (위도: {lat:.4f}, 경도: {lon:.4f})")
        else:
            area_type = "법정리" if layer_used == "lt_c_adri_info" else "읍/면/동"
            st.success(f"✅ 번지수 없음 감지됨! **{area_type}** 단위의 행정구역 경계만 그립니다.")
    else:
        st.error("⚠️ 주소를 찾지 못했습니다. 정확히 띄어쓰기하여 입력해 주세요.")

start_zoom = 19 if parcel_geojson else 15
m = folium.Map(location=[lat, lon], zoom_start=start_zoom, max_zoom=22, tiles=None)

folium.TileLayer(
    tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{vworld_key}/Satellite/{{z}}/{{y}}/{{x}}.jpeg',
    attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22
).add_to(m)

folium.TileLayer(
    tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{vworld_key}/Base/{{z}}/{{y}}/{{x}}.png',
    attr='VWorld Base', name='🗺️ 브이월드 일반지도', overlay=False, control=True, show=False, max_zoom=22
).add_to(m)

folium.TileLayer(
    tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{vworld_key}/Hybrid/{{z}}/{{y}}/{{x}}.png',
    attr='VWorld Hybrid', name='🏷️ 주소/도로명 글자 오버레이', overlay=True, control=True, show=True, max_zoom=22
).add_to(m)

folium.WmsTileLayer(
    url="http://api.vworld.kr/req/wms", layers="lp_pa_cbnd_bubun", fmt="image/png", transparent=True, version="1.3.0",
    name="📐 주변 전체 지적도 선 & 지번", overlay=True, control=True, show=True if parcel_geojson else False,
    key=vworld_key, domain=domain
).add_to(m)

if admin_geojson:
    folium.GeoJson(admin_geojson, style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#FF00FF', 'weight': 3, 'fillOpacity': 0.1}, name="🎯 행정구역 넓은 경계선").add_to(m)

if parcel_geojson:
    folium.GeoJson(parcel_geojson, style_function=lambda x: {'fillColor': '#FF0000', 'color': '#FFD700', 'weight': 6, 'fillOpacity': 0.3}, name="👑 내 땅 지적도 (황금색 띠)").add_to(m)

if search_addr and coord:
    folium.Marker([lat, lon], popup=f"<b>{search_addr}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)

folium.LayerControl(position='topright').add_to(m)
st_folium(m, width=1300, height=750, returned_objects=[], key=f"vworld_map_full_{lat}_{lon}")
