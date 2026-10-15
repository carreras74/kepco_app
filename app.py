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
# 💡 스크롤바 영구 고정 패치 (절대 사라지지 않는 붉은색 스크롤바)
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

st.sidebar.title("🛠️ 시스템 도구")
DEBUG_MODE = st.sidebar.checkbox("🔧 통신 디버그 모드 켜기", value=True)

# ==========================================
# 📊 기획 1, 3, 4번: 구글 시트 연동 및 랭킹 정렬
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
# 🎯 기획 2번: 한전 앱 노가다 해방 (실시간 조회)
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

# ==========================================
# 🗺️ 3부: 카카오 좌표 변환 모듈 
# ==========================================
def get_kakao_coord(address):
    debug_log = []
    headers = {"Authorization": f"KakaoAK {KAKAO_KEY}"}
    
    url_addr = "https://dapi.kakao.com/v2/local/search/address.json"
    try:
        r1 = requests.get(url_addr, headers=headers, params={"query": address}, timeout=5)
        debug_log.append(("카카오 주소검색", r1.status_code, r1.text[:300]))
        res = r1.json()
        if res.get('documents'):
            return float(res['documents'][0]['y']), float(res['documents'][0]['x']), debug_log
    except Exception as e:
        debug_log.append(("카카오 주소검색 에러", None, str(e)))

    url_kw = "https://dapi.kakao.com/v2/local/search/keyword.json"
    try:
        r2 = requests.get(url_kw, headers=headers, params={"query": address}, timeout=5)
        debug_log.append(("카카오 키워드검색", r2.status_code, r2.text[:300]))
        res2 = r2.json()
        if res2.get('documents'):
            return float(res2['documents'][0]['y']), float(res2['documents'][0]['x']), debug_log
    except Exception as e:
        debug_log.append(("카카오 키워드검색 에러", None, str(e)))
    
    return None, None, debug_log


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
# 🎯 2. 한전 API 실시간 스마트 다이렉트 조회 (지능형 인접지번 탐색)
# ---------------------------------------------------------
st.header("🎯 2. 상세 주소 실시간 1건 조회 (한전 여유용량 확인)")
st.markdown("한전 DB에 번지수가 누락된 경우, **대표 지번(본번) 및 앞뒤 이웃 지번 10개씩을 기계가 우선순위로 추적**하여 선로를 찾아냅니다.")

df_regions = get_region_codes()

if not df_regions.empty:
    rt_col1, rt_col2 = st.columns(2)
    with rt_col1: rt_sido = st.selectbox("📍 실시간 조회 시/도", sorted(df_regions['시도'].unique().tolist()), key="rt_sido_bot")
    with rt_col2: rt_sigg = st.selectbox("🏢 실시간 조회 시/군/구", sorted(df_regions[df_regions['시도'] == rt_sido]['시군구'].unique().tolist()), key="rt_sigg_bot")

    target_reg = df_regions[(df_regions['시도'] == rt_sido) & (df_regions['시군구'] == rt_sigg)].iloc[0]

    rt_col3, rt_col4, rt_col5 = st.columns(3)
    with rt_col3: in_lidong = st.text_input("읍/면/동 (필수)", placeholder="예: 아포읍")
    with rt_col4: in_li = st.text_input("리 (선택)", placeholder="예: 대성리")
    with rt_col5: in_jibun = st.text_input("상세번지 (선택)", placeholder="예: 122-3 또는 산12")

    if st.button("🚀 한전 실시간 여유용량 조회하기", use_container_width=True):
        if not in_lidong: st.warning("읍/면/동을 입력해주세요.")
        else:
            with st.spinner("한전 서버 조회 및 스마트 인접 지번 탐색 중..."):
                # 💡 1차: 입력한 원본 지번 검색 (예: 122-3)
                raw_data = fetch_kepco_realtime(target_reg['metroCd'], target_reg['cityCd'], in_lidong.strip(), in_li.strip(), in_jibun.strip())
                
                # 💡 2차: 1차 실패 시 아래쪽(-) 번지수 우선으로 앞뒤 10개씩 공격적 탐색
                if not raw_data and in_jibun:
                    is_san = "산" in in_jibun
                    clean_jibun = in_jibun.replace("산", "").strip()
                    
                    # '122-3' 이라면 '122'만 남김
                    if "-" in clean_jibun:
                        base_num_str = clean_jibun.split("-")[0]
                    else:
                        base_num_str = clean_jibun
                        
                    if base_num_str.isdigit():
                        base_num = int(base_num_str)
                        # 선생님 지시 반영: 아래쪽 번호(-1, -2...)를 꼼꼼하게 먼저 찌르도록 순서 조정 & 범위 10개로 확대
                        offsets = [0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6, -7, 7, -8, 8, -9, 9, -10, 10]
                        
                        for offset in offsets:
                            if offset == 0 and "-" not in clean_jibun:
                                continue
                                
                            nearby_j = str(base_num + offset)
                            if is_san: nearby_j = "산" + nearby_j
                            
                            raw_data = fetch_kepco_realtime(target_reg['metroCd'], target_reg['cityCd'], in_lidong.strip(), in_li.strip(), nearby_j)
                            if raw_data:
                                if offset == 0:
                                    st.success(f"💡 한전 DB에 '{in_jibun}'가 등록되지 않아, 부번을 제외한 대표 지번 **[{nearby_j}번지]** 기준으로 선로를 찾아냈습니다!")
                                else:
                                    st.success(f"💡 한전 DB에 해당 번지가 누락되어, 인접한 주변 지번 **[{nearby_j}번지]** 기준으로 연결 가능한 선로를 추적했습니다!")
                                break

                # 💡 3차: 반경 10개 인접 지번도 없으면 해당 동/리 전체 검색
                if not raw_data:
                    raw_data = fetch_kepco_realtime(target_reg['metroCd'], target_reg['cityCd'], in_lidong.strip(), in_li.strip(), "")
                    if raw_data and in_jibun:
                        st.info(f"💡 반경 10개 지번까지 탐색했으나 한전 DB에 미등록된 오지입니다. 대신 **'{in_lidong} {in_li}' 전체의 배전선로 데이터**를 모두 불러왔습니다.")

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
                else: st.warning("⚠️ 한전 실시간 API에 해당 지역의 데이터가 아예 존재하지 않습니다.")

st.markdown("---")

# ==========================================
# 🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱
# ==========================================
st.header("🗺️ 3. 카카오 x 브이월드 통합 경계 분석 앱")
st.markdown("데이터 차단을 완벽 우회하는 **'지적도 유리창(WMS) 기술'**을 적용했습니다. 내 땅 한가운데에 빨간 핀이 꽂히고 주변 지적도 선이 모두 선명하게 나타납니다.")

search_addr = st.text_input("🔍 지도 주소 직접 검색 (읍/면/동/리 또는 상세 번지)", placeholder="예: 김천시 아포읍 또는 예천군 호명읍 산합리 1123")

if search_addr:
    with st.spinner("카카오 엔진으로 주소를 짚어내고, 지적도를 유리창처럼 덧씌웁니다..."):
        lat_c, lon_c, kakao_log = get_kakao_coord(search_addr)
        
        if DEBUG_MODE:
            with st.expander("🔧 카카오 API 통신 기록 (디버그)", expanded=True):
                for label, status, body in kakao_log:
                    st.write(f"**{label}** — HTTP 상태: `{status}`")
                    st.code(body, language="json")
        
        if lat_c is not None:
            lat, lon = lat_c, lon_c
            has_jibun = bool(re.search(r'\d', search_addr))
            
            if has_jibun:
                st.success(f"✅ 카카오 주소 탐색 성공! 빨간 마커 위치가 해당 번지수입니다. (위도: {lat:.4f}, 경도: {lon:.4f})")
            else:
                st.success(f"✅ 카카오 주소 탐색 성공! 해당 행정구역 중심입니다. (위도: {lat:.4f}, 경도: {lon:.4f})")
                
            start_zoom = 19 if has_jibun else 15
            m = folium.Map(location=[lat, lon], zoom_start=start_zoom, max_zoom=22, tiles=None)

            # 1. 2D 위성지도 (HTTPS)
            folium.TileLayer(
                tiles=f'https://api.vworld.kr/req/wmts/1.0.0/{VWORLD_KEY}/Satellite/{{z}}/{{y}}/{{x}}.jpeg',
                attr='VWorld Satellite', name='🛰️ 브이월드 위성지도', overlay=False, control=True, show=True, max_zoom=22
            ).add_to(m)

            # 2. 도로명/지명 하이브리드 오버레이 (HTTPS)
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
                layer_name = "lt_c_adri_info" if "리" in search_addr.split()[-1] or "리 " in search_addr else "lt_c_ademd_info"
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

            folium.Marker([lat, lon], popup=f"<b>{search_addr}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)
            folium.LayerControl(position='topright').add_to(m)
            
            st_folium(m, width=1300, height=750, returned_objects=[], key=f"wms_map_{lat}_{lon}")
        else:
            st.error("⚠️ 카카오 엔진에서 주소를 찾지 못했습니다. 띄어쓰기 등을 확인해주세요.")
