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
VWORLD_KEY = "013A53E5-52AB-4FD2-AC9D-B7D4A85D5667"
KEPCO_KEY = "L5uHvUC6Mm5zy3sbEza5J690Lq82eaNdBHH11K2o"
KAKAO_KEY = "b5908f52b46962e646bf12c6f70a364d" 
DOMAIN = "https://kepcoapp-biwrxqmgtjrbamcm48ikmr.streamlit.app"

# 💡 디버그 모드 (끄라고 하실 때까지 유지합니다!)
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
# 🗺️ 3부: 카카오 좌표 변환 모듈 (방화벽 영향 없음)
# ==========================================
def get_kakao_coord(address):
    """해외 차단 없는 카카오로 '위도/경도'만 뽑아냅니다."""
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

    # 최대 여유용량 순 정렬
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
# 🗺️ 3. 브이월드 정밀 지도 (스트림릿 영구 에러 차단형 WMS 모드)
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

            # 💡 3. 핵심기술: 에러나던 GeoJSON 데이터를 버리고, WMS 투명 유리창 타일로 지적도 선을 그려냅니다!
            # (이 방식은 선생님의 PC가 이미지를 직수입하므로 국토부 방화벽에 막히지 않습니다)
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
                # 번지수가 없으면 행정구역(읍/면/동/리) 유리창을 씌웁니다.
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

            # 해당 위치 한가운데 정확히 핀 꽂기
            folium.Marker([lat, lon], popup=f"<b>{search_addr}</b>", icon=folium.Icon(color='red', icon='info-sign')).add_to(m)
            folium.LayerControl(position='topright').add_to(m)
            
            st_folium(m, width=1300, height=750, returned_objects=[], key=f"wms_map_{lat}_{lon}")
        else:
            st.error("⚠️ 카카오 엔진에서 주소를 찾지 못했습니다. 띄어쓰기 등을 확인해주세요.")
