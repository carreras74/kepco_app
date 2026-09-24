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
VWORLD_KEY = "FE2792CF-B7DF-4F61-8768-FE0D843209E2"
KEPCO_KEY = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
DOMAIN = "http://localhost"

# ==========================================
# 💡 1. 구글 시트 데이터 로드 (선생님의 전체 크롤링 결과)
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
# 💡 2. 한전 실시간 API 호출 (상세조회용)
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
# 💡 3. 국토부 V-World 모듈 (오류 완벽 수정)
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
            point = res['response']['result']['point']
            return float(point['y']), float(point['x'])
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
            return res['response']['result']['featureCollection'], layer
    except: pass
    return None, None

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
st.title("⚡ 전국 송전여유용량 종합 분석 APP")

# ---------------------------------------------------------
# 📊 1부: 구글 시트 기반 시도별 여유용량 랭킹 (상부)
# ---------------------------------------------------------
st.header("📊 1. 전체 지역 랭킹 조회 (수동 크롤링 데이터 연동)")
df_gs = load_gsheets_data()

if not df_gs.empty:
    gs_col1, gs_col2 = st.columns(2)
    with gs_col1: gs_sido = st.selectbox("📍 시/도를 선택하세요", sorted(df_gs['시/도'].unique().tolist()), key="gs_sido")
    with gs_col2:
        gs_filtered = df_gs[df_gs['시/도'] == gs_sido]
        gs_sigg = st.selectbox("🏢 시/구/군을 선택하세요 (전체 보기 가능)", ["전체"] + sorted(gs_filtered['시/구/군'].unique().tolist()), key="gs_sigg")
        
    gs_target_df = gs_filtered.copy() if gs_sigg == "전체" else gs_filtered[gs_filtered['시/구/군'] == gs_sigg].copy()
    gs_target_df['연계가능여부'] = (gs_target_df['변압기 여유용량'] > 0) & (gs_target_df['변전소 여유용량'] > 0)
    gs_target_df['연계상태'] = gs_target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    
    # 여유용량 많은 순 정렬
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
        with gs_col4: search_addr_top = st.text_input("🔍 참고용 지번 검색 (V-World 기반)", placeholder="예: 1123 또는 산12", key="gs_addr_top")

        info = gs_target_df[gs_target_df['고유DL명'] == sel_dong].iloc[0]
        base_target = info['DL명(읍면동)'] + ("동" if not any(info['DL명(읍면동)'].endswith(s) for s in ['동', '읍', '면', '리']) else "")
        
        # 주소 조합 및 다중 공백 제거
        raw_addr_top = f"{gs_sido} {info['시/구/군']} {search_addr_top.strip() if search_addr_top.strip() else base_target}"
        full_addr_top = re.sub(r'\s+', ' ', raw_addr_top).strip()
        
        coord_top = get_vworld_coord(full_addr_top)
        
        admin_geo_top = None
        parcel_geo_top = None
        lat_top, lon_top = 36.5, 127.5
        
        if coord_top:
            lat_top, lon_top = coord_top
            admin_geo_top, _ = get_vworld_admin_polygon(lat_top, lon_top, full_addr_top)
            has_jibun_top = bool(re.search(r'\d', full_addr_top))
            if has_jibun_top:
                parcel_geo_top = get_vworld_parcel_polygon(lat_top, lon_top)

        start_zoom = 19 if parcel_geo_top else 15
        m1 = folium.Map(location=[lat_top, lon_top], zoom_start=start_zoom, max_zoom=22, tiles=None)

        folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg', attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22).add_to(m1)
        folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{{z}}/{{y}}/{{x}}.png', attr='VWorld Base', name='🗺️ 브이월드 일반지도', overlay=False, control=True, show=False, max_zoom=22).add_to(m1)
        folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Hybrid/{{z}}/{{y}}/{{x}}.png', attr='VWorld Hybrid', name='🏷️ 주소/도로명 글자 오버레이', overlay=True, control=True, show=True, max_zoom=22).add_to(m1)

        folium.WmsTileLayer(
            url="http://api.vworld.kr/req/wms", layers="lp_pa_cbnd_bubun", fmt="image/png", transparent=True, version="1.3.0",
            name="📐 주변 전체 지적도 선 & 지번", overlay=True, control=True, show=True if parcel_geo_top else False,
            key=VWORLD_KEY, domain=DOMAIN
        ).add_to(m1)

        if admin_geo_top:
            folium.GeoJson(admin_geo_top, style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#FF00FF', 'weight': 3, 'fillOpacity': 0.1}, name="🎯 행정구역 넓은 경계선").add_to(m1)

        if parcel_geo_top:
            folium.GeoJson(parcel_geo_top, style_function=lambda x: {'fillColor': '#FF0000', 'color': '#FFD700', 'weight': 6, 'fillOpacity': 0.3}, name="👑 내 땅 지적도 (황금색 띠)").add_to(m1)

        if coord_top:
            folium.Marker([lat_top, lon_top], popup=f"<b>{full_addr_top}</b><br>DL 여유: {info['DL 여유용량']} MW", icon=folium.Icon(color='red', icon='info-sign')).add_to(m1)

        folium.LayerControl(position='topright').add_to(m1)
        st_folium(m1, width=1200, height=500, returned_objects=[], key=f"gs_map_{gs_sido}_{gs_sigg}_{sel_dong}")
else: st.warning("데이터를 불러오지 못했습니다. 크롤러를 통해 구글 시트를 업데이트해 주세요.")

st.markdown("---")

# ---------------------------------------------------------
# 🎯 2부: 상세 주소 실시간 1건 조회 (하부)
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 조회 (한전 API & 브이월드)")

df_regions = get_region_codes()

if not df_regions.empty:
    rt_col1, rt_col2 = st.columns(2)
    with rt_col1:
        rt_sido = st.selectbox("📍 조회 시/도", sorted(df_regions['시도'].unique().tolist()), key="rt_sido_bot")
    with rt_col2:
        rt_sigg = st.selectbox("🏢 조회 시/군/구", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg_bot")
    
    target_reg = df_regions[(df_regions['시도'] == rt_sido) & (df_regions['시군구'] == rt_sigg)].iloc[0]
    m_cd, c_cd = target_reg['metroCd'], target_reg['cityCd']

    rt_col3, rt_col4, rt_col5 = st.columns(3)
    with rt_col3: in_lidong = st.text_input("읍/면/동", placeholder="예: 아포읍", key="rt_dong_bot")
    with rt_col4: in_li = st.text_input("리", placeholder="예: 대성리", key="rt_li_bot")
    with rt_col5: in_jibun = st.text_input("상세번지", placeholder="예: 348", key="rt_jibun_bot")

    # 빈칸을 제외하고 깔끔한 하나의 주소 문자열로 조립 (브이월드 전송용)
    address_parts = [rt_sido, rt_sigg, in_lidong, in_li, in_jibun]
    search_addr = " ".join([p.strip() for p in address_parts if p.strip()])
    
    st.info(f"📍 **조회될 최종 주소:** `{search_addr}`")

    if st.button("🚀 실시간 조회 및 지도 켜기", use_container_width=True):
        if not in_lidong:
            st.warning("읍/면/동을 필수로 입력해주세요.")
        else:
            with st.spinner("한전 실시간 데이터와 브이월드 지도를 가져옵니다..."):
                
                # 💡 1. 한전 API 데이터
                raw_data = fetch_kepco_realtime(m_cd, c_cd, in_lidong.strip(), in_li.strip(), in_jibun.strip())
                rt_info = None
                
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
                    rt_info = rt_df.iloc[0]
                else:
                    st.warning("⚠️ 한전 실시간 API에서 해당 여유용량 데이터를 찾지 못했습니다. 지도를 확인합니다.")

                # 💡 2. 국토부 브이월드 지도 로직
                lat, lon = 36.6573, 128.4528
                admin_geojson = None
                parcel_geojson = None
                
                if search_addr:
                    coord = get_vworld_coord(search_addr)
                    if coord:
                        lat, lon = coord
                        admin_geojson, layer_used = get_vworld_admin_polygon(lat, lon, search_addr)
                        
                        has_jibun = bool(re.search(r'\d', search_addr))
                        if has_jibun:
                            parcel_geojson = get_vworld_parcel_polygon(lat, lon)
                            st.success(f"✅ 상세 번지 감지됨! 글씨가 잘 안 보일 경우 우측 상단 레이어에서 **'🗺️ 브이월드 일반지도'**를 켜보세요. (위도: {lat:.4f}, 경도: {lon:.4f})")
                        else:
                            area_type = "법정리" if layer_used == "lt_c_adri_info" else "읍/면/동"
                            st.success(f"✅ 번지수 없음 감지됨! **{area_type}** 단위의 행정구역 경계만 그립니다.")
                    else:
                        st.error("⚠️ 브이월드에서 주소를 찾지 못했습니다. 정확히 띄어쓰기하여 입력해 주세요.")

                start_zoom = 19 if parcel_geojson else 15
                m2 = folium.Map(location=[lat, lon], zoom_start=start_zoom, max_zoom=22, tiles=None)

                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg', attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22).add_to(m2)
                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{{z}}/{{y}}/{{x}}.png', attr='VWorld Base', name='🗺️ 브이월드 일반지도', overlay=False, control=True, show=False, max_zoom=22).add_to(m2)
                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Hybrid/{{z}}/{{y}}/{{x}}.png', attr='VWorld Hybrid', name='🏷️ 주소/도로명 글자 오버레이', overlay=True, control=True, show=True, max_zoom=22).add_to(m2)

                folium.WmsTileLayer(
                    url="http://api.vworld.kr/req/wms", layers="lp_pa_cbnd_bubun", fmt="image/png", transparent=True, version="1.3.0",
                    name="📐 주변 전체 지적도 선 & 지번", overlay=True, control=True, show=True if parcel_geojson else False,
                    key=VWORLD_KEY, domain=DOMAIN
                ).add_to(m2)

                if admin_geojson:
                    folium.GeoJson(admin_geojson, style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#FF00FF', 'weight': 3, 'fillOpacity': 0.1}, name="🎯 행정구역 넓은 경계선").add_to(m2)

                if parcel_geojson:
                    folium.GeoJson(parcel_geojson, style_function=lambda x: {'fillColor': '#FF0000', 'color': '#FFD700', 'weight': 6, 'fillOpacity': 0.3}, name="👑 내 땅 지적도 (황금색 띠)").add_to(m2)

                if search_addr and coord:
                    popup_text = f"<b>{search_addr}</b>"
                    if rt_info is not None:
                        popup_text += f"<br>배전선로: {rt_info['DL명(읍면동)']}<br>여유용량: {rt_info['DL 여유용량']} MW"
                    folium.Marker([lat, lon], popup=popup_text, icon=folium.Icon(color='red', icon='info-sign')).add_to(m2)

                folium.LayerControl(position='topright').add_to(m2)
                st_folium(m2, width=1300, height=750, returned_objects=[], key=f"vworld_map_final_{lat}_{lon}")
