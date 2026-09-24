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
# 💡 API Keys & Settings (선생님이 찾아내신 도메인 완벽 적용)
# ==========================================
VWORLD_KEY = "FE2792CF-B7DF-4F61-8768-FE0D843209E2"
KEPCO_KEY = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
DOMAIN = "https://kepcoapp-biwrxqmgtjrbamcm48ikmr.streamlit.app" # 💡 실제 스트림릿 주소로 변경!
VWORLD_HEADERS = {"Referer": DOMAIN} # 💡 브이월드 보안 차단 방지용 헤더 추가

# ==========================================
# 💡 1. 구글 시트 데이터 로드 (1번 화면용)
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
# 💡 2. 한전 실시간 API 호출 (2번 화면용)
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
# 💡 3. 국토부 V-World 모듈 (도메인 보안 강화)
# ==========================================
def get_vworld_coord(address):
    url = "http://api.vworld.kr/req/address"
    params = {"service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326", "address": address, "refine": "true", "simple": "false", "format": "json", "type": "parcel", "key": VWORLD_KEY}
    try:
        res = requests.get(url, params=params, headers=VWORLD_HEADERS).json()
        if res.get('response', {}).get('status') == 'OK':
            point = res['response']['result']['point']
            return float(point['y']), float(point['x'])
    except: pass
    return None

def get_vworld_admin_polygon(lat, lon, address_text):
    layer = "lt_c_adri_info" if "리" in address_text.split()[-1] or "리 " in address_text else "lt_c_ademd_info"
    url = "http://api.vworld.kr/req/data"
    params = {"service": "data", "request": "GetFeature", "data": layer, "key": VWORLD_KEY, "domain": DOMAIN, "geomFilter": f"POINT({lon} {lat})", "crs": "EPSG:4326", "geometry": "true", "size": "1"}
    try:
        res = requests.get(url, params=params, headers=VWORLD_HEADERS).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection'], layer
    except: pass
    return None, None

def get_vworld_parcel_polygon(lat, lon):
    url = "http://api.vworld.kr/req/data"
    params = {"service": "data", "request": "GetFeature", "data": "lp_pa_cbnd_bubun", "key": VWORLD_KEY, "domain": DOMAIN, "geomFilter": f"POINT({lon} {lat})", "crs": "EPSG:4326", "geometry": "true", "size": "1"}
    try:
        res = requests.get(url, params=params, headers=VWORLD_HEADERS).json()
        if res.get('response', {}).get('status') == 'OK':
            return res['response']['result']['featureCollection']
    except: pass
    return None

# ==========================================
# 🖥️ 앱 화면 구성 시작
# ==========================================
st.title("⚡ 전국 송전여유용량 대시보드 APP")

# ---------------------------------------------------------
# 📊 1부: 구글 시트 기반 시도별 여유용량 랭킹 (표 전용)
# ---------------------------------------------------------
st.header("📊 1. 지역별 최대 여유용량 랭킹 (수동 크롤링 연동)")
df_gs = load_gsheets_data()

if not df_gs.empty:
    gs_col1, gs_col2 = st.columns(2)
    with gs_col1: gs_sido = st.selectbox("📍 랭킹 조회 시/도", sorted(df_gs['시/도'].unique().tolist()), key="gs_sido")
    with gs_col2:
        gs_filtered = df_gs[df_gs['시/도'] == gs_sido]
        gs_sigg = st.selectbox("🏢 랭킹 조회 시/구/군 (전체 보기 가능)", ["전체"] + sorted(gs_filtered['시/구/군'].unique().tolist()), key="gs_sigg")
        
    gs_target_df = gs_filtered.copy() if gs_sigg == "전체" else gs_filtered[gs_filtered['시/구/군'] == gs_sigg].copy()
    gs_target_df['연계가능여부'] = (gs_target_df['변압기 여유용량'] > 0) & (gs_target_df['변전소 여유용량'] > 0)
    gs_target_df['연계상태'] = gs_target_df['연계가능여부'].apply(lambda x: '🟢 가능' if x else '🔴 불가(용량부족)')
    
    gs_target_df = gs_target_df.sort_values(by=["연계가능여부", "DL 여유용량"], ascending=[False, False])
    gs_target_df['고유DL명'] = gs_target_df['시/구/군'] + " " + gs_target_df['DL명(읍면동)']
    gs_target_df = gs_target_df.drop_duplicates(subset=['고유DL명'], keep='first').reset_index(drop=True)
    gs_target_df["순위"] = range(1, len(gs_target_df) + 1)
    
    gs_cols = ["순위", "연계상태", "시/구/군", "DL명(읍면동)", "DL 여유용량", "DL 누적연계용량", "변압기 여유용량", "변전소 여유용량", "변전소명"]
    st.dataframe(gs_target_df[[c for c in gs_cols if c in gs_target_df.columns]], use_container_width=True, hide_index=True)
else: st.warning("데이터를 불러오지 못했습니다. 로컬 PC에서 크롤러(`api_crawler.py`)를 실행해 주세요.")

st.markdown("---")

# ---------------------------------------------------------
# 🎯 2부: 상세 주소 실시간 데이터 1건 조회 (한전 데이터 전용)
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 1건 조회 (한전 여유용량 확인)")
df_regions = get_region_codes()

if not df_regions.empty:
    rt_col1, rt_col2 = st.columns(2)
    with rt_col1:
        rt_sido = st.selectbox("📍 실시간 조회 시/도", sorted(df_regions['시도'].unique().tolist()), key="rt_sido_bot")
    with rt_col2:
        rt_sigg = st.selectbox("🏢 실시간 조회 시/군/구", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg_bot")
    
    target_reg = df_regions[(df_regions['시도'] == rt_sido) & (df_regions['시군구'] == rt_sigg)].iloc[0]
    m_cd, c_cd = target_reg['metroCd'], target_reg['cityCd']

    rt_col3, rt_col4, rt_col5 = st.columns(3)
    with rt_col3: in_lidong = st.text_input("읍/면/동 (필수)", placeholder="예: 아포읍", key="rt_dong_bot")
    with rt_col4: in_li = st.text_input("리 (선택)", placeholder="예: 대성리", key="rt_li_bot")
    with rt_col5: in_jibun = st.text_input("상세번지 (선택)", placeholder="예: 348 또는 산12", key="rt_jibun_bot")

    # 💡 2번과 3번에서 공통으로 사용할 최종 결합 주소
    raw_addr_bot = f"{rt_sido} {rt_sigg} {in_lidong} {in_li} {in_jibun}"
    full_addr_bot = re.sub(r'\s+', ' ', raw_addr_bot).strip()
    
    st.info(f"📍 **현재 입력된 주소:** `{full_addr_bot}`")

    if st.button("🚀 한전 실시간 여유용량 조회하기", use_container_width=True):
        if not in_lidong:
            st.warning("읍/면/동 정보는 필수로 입력해주세요.")
        else:
            with st.spinner("한전 서버에서 해당 지번 1건을 실시간 조회 중입니다..."):
                raw_data = fetch_kepco_realtime(m_cd, c_cd, in_lidong.strip(), in_li.strip(), in_jibun.strip())
                
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
                else:
                    st.warning("⚠️ 한전 실시간 API에서 해당 번지/동네에 대한 여유용량 데이터를 찾지 못했습니다.")

st.markdown("---")

# ---------------------------------------------------------
# 🗺️ 3부: 국토부 V-World 정밀 지적도 (지도 전용 독립 모듈)
# ---------------------------------------------------------
st.header("🗺️ 3. 국토부 V-World 정밀 지적도 조회")
st.markdown("위 2번 항목에서 세팅된 주소값을 바탕으로, 한전 데이터 성공/실패 여부와 상관없이 **무조건 독립적으로 렌더링**됩니다.")

if st.button("🚀 브이월드 정밀 지도 켜기", use_container_width=True):
    if not in_lidong:
        st.warning("위 2번 항목에서 최소 '읍/면/동'까지는 입력하신 후 버튼을 눌러주세요.")
    else:
        with st.spinner("국토부 V-World 지도를 렌더링 중입니다... (스트림릿 도메인 인증 적용됨)"):
            coord_bot = get_vworld_coord(full_addr_bot)
            
            if coord_bot:
                rt_lat, rt_lon = coord_bot
                admin_geo_bot, layer_used_bot = get_vworld_admin_polygon(rt_lat, rt_lon, full_addr_bot)
                has_jibun_bot = bool(re.search(r'\d', full_addr_bot))
                parcel_geo_bot = get_vworld_parcel_polygon(rt_lat, rt_lon) if has_jibun_bot else None
                
                if has_jibun_bot and parcel_geo_bot:
                    st.success(f"✅ 상세 지번 지적도 모드 활성화 (위도: {rt_lat:.4f}, 경도: {rt_lon:.4f})")
                else:
                    st.info(f"✅ 읍/면/동/리 행정구역 경계 모드 활성화")

                # 상세 번지가 있으면 확 줌인(19), 없으면 동네가 보이도록 줌아웃(15)
                m3 = folium.Map(location=[rt_lat, rt_lon], zoom_start=19 if parcel_geo_bot else 15, max_zoom=22, tiles=None)
                
                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg', attr='VWorld', name='🛰️ 브이월드 위성지도', show=True, max_zoom=22).add_to(m3)
                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Base/{{z}}/{{y}}/{{x}}.png', attr='VWorld', name='🗺️ 브이월드 일반지도', show=False, max_zoom=22).add_to(m3)
                folium.TileLayer(tiles=f'http://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Hybrid/{{z}}/{{y}}/{{x}}.png', attr='VWorld', name='🏷️ 하이브리드 오버레이', overlay=True, show=True, max_zoom=22).add_to(m3)
                
                # 주변 지적도 선 표출 유무 (상세 번지가 있으면 자동으로 켬)
                folium.WmsTileLayer(url="http://api.vworld.kr/req/wms", layers="lp_pa_cbnd_bubun", fmt="image/png", transparent=True, version="1.3.0", name="📐 주변 전체 지적도 (농지/산지 포함)", overlay=True, show=True if parcel_geo_bot else False, key=VWORLD_KEY, domain=DOMAIN).add_to(m3)
                
                # 경계선 그리기
                if admin_geo_bot: folium.GeoJson(admin_geo_bot, style_function=lambda x: {'fillColor': '#00FFFF', 'color': '#FF00FF', 'weight': 3, 'fillOpacity': 0.1}).add_to(m3)
                if parcel_geo_bot: folium.GeoJson(parcel_geo_bot, style_function=lambda x: {'fillColor': '#FF0000', 'color': '#FFD700', 'weight': 6, 'fillOpacity': 0.3}).add_to(m3)
                
                folium.Marker([rt_lat, rt_lon], popup=f"<b>{full_addr_bot}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m3)
                folium.LayerControl(position='topright').add_to(m3)
                
                st_folium(m3, width=1200, height=700, returned_objects=[], key=f"rt_map_3_{rt_lat}_{rt_lon}")
            else:
                st.error(f"⚠️ V-World 지도에서 '{full_addr_bot}' 주소의 좌표를 찾을 수 없습니다. 오타를 확인해 주세요.")
