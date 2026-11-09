const C = Object.assign({ korea: "#ff7a6b", japan: "#63aeff", kmap: "#c8283c", jmap: "#1860c0", muted: "#9bb8cd", text: "#d3e6f3", ink: "#7fc6e0", grid: "rgba(155,200,230,.14)", warn: "#f6b94e" }, window.THEME_C || {});
const KOREA_CITIES = [[37.57, 126.98], [35.18, 129.08], [35.16, 126.85], [33.50, 126.53], [37.75, 128.90], [36.35, 127.38]];
const CITY_NAMES = ["서울", "부산", "광주", "제주", "강릉", "대전"];
const JAPAN_CITIES = [[33.59, 130.40], [31.60, 130.56], [34.69, 135.50], [35.68, 139.69], [38.27, 140.87], [43.06, 141.35], [26.21, 127.68], [33.56, 133.53], [37.90, 139.00]];
const JAPAN_NAMES = ["후쿠오카", "가고시마", "오사카", "도쿄", "센다이", "삿포로", "나하", "고치", "니가타"];
const KMA_URL = "https://www.weather.go.kr/w/typhoon/report.do", JMA_URL = "https://www.jma.go.jp/bosai/map.html#contents=typhoon";
const WARN_P = .4;      // 경보 기준(typhoon/analyze.py THRESHOLD)
const STALE_H = 18;     // 마지막 갱신이 이보다 오래되면 경고. 자동 갱신이 몇 시간씩 늦기도 한다
const QUIET_H = 18;     // 새 관측이 이만큼 없을 때: 실시간 피드를 정상으로 받았으면 태풍 없음, 아니면 자료를 못 받은 것으로 알린다
const FEATURE_KO = {
  doy: "계절", month: "월", LAT: "위도", LON: "경도", wind: "최대풍속", pres: "중심기압", dwind_24: "하루 풍속 변화",
  dlat_6: "6시간 남북 이동", dlon_6: "6시간 동서 이동", dlat_24: "24시간 남북 이동", dlon_24: "24시간 동서 이동",
  speed_kmh: "이동 속도", heading: "진행 방향", age_h: "발생 후 시간",
  dist_seoul: "서울까지 거리", bearing_seoul: "서울 방위", dist_tokyo: "도쿄까지 거리", bearing_tokyo: "도쿄 방위",
  atm_u_steer: "태풍을 미는 동서 바람", atm_v_steer: "태풍을 미는 남북 바람", atm_u_steer_24: "미는 동서 바람의 하루 변화", atm_v_steer_24: "미는 남북 바람의 하루 변화",
  atm_dz_ew: "동서 기압 경도", atm_dz_n: "남북 기압 경도", atm_dz_max: "고기압 세기", atm_ridge_dlon: "고기압 서쪽 끝과의 거리", atm_ridge_dlat: "고기압 축까지 위도 차",
  atm_shear: "높이별 바람 차이", atm_z500: "500hPa 고도", atm_z500_n: "북쪽 500hPa 고도", atm_z500_e: "동쪽 500hPa 고도",
  atm_z500_w: "서쪽 500hPa 고도", atm_ridge_west: "북태평양 고기압 서쪽 끝", atm_ridge_lat: "고기압 축 위도", atm_z500_max: "고기압 세기",
  ocn_sst: "해수면 온도", ocn_sst_anom: "해수온 편차", ocn_sst_ahead: "북쪽 해수온", ocn_sst_ahead_diff: "북쪽 해수온 차이",
  ocn_oni: "엘니뇨 지수", ocn_pdo: "태평양 10년 진동", ocn_nino4: "니뇨4 편차",
};

const $ = id => document.getElementById(id);
const REDUCED = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
if (window.Chart) {
  Chart.defaults.color = C.muted;
  Chart.defaults.font.family = "'Pretendard Variable', Pretendard, system-ui, sans-serif";
  Chart.defaults.borderColor = C.grid;
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: "rgba(3,12,20,.97)", borderColor: "#35607f", borderWidth: 1, titleColor: "#fff", bodyColor: "#fff", padding: 10, cornerRadius: 3, boxPadding: 4 }, window.THEME_TIP || {});
  if (window.THEME_FONT) Chart.defaults.font.family = window.THEME_FONT;
}

const getJSON = p => fetch(p, { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(p); return r.json(); });
const soft = p => getJSON(p).catch(() => null);
const utc = t => new Date(t + ":00:00Z");
function kst(t, withYear = false) {
  const d = new Date(utc(t).getTime() + 9 * 36e5);
  const s = `${d.getUTCMonth() + 1}월 ${d.getUTCDate()}일 ${String(d.getUTCHours()).padStart(2, "0")}시`;
  return withYear ? `${d.getUTCFullYear()}년 ${s}` : s;
}
const pct = p => `${Math.round(p * 100)}%`;
const pctCard = p => p < .01 ? "1% 미만" : pct(p);
const has = v => v != null && !Number.isNaN(v);
const hrsFromNow = t => (utc(t).getTime() - Date.now()) / 36e5;
const addHours = (t, h) => new Date(utc(t).getTime() + h * 36e5).toISOString().slice(0, 13);
const agoText = h => h < 1 ? "방금" : h < 48 ? `${Math.round(h)}시간 전` : `${Math.round(h / 24)}일 전`;
const leftText = t => { const h = hrsFromNow(t); return h <= 0 ? "시각 지남" : h < 1 ? "1시간 내" : `${Math.round(h)}시간 후`; };
const earlyText = t => hrsFromNow(t) > 0 ? kst(t) : "지금";
const kn2ms = kt => Math.round(kt * .5144);
// 기상청 한글 표기(사이트에 나오는 이름만). 없는 이름은 영문 그대로
const KO_NAME = { CHABA: "차바", "CHAN-HOM": "찬홈", CHANTHU: "찬투", DANAS: "다나스", FRANCISCO: "프란시스코", GONI: "고니", HAGUPIT: "하구핏", HAISHEN: "하이선", HINNAMNOR: "힌남노", JANGMI: "장미", JONGDARI: "종다리", KHANUN: "카눈", "KONG-REY": "콩레이", LEEPI: "리피", LINGLING: "링링", MAYSAK: "마이삭", MERANTI: "므란티", MITAG: "미탁", NAMTHEUN: "남테운", OMAIS: "오마이스", PRAPIROON: "쁘라삐룬", PULASAN: "풀라산", SOUDELOR: "사우델로르", SOULIK: "솔릭", TAPAH: "타파", TRASES: "뜨라세", CHAMPI: "참피", "IN-FA": "인파", KOGUMA: "고구마", HALOLA: "할롤라", NANMADOL: "난마돌", BAVI: "바비", AERE: "에어리", SONGDA: "송다", SHANSHAN: "산산" };
const unnamed = s => s.name === "UNNAMED" || s.name === "NOT_NAMED";
const stormName = s => unnamed(s) ? "이름 없음" : KO_NAME[s.name] || s.name;
const stormFull = s => KO_NAME[s.name] ? `${KO_NAME[s.name]}(${s.name})` : stormName(s);
const debounce = (fn, ms = 200) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const tableHtml = (head, rows) => `<table><thead><tr>${head.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
const describeChart = (canvas, text) => { canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", text); };
const setBar = (el, p) => { if (el) el.style.transform = `scaleX(${Math.max(0, Math.min(1, p))})`; };

/* 화면에 들어왔을 때 한 번 실행 */
function seen(el, fn, th = .15) {
  if (!el) return;
  if (!("IntersectionObserver" in window)) { fn && fn(); return; }
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { io.disconnect(); fn && fn(); }
  }), { threshold: th });
  io.observe(el);
}

/* 강도 체계 (풍속 kt 기준, 없으면 JMA 등급). 33·44·54m/s(약 64·85·105kt)는 기상청 강·매우강·초강력 경계 */
const LV = [
  { name: "열대저압부", color: "#6b8299", ink: "#a9c0d4" },
  { name: "열대폭풍", color: "#2aa7d8", ink: "#6fd3f5" },
  { name: "강한 열대폭풍", color: "#f08a1c", ink: "#ffb35c" },
  { name: "강한 태풍", color: "#e5383b", ink: "#ff8a8d" },
  { name: "매우 강한 태풍", color: "#a4161a", ink: "#ff9a9e" },
  { name: "초강력 태풍", color: "#7b2cbf", ink: "#d3a6f7" },
];
const lvLabel = i => i ? `${i}단계` : LV[0].name;
const NO_LV = "#14213d";
const US_LINE = "#0b8a5f";
const OWN_LINE = "#c8187f";    // 자체 경로(GFS 중심 추적)
const KR_LINE = "#8a5a00";     // 한국 기상청 예보
function lvIndex(kt, grade) {
  if (kt != null) return kt < 34 ? 0 : kt < 48 ? 1 : kt < 64 ? 2 : kt < 85 ? 3 : kt < 105 ? 4 : 5;
  return grade === 3 ? 1 : grade === 4 ? 2 : grade === 5 ? 3 : 0;
}
const lvName = (kt, grade) => (kt == null && grade === 6) ? "온대저기압" : LV[lvIndex(kt, grade)].name;
// 풍속이 비어 있는 지점은 앞뒤 값으로 채움
function fillWind(track) {
  const w = track.map(t => t[3]);
  let last = null;
  const f = w.map(v => (v != null ? (last = v) : last));
  last = null;
  for (let i = w.length - 1; i >= 0; i--) { if (w[i] != null) last = w[i]; if (f[i] == null) f[i] = last; }
  return f;
}

/* 지난 태풍 자료(history.json) 공통 */
const refTime = x => x.arrival || x.closest.t;      // 한국 영향권 도착, 들어오지 않은 태풍은 최근접 시각
const peakOf = s => { const w = s.track.map(t => t[3]).filter(has); return w.length ? Math.max(...w) : null; };
// 도착(최근접) 전 처음 확률이 기준을 넘긴 시점까지 남은 시간(analyze.warning_lead 와 같은 정의)
const firstLead = s => {
  const ref = utc(refTime(s)), t = s.track.find(x => (x[5] || 0) >= WARN_P && utc(x[0]) < ref);
  return t ? (ref - utc(t[0])) / 36e5 : null;
};
const whyText = why => why.map(([g, v]) => `${g} ${v > 0 ? "+" : "−"}${Math.abs(v)}%p`).join(" · ");
const won = d => `${d.won.toLocaleString()}억원${d.with ? `(${d.with} 합산)` : ""}`;

/* ---------- 지도 부품 ---------- */
const DOT = 1.6;
function ensurePanes(map) {
  if (map._tcPanes) return;
  map.createPane("tcCone").style.zIndex = 380;
  map.createPane("tcCasing").style.zIndex = 395;
  map._tcPanes = true;
}

// 지도마다 범례 하나(오른쪽 아래, 좁은 화면은 접힘), 확대 버튼은 오른쪽 위
function baseMap(id, center = [30, 130], zoom = 4, { forecast = false } = {}) {
  const legendOpen = forecast && window.innerWidth > 860;      // 지난 태풍 지도는 접어 두어 경로를 가리지 않게
  const map = L.map(id, { zoomControl: false, scrollWheelZoom: false, worldCopyJump: true, dragging: !L.Browser.mobile }).setView(center, zoom);
  L.control.zoom({ position: "topright" }).addTo(map);
  map.attributionControl.setPrefix("Leaflet");
  const base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
  L.tileLayer(base + "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "&copy; Esri", maxZoom: 10 }).addTo(map);
  ensurePanes(map);
  const lg = L.control({ position: "bottomright" });
  lg.onAdd = () => {
    const d = L.DomUtil.create("details", "tc-legend");
    d.open = legendOpen;
    d.innerHTML = `<summary>범례</summary>` + LV.map((l, i) => `<div><i style="background:${l.color}"></i>${i ? `${i}단계 ` : ""}${l.name}</div>`).join("") + `<div><i style="background:${NO_LV}"></i>강도 기록 없음</div>`
      + (forecast ? `<div class="sep"><i class="lg-line"></i>일본 기상청 예보</div><div><i class="lg-cone"></i>일본 예보 오차 범위(70%)</div><div><i class="lg-line us"></i>미국 JTWC 예보</div><div><i class="lg-line kr"></i>한국 기상청 예보</div><div><i class="lg-line own"></i>자체 경로(GFS 추적)</div>` : "");
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  lg.addTo(map);
  map.on("zoomend moveend", debounce(() => declutter(map), 120));
  return map;
}

// 라벨이 겹치면 덜 중요한 쪽을 숨긴다. 태풍 이름표는 항상 보이게 아래로 비키고,
// 그다음 고른 태풍의 시각 > 주요 도시 > 나머지 도시 > 예보 시각 > 날짜 > 소멸 태풍 순서
const LABEL_ORDER = [".tc-fc.hot span", ".tc-date.hot span", ".tc-city.pri span", ".tc-city span", ".tc-fc span", ".tc-date span", ".tc-end .tc-label"];
function declutter(map) {
  const el = map.getContainer();
  const box = r => r.width && r.height;
  const hits = (r, p) => r.left < p.right && r.right > p.left && r.top < p.bottom && r.bottom > p.top;
  const down = (r, dy) => ({ left: r.left, right: r.right, top: r.top + dy, bottom: r.bottom + dy });
  const faded = e => el.classList.contains("focus") && e.closest(".tc-p") && !e.closest(".hot");     // 흐려진 태풍은 자리를 차지하지 않음
  const placed = [];
  el.querySelectorAll(".tc-box .tc-label").forEach(e => {
    e.style.marginTop = "";
    const r = e.getBoundingClientRect();
    if (!box(r) || faded(e)) return;
    let dy = 0;
    while (dy < 120 && placed.some(p => hits(down(r, dy), p))) dy += r.height + 2;
    if (dy) e.style.marginTop = `${dy}px`;
    placed.push(down(r, dy));
  });
  const done = new Set();
  LABEL_ORDER.forEach(sel => el.querySelectorAll(sel).forEach(e => {
    if (done.has(e)) return;
    done.add(e);
    e.style.visibility = "";
    const r = e.getBoundingClientRect();
    if (!box(r) || faded(e)) return;
    if (placed.some(p => hits(r, p))) e.style.visibility = "hidden";
    else placed.push(r);
  }));
}

// 한국 6개·일본 9개 도시
function drawCities(map) {
  const pri = new Set(["서울", "부산", "제주", "도쿄", "오사카", "후쿠오카", "나하", "삿포로"]);
  const draw = (cities, names, cls) => cities.forEach((c, i) => {
    L.marker(c, { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-city ${cls}${pri.has(names[i]) ? " pri" : ""}`, iconSize: [0, 0], html: `<i></i><span>${names[i]}</span>` }) }).addTo(map);
  });
  draw(JAPAN_CITIES, JAPAN_NAMES, "jp");
  draw(KOREA_CITIES, CITY_NAMES, "kr");
}

// 태풍 아이콘: 구름띠 4+4가닥 + 고정된 눈
const BLADE = "M50 50C44 34 54 18 80 16C66 26 62 40 50 50Z";
const TC_SVG = `<svg class="tc-svg" viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="46" fill="currentColor" fill-opacity=".13"/><g class="tc-arms">`
  + [45, 135, 225, 315].map(a => `<path d="${BLADE}" transform="rotate(${a} 50 50) translate(6 6) scale(.88)" fill="currentColor" fill-opacity=".32"/>`).join("")
  + [0, 90, 180, 270].map(a => `<path d="${BLADE}" transform="rotate(${a} 50 50)" fill="currentColor" fill-opacity=".62" stroke="#fff" stroke-opacity=".75" stroke-width="1"/>`).join("")
  + `</g><circle cx="50" cy="50" r="9" fill="#fff" stroke="currentColor" stroke-width="3"/></svg>`;

function tcIcon({ size = 38, idx = 3, live = true, label = "", cls = "" } = {}) {
  const color = LV[idx].color;
  return L.divIcon({
    className: `tc-icon ${cls}`, iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    html: `<div class="tc-box" style="color:${color}">${live ? '<span class="tc-pulse"></span><span class="tc-pulse b"></span>' : ""}${TC_SVG}${label ? `<div class="tc-label">${label}</div>` : ""}</div>`,
  });
}
const iconSize = idx => 32 + idx * 4;
const chipLabel = (name, kt, hpa) => `${name}${kt != null || hpa != null ? `<small>${[kt != null ? kt + "kt" : "", hpa != null ? hpa + "hPa" : ""].filter(Boolean).join(" · ")}</small>` : ""}`;

// 경로: 강도 색, 흰 외곽선, 6시간 점, 00UTC 큰 점 + 날짜. 반환 { segs } 로 투명도 제어 가능
function drawTrack(layer, track, { grade = null, animate = true, weight = 3.5, cls = "", dates = true, opacityOf = null, done = null } = {}) {
  const w = fillWind(track), segs = [];
  const col = i => w[i] == null && grade == null ? NO_LV : LV[lvIndex(w[i], grade)].color;      // 강도 기록이 없으면 회색 지도에서도 보이는 색
  const items = [];
  if (track.length === 1) {
    items.push(() => L.circleMarker([track[0][1], track[0][2]], { radius: 4, color: col(0), weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls }).addTo(layer));
  }
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1], b = track[i], k = i - 1, ll = [[a[1], a[2]], [b[1], b[2]]];
    const op = opacityOf ? () => opacityOf(k) : () => .95;
    items.push(() => {
      L.polyline(ll, { pane: "tcCasing", color: "#fff", weight: weight + 3, opacity: .9, lineCap: "round", className: cls, interactive: false }).addTo(layer);
      const seg = L.polyline(ll, { color: col(i), weight, opacity: op(), lineCap: "round", lineJoin: "round", className: cls, interactive: false }).addTo(layer);
      segs.push(seg);
      const day = b[0].endsWith("T00");
      L.circleMarker(ll[1], day
        ? { radius: 4.2, color: col(i), weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls, interactive: false }
        : { radius: DOT, stroke: false, fillColor: "#fff", fillOpacity: .95, className: cls, interactive: false }).addTo(layer);
      if (day && dates) {
        L.marker(ll[1], { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-date ${cls}`, iconSize: [40, 14], iconAnchor: [-5, 16], html: `<span>${+b[0].slice(5, 7)}/${+b[0].slice(8, 10)}</span>` }) }).addTo(layer);
      }
    });
  }
  const restyle = () => segs.forEach((sg, k) => sg.setStyle({ opacity: opacityOf ? opacityOf(k) : .95 }));
  let dead = false;
  if (!animate || REDUCED) { items.forEach(f => f()); done && done(); }
  else {
    // 앞부분은 빠르게, 뒤로 갈수록 천천히
    let n = 0;
    const step = () => {
      if (dead) return;
      if (n < items.length) { items[n++](); setTimeout(step, 6 + 30 * (n / items.length)); }
      else if (done) done();
    };
    step();
  }
  return { restyle, cancel: () => { dead = true; } };
}

// 예보 경로 + 불확실성 원뿔(전용 pane, 합쳐서 보이게)
function destPoint(p, brg, km) {
  const R = 6371, d = km / R, b = brg * Math.PI / 180, l1 = p[0] * Math.PI / 180, g1 = p[1] * Math.PI / 180;
  const l2 = Math.asin(Math.sin(l1) * Math.cos(d) + Math.cos(l1) * Math.sin(d) * Math.cos(b));
  const g2 = g1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(l1), Math.cos(d) - Math.sin(l1) * Math.sin(l2));
  return [l2 * 180 / Math.PI, g2 * 180 / Math.PI];
}
function hull(pts) {
  pts = pts.slice().sort((a, b) => a.x - b.x || a.y - b.y);
  const cr = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lo = [], up = [];
  for (const p of pts) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], p) <= 0) lo.pop(); lo.push(p); }
  for (const p of pts.slice().reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], p) <= 0) up.pop(); up.push(p); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}
const ring = (c, km) => km ? [...Array(36)].map((_, i) => destPoint(c, i * 10, km)) : [c];
function drawForecast(map, layer, s, last, color, cls) {
  const fc = s.forecast;
  if (!fc || !fc.points || !fc.points.length) return { circles: 0, hulls: 0, points: [] };
  const MERC = L.Projection.SphericalMercator;
  const pts = fc.points;
  const path = [[last[1], last[2]], ...pts.map(p => [p[1], p[2]])];
  L.polyline(path, { color, weight: 2.5, opacity: .9, dashArray: "2 7", lineCap: "round", className: cls, interactive: false }).addTo(layer);
  let circles = 0, hulls = 0, prev = { c: [last[1], last[2]], km: 0 };
  pts.forEach(p => {
    const c = [p[1], p[2]], km = p[3];
    if (km) {
      L.circle(c, { pane: "tcCone", radius: km * 1000, stroke: false, fillColor: color, fillOpacity: 1, className: cls + " tc-cone", interactive: false }).addTo(layer);
      circles++;
      const proj = [...ring(prev.c, prev.km), ...ring(c, km)].map(ll => { const q = MERC.project(L.latLng(ll[0], ll[1])); return { x: q.x, y: q.y }; });
      const poly = hull(proj).map(q => MERC.unproject(L.point(q.x, q.y)));
      L.polygon(poly, { pane: "tcCone", stroke: false, fillColor: color, fillOpacity: 1, className: cls + " tc-cone", interactive: false }).addTo(layer);
      hulls++;
    }
    prev = { c, km: km || 0 };
    L.circleMarker(c, { radius: 4.5, color, weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls, interactive: false }).addTo(layer);
    L.marker(c, { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-fc ${cls}`, iconSize: [0, 0], html: `<span style="color:${color}">+${p[0]}h</span>` }) }).addTo(layer);
  });
  return { circles, hulls, points: path };
}

/* ---------- 차트 부품 ---------- */
// 왼쪽에서 오른쪽으로 한 번 그려지는 효과(선 차트)
const wipePlugin = {
  id: "wipe",
  beforeInit(c) { c.$p = 0; },
  beforeDatasetsDraw(c) {
    const p = c.$p ?? 1;
    if (p >= 1) return;
    const a = c.chartArea;
    c.ctx.save(); c.ctx.beginPath();
    c.ctx.rect(a.left - 12, a.top - 12, (a.width + 24) * p, a.height + 24);
    c.ctx.clip(); c.$clipped = true;
  },
  afterDatasetsDraw(c) { if (c.$clipped) { c.ctx.restore(); c.$clipped = false; } },
};
function runWipe(chart, dur = 1800) {
  if (REDUCED) { chart.$p = 1; chart.draw(); return; }
  const t0 = performance.now();
  const tick = now => {
    const k = Math.min(1, (now - t0) / dur);
    chart.$p = 1 - Math.pow(1 - k, 3);
    chart.draw();
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
// 선 끝에 직접 이름 붙이기(겹치면 위아래로 벌림)
const endLabels = {
  id: "endLabels",
  afterDatasetsDraw(c) {
    const p = c.$p ?? 1, alpha = Math.max(0, Math.min(1, (p - .78) / .22));
    if (alpha <= 0) return;
    const a = c.chartArea, ctx = c.ctx, items = [];
    c.data.datasets.forEach((ds, i) => {
      const meta = c.getDatasetMeta(i), pt = meta.data[meta.data.length - 1];
      if (!pt || meta.hidden || !ds.label) return;
      items.push({ ds, x: pt.x, y: pt.y, ly: pt.y });
    });
    items.sort((u, v) => u.y - v.y);
    for (let i = 1; i < items.length; i++) if (items[i].ly - items[i - 1].ly < 16) items[i].ly = items[i - 1].ly + 16;
    if (items.length && items[items.length - 1].ly > a.bottom) {      // 아래로 넘치면 위로 밀어 올림
      items[items.length - 1].ly = a.bottom;
      for (let i = items.length - 2; i >= 0; i--) if (items[i + 1].ly - items[i].ly < 16) items[i].ly = items[i + 1].ly - 16;
    }
    ctx.save(); ctx.globalAlpha = alpha; ctx.textBaseline = "middle";
    ctx.font = `700 12.5px ${Chart.defaults.font.family}`;
    items.forEach(it => {
      ctx.strokeStyle = it.ds.borderColor; ctx.fillStyle = it.ds.borderColor; ctx.lineWidth = 1;
      if (Math.abs(it.ly - it.y) > 1) { ctx.beginPath(); ctx.moveTo(it.x + 3, it.y); ctx.lineTo(a.right + 8, it.ly); ctx.stroke(); }
      ctx.fillText(it.ds.label, a.right + 12, it.ly);
    });
    ctx.restore();
  },
};

/* ---------- 현재 태풍 ---------- */
function renderNow(latest) {
  const map = baseMap("nowMap", [27, 134], 4, { forecast: true });
  const mapEl = $("nowMap");
  drawCities(map);
  const list = $("nowList"), goneList = $("goneList");
  list.innerHTML = "";
  const liveBounds = [];
  const wide = () => window.innerWidth > 860;
  const pad = () => ({ paddingTopLeft: [wide() ? (window.PAD_L ?? 420) : 64, 44], paddingBottomRight: [wide() ? 120 : 64, 44], maxZoom: 5 });
  const cards = [];
  let selected = null, view = null, allView = null;

  const highlight = n => {
    mapEl.classList.toggle("focus", n != null);
    mapEl.querySelectorAll(".tc-p").forEach(e => e.classList.toggle("hot", n != null && e.classList.contains("tc-s" + n)));
  };
  const fit = (b, o = {}) => {
    view = b;
    if (REDUCED || document.hidden) map.fitBounds(b, pad());      // 배경 탭은 애니메이션 프레임이 빠져 표식이 어긋날 수 있다
    else map.flyToBounds(b, { ...pad(), duration: 1.1, ...o });
  };
  const stormBounds = (s, live) => {
    const pts = s.track.map(t => [t[1], t[2]]);
    if (!live) return L.latLngBounds(pts);
    return L.latLngBounds([...pts, ...[...(s.forecast?.points || []), ...(s.forecast_us?.points || []), ...(s.track_own?.points || []), ...(s.forecast_kr?.points || [])].map(p => [p[1], p[2]]), ...KOREA_CITIES, ...JAPAN_CITIES]);
  };
  const scrollToEl = (e, off = document.querySelector(".top").offsetHeight) => window.scrollTo({ top: e.getBoundingClientRect().top + scrollY - off, behavior: REDUCED ? "auto" : "smooth" });
  const toMap = () => { if (!wide()) scrollToEl(mapEl); };
  $("toMap").addEventListener("click", toMap);
  const markCards = () => cards.forEach((c, i) => { c.classList.toggle("sel", i === selected); c.setAttribute("aria-pressed", i === selected); });
  const select = (n, s, live, scroll = true) => {
    selected = n;
    highlight(n);
    markCards();
    fit(stormBounds(s, live));
    if (scroll) toMap();
  };
  const reset = () => {
    selected = null;
    highlight(null);
    markCards();
    if (allView) fit(allView);
    else { view = null; map.flyTo([27, 134], 4); }
  };
  map.on("click", () => { if (selected != null) reset(); });
  map.on("resize", debounce(() => { if (view) map.fitBounds(view, pad()); }, 250));
  const allBtn = L.control({ position: "topright" });
  allBtn.onAdd = () => {
    const d = L.DomUtil.create("div", "map-btns");
    d.innerHTML = `<button type="button" class="map-all">전체 보기</button><button type="button" class="map-all back">목록 보기</button>`;
    L.DomEvent.disableClickPropagation(d);
    L.DomEvent.on(d.firstChild, "click", reset);
    L.DomEvent.on(d.lastChild, "click", () => scrollToEl(selected != null ? cards[selected] : $("nowPanel")));
    return d;
  };
  allBtn.addTo(map);
  const bind = (el, n, s, live) => {
    cards[n] = el;
    el.setAttribute("role", "button");
    el.setAttribute("aria-pressed", "false");
    el.addEventListener("click", () => select(n, s, live));
    el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n, s, live); } });
    ["mouseenter", "focus"].forEach(ev => el.addEventListener(ev, () => highlight(n)));
    ["mouseleave", "blur"].forEach(ev => el.addEventListener(ev, () => highlight(selected)));
  };

  // 카드의 나라별 줄
  const whenLines = (v, s, label) => {
    if (!v.range) return `<div class="eta"><span class="who">${label} 자체 예측</span> ${kst(v.eta)}쯤 · <span data-left="${v.eta}"></span></div>`;
    // eta 는 시 단위로 버리고 range 는 반올림이라 1시간 어긋날 수 있어 eta 를 감싸게 맞춘다
    const lo = [addHours(s.end, v.range[0]), v.eta].sort()[0], hi = [addHours(s.end, v.range[1]), v.eta].sort()[1];
    return `<div class="eta"><span class="who">${label} 자체 예측</span> ${kst(v.eta)} · <span data-left="${v.eta}"></span></div>`
      + `<div class="eta">범위 <span data-early="${lo}"></span> ~ ${kst(hi)}</div>`;
  };
  // 기관 예보 진로가 도시 200km 안에 들어오는지: 중심 진입 > 오차 범위 닿음 > 최단 거리
  const agencyCell = (fc, c, v) => {
    const a = fc?.approach?.[c];
    if (!a) return "<td>–</td>";
    const km = a.min_km != null ? `${a.near || ""} ${a.min_km.toLocaleString()}km`.trim() : "";
    const txt = a.center ? `${kst(a.center)} 진입` : a.cone ? `오차 범위 ${kst(a.cone)}` : `최단 ${km}`;
    // 자체 모델은 5일 기준이라, 기관 예보 기간 밖의 진입은 다르다고 보지 않는다
    const span = Math.max(...fc.points.map(p => p[0]));
    const differs = !v.arrived && ((v.p >= .2 && v.hours <= span && !a.center && !a.cone) || (v.p < .2 && a.center));
    return `<td${differs ? ' class="differ"' : ""}>${txt}${differs ? " · 다름" : ""}</td>`;
  };
  const compareTable = s => {
    const row = (who, cell) => `<tr><th scope="row">${who}</th>${["korea", "japan"].map(cell).join("")}</tr>`;
    const agency = (name, fc) => row(`${name}${fc ? `<small>${kst(fc.issued).replace(/^\d+월 /, "")} 발표 · ${Math.max(...fc.points.map(p => p[0]))}h</small>` : ""}`, c => agencyCell(fc, c, s[c]));
    return `<div class="cmp-cap"><span>주요 도시 200km 진입 예측</span><a href="${KMA_URL}" target="_blank" rel="noopener">공식 예보: 기상청</a></div><table class="cmp"><thead><tr><th></th><th scope="col" class="k-t">한국</th><th scope="col" class="j-t">일본</th></tr></thead><tbody>`
      + row("자체 모델<small>5일 내 확률</small>", c => `<td class="self">${s[c].arrived ? `${kst(s[c].arrived)} 진입` : pctCard(s[c].p)}</td>`)
      + (s.track_own ? row(`자체 경로<small>GFS ${kst(s.track_own.cycle).replace(/^\d+월 /, "")} 발표 추적${s.track_own.turn48 != null ? ` · 48h 방향 ${s.track_own.turn48 > 0 ? "+" : ""}${s.track_own.turn48}°` : ""}${s.track_own.ens ? ` · 전향 2일 내 ${s.track_own.ens.p48 != null ? pct(s.track_own.ens.p48) : "–"}, 2~4일 ${s.track_own.ens.p96 != null ? pct(s.track_own.ens.p96) : "–"}(앙상블 ${s.track_own.ens.members})` : ""}</small>`, c => agencyCell(s.track_own, c, s[c])) : "")
      + agency("한국 기상청", s.forecast_kr) + agency("일본 기상청", s.forecast) + agency("미국 JTWC", s.forecast_us)
      + "</tbody></table>";
  };
  const details = (s, c, label) => {
    const v = s[c];
    if (v.arrived) return "";
    return (v.p >= .2 ? whenLines(v, s, label) : "") + (v.why?.length ? `<div class="eta"><span class="who">${label} 요인별 영향(그 요인이 없을 때와 비교)</span> ${whyText(v.why)}</div>` : "");
  };

  // data_until 은 마지막 관측 시각이라 태풍이 없으면 멈춰 있다. 신선도는 갱신 시각(generated)으로 따진다
  const generatedAt = new Date(`${latest.generated}:00Z`).getTime();
  const longGap = (generatedAt - utc(latest.data_until).getTime()) / 36e5 >= QUIET_H;
  // 피드는 받았어도 전문을 못 읽었을 수 있다. JTWC가 활동 중이라고 하면 태풍 없음으로 처리하지 않는다
  const usActive = latest.us_active || [];
  const quiet = longGap && latest.feed_ok === true && !latest.storms.some(s => s.end === latest.data_until && usActive.includes(s.name.toUpperCase()));
  const noFeed = longGap && !quiet;
  let liveN = 0, goneN = 0;
  const liveList = [];
  const SRC = { own: L.layerGroup().addTo(map), jma: L.layerGroup().addTo(map), kr: L.layerGroup().addTo(map), us: L.layerGroup().addTo(map) }, srcSeen = new Set();
  const goneDraws = [], goneIdx = new Set();
  latest.storms.forEach((s, n) => {
    const live = s.end === latest.data_until && !quiet;
    if (!live && unnamed(s)) return;      // 이름 없이 끝난 약한 시스템은 지난 태풍 목록과 같이 뺀다
    const cls = `tc-p tc-s${n}${live ? "" : " tc-gone"}`;
    const layer = L.layerGroup();
    const last = s.track[s.track.length - 1];
    const w = fillWind(s.track);
    const kt = last[3] ?? null, hpa = last[4] ?? null;
    const idx = lvIndex(kt ?? (live ? w[w.length - 1] : null), s.grade);
    const li = document.createElement("li");
    li.tabIndex = 0;
    if (live) {
      layer.addTo(map);
      drawTrack(layer, s.track, { grade: s.grade, cls, dates: true, done: () => declutter(map) });
      liveBounds.push(...s.track.map(t => [t[1], t[2]]));
      L.marker([last[1], last[2]], { title: `${stormName(s)} 현재 위치`, alt: `${stormName(s)} 현재 위치`, icon: tcIcon({ size: iconSize(idx), idx, live: true, label: chipLabel(stormName(s), kt, hpa), cls }), zIndexOffset: 1000 })
        .on("click", () => { select(n, s, true, false); li.scrollIntoView({ block: "nearest" }); })
        .addTo(layer);
      // 예보 경로는 기관별 묶음에 그려 지도 왼쪽 위 체크박스로 켜고 끈다
      const color = C.jmap;
      if (s.forecast?.points?.length) srcSeen.add("jma");
      liveBounds.push(...drawForecast(map, SRC.jma, s, last, color, cls).points);
      if (s.forecast_us) {
        srcSeen.add("us");
        const us = [[last[1], last[2]], ...s.forecast_us.points.map(p => [p[1], p[2]])];
        L.polyline(us, { color: US_LINE, weight: 2.5, opacity: .9, dashArray: "8 6", className: cls, interactive: false }).addTo(SRC.us);
        us.slice(1).forEach(c => L.circleMarker(c, { radius: 3.5, color: US_LINE, weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls, interactive: false }).addTo(SRC.us));
        liveBounds.push(...us);
      }
      if (s.forecast_kr) {
        srcSeen.add("kr");
        const kr = [[last[1], last[2]], ...s.forecast_kr.points.map(p => [p[1], p[2]])];
        L.polyline(kr, { color: KR_LINE, weight: 2.5, opacity: .9, dashArray: "2 5 8 5", className: cls, interactive: false }).addTo(SRC.kr);
        kr.slice(1).forEach(c => L.circleMarker(c, { radius: 3.5, color: KR_LINE, weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls, interactive: false }).addTo(SRC.kr));
        liveBounds.push(...kr);
      }
      if (s.track_own) {
        srcSeen.add("own");
        const own = [[last[1], last[2]], ...s.track_own.points.map(p => [p[1], p[2]])];
        L.polyline(own, { color: OWN_LINE, weight: 2.5, opacity: .9, className: cls, interactive: false }).addTo(SRC.own);
        own.slice(1).forEach(c => L.circleMarker(c, { radius: 3.5, color: OWN_LINE, weight: 2, fillColor: "#fff", fillOpacity: 1, className: cls, interactive: false }).addTo(SRC.own));
        liveBounds.push(...own);
      }
      const L1 = LV[idx];
      li.style.animationDelay = `${liveN * 90}ms`;
      li.innerHTML = `<div class="s-head"><b>${stormFull(s)}</b>${kt != null || s.grade != null ? `<span class="lv" style="--c:${L1.color};--ci:${L1.ink}">${lvName(kt, s.grade)}</span>` : ""}</div>`
        + (kt != null || hpa != null ? `<div class="vit">${kt != null ? `풍속 <b>${kt}</b>kt(${kn2ms(kt)}m/s)${s.wind_usa ? " 미국 환산" : ""}` : ""}${kt != null && hpa != null ? " · " : ""}${hpa != null ? `기압 <b>${hpa}</b>hPa` : ""}</div>` : "")
        + compareTable(s) + details(s, "korea", "한국") + details(s, "japan", "일본")
        + `${s.env ? "" : '<div class="src weak">상공 자료 없음</div>'}`;
      list.appendChild(li); liveN++; liveList.push(s);
    } else {
      // 소멸한 태풍은 '최근 소멸'을 펼쳤을 때만 지도에 그린다(활동 중인 것처럼 보이지 않게)
      goneDraws.push(Object.assign(() => {
        layer.addTo(map);
        drawTrack(layer, s.track, { grade: s.grade, cls, dates: false, animate: false });
        L.marker([last[1], last[2]], { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-icon ${cls}`, iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="tc-end" style="color:${LV[idx].color}"><i></i><div class="tc-label">${stormName(s)}</div></div>` }) }).addTo(layer);
        return layer;
      }, { sid: s.sid }));
      li.dataset.sid = s.sid;
      const hit = [s.korea.arrived ? `한국 200km 진입 ${kst(s.korea.arrived)}` : "", s.japan.arrived ? `일본 200km 진입 ${kst(s.japan.arrived)}` : ""].filter(Boolean).join(" · ");
      li.innerHTML = `<b>${stormFull(s)}</b><span>마지막 관측 ${kst(s.end)}</span>${hit ? `<em>${hit}</em>` : ""}`;
      goneList.appendChild(li); goneN++;
      goneIdx.add(n);
    }
    bind(li, n, s, live);
  });
  if (srcSeen.size) {
    const NAMES = { own: ["자체 경로", OWN_LINE, "solid"], jma: ["일본 기상청", C.jmap, "dotted"], kr: ["한국 기상청", KR_LINE, "dashed"], us: ["미국 JTWC", US_LINE, "dashed"] };
    const ctl = L.control({ position: "topleft" });
    ctl.onAdd = () => {
      const d = L.DomUtil.create("div", "src-toggle");
      d.innerHTML = Object.keys(NAMES).filter(k => srcSeen.has(k)).map(k => `<label><input type="checkbox" data-k="${k}" checked><i style="border-top:3px ${NAMES[k][2]} ${NAMES[k][1]}"></i>${NAMES[k][0]}</label>`).join("");
      L.DomEvent.disableClickPropagation(d);
      d.addEventListener("change", e => { const g = SRC[e.target.dataset.k]; if (e.target.checked) g.addTo(map); else g.remove(); declutter(map); });
      return d;
    };
    ctl.addTo(map);
  }
  if (window.KEYFIGS) window.KEYFIGS(latest, liveList, { stormName, stormFull, pctCard, kst, lvName, lvIndex, LV });
  if (!liveN) list.innerHTML = `<li class="empty" style="cursor:default;animation:none">활동 중인 태풍 없음</li>`;
  if (goneN) {
    let goneLayers = null;
    $("goneBox").hidden = false;
    $("goneN").textContent = `(${goneN})`;
    $("goneBox").addEventListener("toggle", e => {
      if (e.target.open) { if (goneLayers) goneLayers.forEach(l => l.addTo(map)); else goneLayers = goneDraws.map(f => f()); }
      else { (goneLayers || []).forEach(l => l.remove()); if (selected != null && goneIdx.has(selected)) reset(); }
      declutter(map);
    });
  }

  // 남은 시간과 자료 나이는 지금 시각 기준으로 계속 다시 계산한다
  let lastNote = null;
  const tick = () => {
    const age = (Date.now() - generatedAt) / 36e5, stale = age >= STALE_H || noFeed, note = $("staleNote");
    const obs = agoText(-hrsFromNow(latest.data_until)), link = `<a href="${KMA_URL}" target="_blank" rel="noopener">기상청 태풍 정보</a>`;
    const noteHtml = !stale ? "" : age >= STALE_H ? `마지막 갱신 ${agoText(age)} · ${link}`
      : `실시간 자료 끊김, 마지막 관측 ${obs} · ${link}`;
    if (noteHtml !== lastNote) note.innerHTML = lastNote = noteHtml;       // 내용이 그대로면 초점과 낭독을 흔들지 않는다
    $("asof").textContent = `${kst(latest.data_until)} 자료 · ${agoText(age)} 갱신`;
    $("nowPanel").classList.toggle("stale", stale);
    note.hidden = !stale;
    list.querySelectorAll("[data-left]").forEach(e => { e.textContent = leftText(e.dataset.left); });
    list.querySelectorAll("[data-early]").forEach(e => { e.textContent = earlyText(e.dataset.early); });
  };
  tick();
  setInterval(tick, 6e4);

  // 첫 로드: 넓게 보다가 활동 중인 태풍 쪽으로 한 번 부드럽게 이동
  if (liveBounds.length) {
    allView = L.latLngBounds([...liveBounds, ...KOREA_CITIES]);
    view = allView;
    if (REDUCED || document.hidden) map.fitBounds(allView, pad());
    else setTimeout(() => map.flyToBounds(allView, { ...pad(), duration: 2.4, easeLinearity: .2 }), 700);
  }
  setTimeout(() => declutter(map), 1500);

  // 활동 중인 태풍이 없을 때: 가장 최근 근접 태풍의 결과
  const showLast = (s, damage) => {
    mapEl.querySelectorAll(".tc-legend .sep, .tc-legend .sep ~ div").forEach(e => e.remove());      // 예보 범례는 필요 없음
    const dup = goneDraws.findIndex(f => f.sid === s.sid);       // 소멸 목록과 경로가 겹치지 않게 카드 쪽만 남긴다
    if (dup >= 0) {
      goneDraws.splice(dup, 1);
      goneList.querySelector(`[data-sid="${s.sid}"]`)?.remove();
      $("goneN").textContent = `(${goneList.children.length})`;
      $("goneBox").hidden = !goneList.children.length;
    }
    const peak = peakOf(s), lv = peak == null ? null : lvIndex(peak), lead = firstLead(s), dmg = damage[s.sid];
    const li = document.createElement("li");
    li.className = "last-card";
    li.innerHTML = `<span class="who">최근 한국 근접 태풍</span>`
      + `<div class="s-head"><b>${stormFull(s)}</b>${lv != null ? `<span class="lv" style="--c:${LV[lv].color};--ci:${LV[lv].ink}">${lvLabel(lv)}</span>` : `<span class="lv" style="--c:${NO_LV};--ci:#9bb8cd">강도 기록 없음</span>`}</div>`
      + `<div class="vit">${s.season}년 ${s.arrival ? `${kst(s.arrival)} 한국 200km 진입` : `${kst(s.closest.t)} 한국 도시까지 최단 ${s.closest.km}km`}</div>`
      + `<div class="vit">${lead == null ? "한국 도달 예측 없음" : `${s.arrival ? "한국 첫 도달 예측" : "한국 도달 예측(빗나감)"} ${lead}시간 전`}${dmg ? ` · 재산피해 ${won(dmg)}` : ""}</div>`
      + `<a class="last-link" href="#history">지난 태풍에서 보기</a>`;
    list.appendChild(li);
    const layer = L.layerGroup().addTo(map), last = s.track[s.track.length - 1];
    drawTrack(layer, s.track, { cls: "tc-p", dates: true, done: () => declutter(map) });
    L.marker([last[1], last[2]], { interactive: false, keyboard: false, icon: L.divIcon({ className: "tc-icon tc-p", iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="tc-end" style="color:${LV[lv ?? 0].color}"><i></i><div class="tc-label">${stormName(s)}</div></div>` }) }).addTo(layer);
    allView = L.latLngBounds([...s.track.map(t => [t[1], t[2]]), ...KOREA_CITIES]);
    fit(allView);
  };
  return { idle: !liveN, showLast };
}

/* ---------- 자체 모델 vs 기상청 ---------- */
function renderCompare(stats, kma) {
  const a = stats.analysis, m = stats.metrics, full = m.full || {};
  const lead = c => (a.model_warning_lead_full || {})[c] || a.model_warning_lead[c];
  const kmaF = kma && kma.korea_lead && kma.korea_lead.since_2015_same_as_model && kma.korea_lead.since_2015_same_as_model.first_fcst_track_in_korea;

  // 경보 리드타임 차트 (선에 직접 이름)
  const leads = [24, 48, 72, 96, 120];
  const line = (label, src, color, o = {}) => ({
    label, data: leads.map(h => src[`warned_${h}h_before`] * 100), borderColor: color, backgroundColor: color,
    tension: .3, borderWidth: 2.4, pointRadius: 3, pointHoverRadius: 5, fill: false, ...o,
  });
  const sets = [line("자체 모델", lead("korea"), C.korea, { borderWidth: 3 })];
  if (kmaF) sets.unshift(line("기상청", kmaF, C.warn, { borderDash: [2, 5], pointStyle: "rectRot", pointRadius: 4 }));
  seen($("leadChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
    const cfg = window.LEAD_CHART ? window.LEAD_CHART({ sets, leads, C }) : null;
    if (cfg) { new Chart($("leadChart"), cfg); return; }
    const ch = new Chart($("leadChart"), {
      type: "line", plugins: [wipePlugin, endLabels],
      data: { labels: leads.map(h => `${h}시간 전`), datasets: sets },
      options: {
        maintainAspectRatio: false, animation: false, layout: { padding: { right: 108, top: 6 } },
        interaction: { mode: "index", intersect: false },
        scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
      },
    });
    runWipe(ch);
  }, .25);
  const leadRows = sets.map(d => [d.label, ...d.data.map(v => `${Math.round(v)}%`)]);
  $("leadTable").innerHTML = tableHtml(["구분", ...leads.map(h => `${h}시간 전`)], leadRows);
  describeChart($("leadChart"), `200km 진입 전 도달 예측 비율 그래프. ${leadRows.map(r => `${r[0]} 48시간 전 ${r[2]}`).join(", ")}. 자세한 값은 아래 표에 있습니다.`);

  // 지표 표. 경보 기준이 달라 값만으로 우열을 가리지 않도록 기준과 미집계 항목을 함께 둔다
  const me = lead("korea"), x = full.korea || m.korea, k = kmaF || {};
  const cell = (v, u = "") => has(v) ? v + u : "–";
  const fa = (a.model_false_alarm || {}).korea;
  const ks = (kma && kma.korea_lead && kma.korea_lead.since_2015_same_as_model && kma.korea_lead.since_2015_same_as_model.scores) || {};
  $("metrics").innerHTML = tableHtml(["2015년~", "자체 모델", "기상청"], [
    ["도달 예측 기준", `확률 ${pct(WARN_P)} 이상`, "예보 경로 200km 진입"],
    ["첫 도달 예측(중앙값)", cell(me.median_warning_lead_h, "시간 전"), cell(k.median_warning_lead_h, "시간 전")],
    ["빗나간 도달 예측 비율", fa && has(fa.false_alarm_ratio) ? `${pct(fa.false_alarm_ratio)}(${fa.warned_storms}개 중)` : "–",
      has(ks.false_alarm_ratio) ? `${pct(ks.false_alarm_ratio)}(${ks.warned_storms}개 중)` : "미집계"],
    ["진입 시각 오차(평균)", cell(x.arrival_mae_h, "시간"),
      has(ks.entry_mae_h) ? `${ks.entry_mae_h}시간(진입 예측한 발표 ${ks.entry_issues}건)` : "미집계"],
    ["평가한 태풍 수", cell(me.storms, "개"), cell(k.storms, "개")],
  ]);

  // 요인: 가는 막대 표
  const fi = a.feature_importance;
  const keys = [...new Set([...Object.keys(fi.korea), ...Object.keys(fi.japan)])]
    .sort((x, y) => (fi.korea[y] || 0) - (fi.korea[x] || 0)).slice(0, 6);
  const mx = Math.max(...keys.map(k => Math.max(fi.korea[k] || 0, fi.japan[k] || 0))) || 1;
  $("factors").innerHTML = `<div class="fac-head"><span style="flex:1">자체 모델 주요 요인</span><span>한국</span><span>일본</span></div>`
    + keys.map(k => `<div class="fac"><span>${FEATURE_KO[k] || "기타 요인"}</span><div class="bars"><i class="bar korea"><u data-p="${(fi.korea[k] || 0) / mx}" style="background:${C.korea}"></u></i><i class="bar japan"><u data-p="${(fi.japan[k] || 0) / mx}" style="background:${C.japan}"></u></i></div></div>`).join("");
  seen($("factors"), () => $("factors").querySelectorAll(".bar u").forEach(u => setBar(u, +u.dataset.p)), .2);
}

/* ---------- 지난 태풍 ---------- */
let histOpen = null;      // 다른 섹션(전향 복기)에서 태풍 하나를 지난 태풍 화면으로 열 때
function renderHistory(history, damage, review = {}) {
  const list = $("histList"), scrub = $("scrub"), play = $("play");
  // 단계는 경로 중 최대풍속 기준(LV의 번호: 5단계 = 초강력 태풍)
  const items = history.map((s, i) => {
    const peak = peakOf(s);
    return { s, i, peak, lv: peak == null ? null : lvIndex(peak), dmg: damage[s.sid] || null };
  });
  const map = baseMap("histMap", [30, 128], 4);
  drawCities(map);
  const layer = L.layerGroup().addTo(map);
  const cursor = L.marker([0, 0], { icon: tcIcon({ size: 40, idx: 3, live: false, label: "&nbsp;" }), zIndexOffset: 1000, interactive: false });
  let tr = null;
  let chart, cur, timer;

  const cursorLine = {
    id: "cursor",
    afterDatasetsDraw(c) {
      const { ctx, chartArea: ar, scales: { x } } = c;
      const draw = (v, color, dash) => {
        const px = x.getPixelForValue(v);
        ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.setLineDash(dash);
        ctx.beginPath(); ctx.moveTo(px, ar.top); ctx.lineTo(px, ar.bottom); ctx.stroke(); ctx.restore();
      };
      draw(0, window.THEME_REFLINE || "rgba(255,122,107,.75)", [4, 4]);
      if (cur) draw(hoursTo(cur.track[+scrub.value]), C.ink, []);
      const y = c.scales.y.getPixelForValue(WARN_P * 100);       // 도달 예측 기준선
      ctx.save(); ctx.strokeStyle = C.warn; ctx.lineWidth = 1; ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(ar.left, y); ctx.lineTo(ar.right, y); ctx.stroke();
      ctx.setLineDash([]); ctx.fillStyle = C.warn; ctx.font = `600 11px ${Chart.defaults.font.family}`; ctx.fillText(pct(WARN_P), ar.left + 4, y - 4);
      ctx.restore();
    },
  };
  const refName = () => cur.arrival ? "200km 진입" : "최단 거리";
  const hoursTo = t => (utc(t[0]) - utc(refTime(cur))) / 36e5;

  // 복기: 실제 전향 시작과 누가 언제 처음 전향을 예측했는지(typhoon/review.py)
  function turnLine(r) {
    if (!r || !r.turn) return "";
    const who = (name, v) => v === "none" ? null : `${name} ${v ? `${kst(v.t)}(${v.lead_h}시간 전)` : "예측 없음"}`;
    const parts = [who("기상청", r.kma), who("자체 경로", r.own), who("자체 분류(48시간)", r.clf)].filter(Boolean);
    return `<span class="h-why">전향 시작 ${kst(r.turn)} · 첫 전향 예측: ${parts.join(" · ")}</span>`;
  }

  function summary() {
    const lead = firstLead(cur), first = lead != null;
    const it = items[history.indexOf(cur)];
    return `<b>${cur.season} ${stormFull(cur)}</b>`
      + (it.lv != null ? ` · ${it.lv ? `${lvLabel(it.lv)}(${LV[it.lv].name}, ` : `${lvLabel(0)}(`}최대 ${it.peak}kt${cur.wind_usa ? ", 미국 풍속 환산" : ""})` : "")
      + (it.dmg ? ` · 재산피해 ${won(it.dmg)}` : "")
      + (cur.arrival ? ` · 한국 200km 진입 ${kst(cur.arrival)} · ${first ? `한국 첫 도달 예측 ${lead}시간 전` : "한국 도달 예측 없음"}`
        : ` · 한국 도시까지 최단 ${cur.closest.km}km(${kst(cur.closest.t)}) · ${first ? `한국 도달 예측(빗나감) ${lead}시간 전` : "한국 도달 예측 없음"}`)
      + (cur.arrival_japan ? ` · 일본 200km 진입 ${kst(cur.arrival_japan)}` : "")
      + turnLine(review[cur.sid])
      + (cur.peak ? `<span class="h-why">한국 최고 확률 ${pct(cur.peak.p)}(${kst(cur.peak.t)})${cur.peak.why?.length ? ` · 요인별 영향(그 요인이 없을 때와 비교): ${whyText(cur.peak.why)}` : ""}</span>` : "");
  }

  function setFrame(i) {
    const t = cur.track[i];
    cursor.setLatLng([t[1], t[2]]);
    const ce = cursor.getElement();
    if (ce) {
      const wv = fillWind(cur.track)[i], k = wv != null ? Math.round(wv) : null;
      ce.firstChild.style.color = LV[lvIndex(wv, null)].color;
      const lb = ce.querySelector(".tc-label");
      if (lb) lb.innerHTML = chipLabel(kst(t[0]).replace(/^\d+월 /, ""), k, t[4] != null ? Math.round(t[4]) : null);
    }
    tr && tr.restyle();
    $("scrubTime").textContent = `${kst(t[0])} · 한국 ${pct(t[5] || 0)} · 일본 ${pct(t[6] || 0)}`;
    chart && chart.update("none");
  }

  function markList() {
    list.querySelectorAll("button").forEach(b => { const on = history[+b.dataset.i] === cur; b.classList.toggle("sel", on); b.setAttribute("aria-pressed", on); });
  }

  // 겹쳐 볼 태풍(다른 태풍 하나를 점선 경로·확률선으로 같이 그림)
  const slug = s => `${s.season}-${stormName(s)}`;
  const cmpLayer = L.layerGroup().addTo(map), allLayer = L.layerGroup();
  let cmpI = null, allOn = false, shown = [];
  const hoursFor = (s, t) => (utc(t[0]) - utc(refTime(s))) / 36e5;
  function buildData() {
    const pts = c => cur.track.map(t => ({ x: hoursTo(t), y: (t[c] || 0) * 100 }));
    const data = {
      datasets: [
        { label: "한국", data: pts(5), borderColor: C.korea, backgroundColor: C.korea + "1f", fill: true },
        { label: "일본", data: pts(6), borderColor: C.japan, backgroundColor: "transparent" },
      ].map(d => ({ ...d, tension: window.THEME_TENSION ?? .3, pointRadius: 0, borderWidth: 2.4 })),
    };
    if (window.HIST_DATA) window.HIST_DATA(data, C);
    const o = cmpI != null && history[cmpI] !== cur ? history[cmpI] : null;
    if (o) data.datasets.push({ label: `${stormName(o)} 한국`, data: o.track.map(t => ({ x: hoursFor(o, t), y: (t[5] || 0) * 100 })), borderColor: C.muted, backgroundColor: "transparent", borderDash: [6, 4], borderWidth: 1.6, pointRadius: 0, fill: false, tension: window.THEME_TENSION ?? .3 });
    return data;
  }
  function drawCmp() {
    cmpLayer.clearLayers();
    const o = cmpI != null && history[cmpI] !== cur ? history[cmpI] : null;
    if (!o || allOn) return;
    const ll = o.track.map(t => [t[1], t[2]]);
    L.polyline(ll, { color: C.ink, weight: 2.5, opacity: .7, dashArray: "6 6", interactive: false }).addTo(cmpLayer);
    L.marker(ll[ll.length - 1], { interactive: false, keyboard: false, icon: L.divIcon({ className: "tc-icon", iconSize: [14, 14], iconAnchor: [7, 7], html: `<div class="tc-end" style="color:${C.ink}"><i></i><div class="tc-label">${o.season} ${stormName(o)}</div></div>` }) }).addTo(cmpLayer);
    map.flyToBounds([...cur.track.map(t => [t[1], t[2]]), ...ll], { padding: [40, 40], duration: .8 });
  }
  // 전체 경로: 지금 목록에 있는 태풍을 한 지도에 모두. 경로를 누르면 그 태풍으로
  function drawAll() {
    allLayer.clearLayers();
    shown.forEach(r => {
      const ll = r.s.track.map(t => [t[1], t[2]]);
      L.polyline(ll, { color: r.lv != null ? LV[r.lv].color : NO_LV, weight: 2.2, opacity: .7 })
        .bindTooltip(`${r.s.season} ${stormName(r.s)}`, { sticky: true, className: "all-tip" })
        .on("click", () => load(r.i))
        .addTo(allLayer);
    });
    const pts = shown.flatMap(r => r.s.track.map(t => [t[1], t[2]]));
    if (pts.length) map.flyToBounds(pts, { padding: [30, 30], duration: .8 });
  }
  function setAll(on, quiet) {
    allOn = on;
    $("histAll")?.setAttribute("aria-pressed", on);
    $("history").classList.toggle("all-on", on);
    if (on) { stop(); layer.remove(); cmpLayer.remove(); allLayer.addTo(map); drawAll(); }
    else { allLayer.remove(); layer.addTo(map); cmpLayer.addTo(map); if (!quiet && cur) map.flyToBounds(cur.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: .8 }); }
  }

  function load(i) {
    stop();
    if (allOn) setAll(false, true);
    cur = history[i];
    tr && tr.cancel();
    layer.clearLayers();
    tr = drawTrack(layer, cur.track, { weight: 4, cls: "tc-hist", opacityOf: k => k < +scrub.value ? 1 : .25, done: () => declutter(map) });
    cursor.addTo(layer);
    map.flyToBounds(cur.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: .8 });
    scrub.max = cur.track.length - 1;
    scrub.value = 0;
    $("histSummary").innerHTML = summary();
    markList();
    const sample = cur.track.filter((_, k) => k % 2 === 0);
    $("histTable").innerHTML = tableHtml(["시각", `한국 ${refName()}까지`, "한국", "일본"], sample.map(t => [kst(t[0]), hoursTo(t) < 0 ? `${-hoursTo(t)}시간 전` : hoursTo(t) === 0 ? refName() : `${refName()} ${hoursTo(t)}시간 뒤`, pct(t[5] || 0), pct(t[6] || 0)]));
    describeChart($("histChart"), `${cur.season} ${stormName(cur)}의 시간별 도달 확률 그래프. 자세한 값은 아래 표에 있습니다.`);
    const data = buildData();
    drawCmp();
    if (chart) { chart.data = data; chart.options.scales.x.title.text = `한국 ${refName()} 기준`; chart.update(); }
    else if (window.Chart) chart = new Chart($("histChart"), {
      type: "line", data, plugins: [cursorLine, endLabels],
      options: {
        maintainAspectRatio: false, animation: { duration: REDUCED ? 0 : 1200 }, interaction: { mode: "nearest", axis: "x", intersect: false },
        layout: { padding: { right: 40 } },
        onClick(e, _, c) {                 // 누른 곳과 가장 가까운 시점으로 이동
          const x = c.scales.x.getValueForPixel(e.x);
          let best = 0;
          cur.track.forEach((t, k) => { if (Math.abs(hoursTo(t) - x) < Math.abs(hoursTo(cur.track[best]) - x)) best = k; });
          stop();
          scrub.value = best;
          setFrame(best);
        },
        scales: {
          x: { type: "linear", bounds: "data", title: { display: true, text: `한국 ${refName()} 기준` }, afterBuildTicks: a => { a.ticks = []; for (let v = Math.ceil(a.min / 24) * 24; v <= a.max; v += 24) a.ticks.push({ value: v }); }, ticks: { callback: v => v === 0 ? "0" : `${v > 0 ? "+" : ""}${v}h`, maxRotation: 0, autoSkip: true } },
          y: { min: 0, max: 100, ticks: { callback: v => v + "%" } },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: i => i[0].parsed.x < 0 ? `${refName()} ${-i[0].parsed.x}시간 전` : i[0].parsed.x === 0 ? refName() : `${refName()} ${i[0].parsed.x}시간 뒤`, label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
      },
    });
    setFrame(0);
  }

  function stop() { clearInterval(timer); timer = null; play.textContent = "▶"; play.setAttribute("aria-label", "재생"); }
  play.addEventListener("click", () => {
    if (timer) return stop();
    if (+scrub.value >= +scrub.max) scrub.value = 0;
    play.textContent = "❚❚";
    play.setAttribute("aria-label", "일시정지");
    timer = setInterval(() => {
      if (+scrub.value >= +scrub.max) return stop();
      scrub.value = +scrub.value + 1;
      setFrame(+scrub.value);
    }, 140);
  });
  scrub.addEventListener("input", () => { stop(); setFrame(+scrub.value); });

  // 목록: 연도·단계로 거르고 최신·피해·강도 순으로 정렬
  const f = { year: "", lv: "", sort: "new", q: "" };
  // 주소 #history/2026-참피 로 바로 열기
  const fromHash = () => { const m = decodeURIComponent(location.hash).match(/^#history\/(.+)$/); return m ? history.findIndex(s => slug(s) === m[1] || stormName(s) === m[1]) : -1; };
  let want = fromHash();
  // 위쪽 섹션이 늦게 채워지며 자리가 밀리므로 그린 뒤에 한 번 더 맞춘다
  // (브라우저가 없는 앵커를 찾다가 첫 화면으로 되돌리므로 페이지 load 뒤에도 맞춘다)
  const goHist = () => {
    const go = () => $("history").scrollIntoView({ behavior: "instant" });
    go(); [300, 1200].forEach(ms => setTimeout(go, ms));
    if (document.readyState !== "complete") addEventListener("load", () => setTimeout(go, 50), { once: true });
  };
  const SORT = { new: (a, b) => a.i - b.i, dmg: (a, b) => (b.dmg?.won ?? -1) - (a.dmg?.won ?? -1) || a.i - b.i, peak: (a, b) => (b.peak ?? -1) - (a.peak ?? -1) || a.i - b.i };
  let mapSeen = false;
  function refresh() {
    const q = f.q.trim().toLowerCase();
    const rows = items.filter(r => (!f.year || r.s.season === +f.year) && (f.lv === "" || r.lv === +f.lv)
      && (!q || stormName(r.s).toLowerCase().includes(q) || r.s.name.toLowerCase().includes(q))).sort(SORT[f.sort]);
    shown = rows;
    if (allOn) drawAll();
    list.innerHTML = rows.length ? rows.map(r => {
      const L1 = r.lv != null ? LV[r.lv] : null;
      const hurt = r.dmg ? won(r.dmg) : f.sort === "dmg" ? "피해 자료 없음" : "";
      return `<li><button type="button" data-i="${r.i}"><span class="h-top"><b>${stormName(r.s)}</b>${L1 ? `<span class="lv" style="--c:${L1.color};--ci:${L1.ink}">${lvLabel(r.lv)}</span>` : `<span class="lv" style="--c:${NO_LV};--ci:#9bb8cd">강도 기록 없음</span>`}</span><span class="h-sub">${r.s.season}년 ${+refTime(r.s).slice(5, 7)}월 · ${r.s.arrival ? "200km 진입" : `도시까지 최단 ${r.s.closest.km}km`}${hurt ? ` · ${hurt}` : ""}</span></button></li>`;
    }).join("") : `<li class="h-none">해당 태풍 없음</li>`;
    if (want >= 0) { mapSeen = true; load(want); want = -1; goHist(); }      // 주소로 고른 태풍이 먼저(지도는 이미 크기가 있어 바로 그린다)
    else if (rows.some(r => r.s === cur)) markList();
    else if (rows.length && mapSeen && !allOn) load(rows[0].i);      // 무거운 그리기는 지도가 보일 때 한다
  }
  list.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    load(+b.dataset.i);
    window.history.replaceState(null, "", `#history/${encodeURIComponent(slug(cur))}`);      // 이 함수 안의 history 는 태풍 배열
    if (window.innerWidth <= 860) $("histSummary").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" });
  });
  $("histYear").innerHTML = `<option value="">전체 연도</option>` + [...new Set(history.map(s => s.season))].sort((a, b) => b - a).map(y => `<option value="${y}">${y}년</option>`).join("");
  $("histYear").addEventListener("change", e => { f.year = e.target.value; refresh(); });
  const group = (id, key, opts) => {
    const box = $(id);
    box.innerHTML = opts.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${f[key] === v}">${t}</button>`).join("");
    box.addEventListener("click", e => {
      const b = e.target.closest("button");
      if (!b) return;
      f[key] = b.dataset.v;
      box.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", x === b));
      refresh();
    });
  };
  group("histLv", "lv", [["", "전체"], ...[5, 4, 3, 2, 1, ...(items.some(r => r.lv === 0) ? [0] : [])].map(n => [String(n), lvLabel(n)])]);
  group("histSort", "sort", [["new", "최신순"], ["dmg", "피해 큰 순"], ["peak", "강한 순"]]);
  $("histQ")?.addEventListener("input", e => { f.q = e.target.value; refresh(); });
  $("histAll")?.addEventListener("click", () => setAll(!allOn));
  const cmpSel = $("histCmp");
  if (cmpSel) {
    cmpSel.innerHTML = `<option value="">겹쳐 볼 태풍</option>` + items.slice().sort((a, b) => b.s.season - a.s.season || a.i - b.i).map(r => `<option value="${r.i}">${r.s.season} ${stormName(r.s)}</option>`).join("");
    cmpSel.addEventListener("change", e => {
      cmpI = e.target.value === "" ? null : +e.target.value;
      if (!cur) return;
      if (allOn) setAll(false, true);
      drawCmp();
      if (chart) { chart.data = buildData(); chart.update(); }
      if (cmpI == null) map.flyToBounds(cur.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: .8 });
    });
  }
  $("histLink")?.addEventListener("click", e => {
    if (!cur) return;
    const url = `${location.origin}${location.pathname}#history/${encodeURIComponent(slug(cur))}`;
    window.history.replaceState(null, "", url);
    const b = e.currentTarget, t = b.textContent;
    (navigator.clipboard ? navigator.clipboard.writeText(url) : Promise.reject()).then(() => { b.textContent = "복사됨"; }, () => { b.textContent = "주소창에 표시됨"; })
      .finally(() => setTimeout(() => { b.textContent = t; }, 1600));
  });
  histOpen = sid => {
    const i = history.findIndex(s => s.sid === sid);
    if (i < 0) return;
    mapSeen = true;
    load(i);
    $("history").scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
    window.history.replaceState(null, "", `#history/${encodeURIComponent(slug(history[i]))}`);
  };
  addEventListener("hashchange", () => { const i = fromHash(); if (i >= 0) histOpen(history[i].sid); });
  refresh();
  seen($("histMap"), () => { mapSeen = true; refresh(); }, .1);
}

/* ---------- 전향 복기 ---------- */
// 태풍마다 실제 전향 시작 120시간 전부터 시작까지, 누가 언제 처음 전향을 예측했는지(typhoon/review.py)
const TURN_SRC = [["kma", "기상청", "sq"], ["own", "자체 경로", "dot"], ["clf", "자체 분류", "tri"]];
function renderReview(review, history) {
  const bySid = Object.fromEntries(history.map(s => [s.sid, s]));
  const rows = Object.entries(review).filter(([sid, r]) => r.turn && bySid[sid]).map(([sid, r]) => ({ s: bySid[sid], r }))
    .sort((a, b) => b.r.turn.localeCompare(a.r.turn));
  const median = a => { if (!a.length) return null; const b = a.slice().sort((x, y) => x - y), m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  $("turnStats").innerHTML = TURN_SRC.map(([k, name, mk]) => {
    const have = rows.filter(x => x.r[k] !== "none" && x.r[k] !== undefined), hit = have.filter(x => x.r[k] && x.r[k].lead_h != null);
    const med = median(hit.map(x => x.r[k].lead_h));
    return `<div class="ts"><span><i class="mk ${mk}"></i>${name}</span><b>${hit.length}<small>/${have.length}</small></b><em>${med == null ? "–" : `${Math.round(med)}시간 전`}</em></div>`;
  }).join("");
  const SPAN = 120, x = h => `${(1 - Math.min(h, SPAN) / SPAN) * 100}%`;
  $("turnAxis").innerHTML = [120, 96, 72, 48, 24, 0].map(h => `<span style="left:${x(h)}">${h ? `${h / 24}일 전` : "전향"}</span>`).join("");
  $("turnList").innerHTML = rows.map(({ s, r }) => {
    const marks = TURN_SRC.map(([k, name, mk]) => {
      const v = r[k];
      if (v === "none" || v === undefined) return "";
      if (!v) return `<i class="mk ${mk} miss" style="left:100%" title="${name}: 예측 없음"></i>`;
      return `<i class="mk ${mk}" style="left:${x(v.lead_h)}" title="${name}: ${kst(v.t)} (${v.lead_h}시간 전)"></i>`;
    }).join("");
    return `<li><button type="button" data-sid="${s.sid}"><b>${stormName(s)}</b><span>${s.season}</span></button><div class="tl">${marks}</div><span class="tt">${kst(r.turn)}</span></li>`;
  }).join("");
  $("turnList").addEventListener("click", e => { const b = e.target.closest("button[data-sid]"); if (b && histOpen) histOpen(b.dataset.sid); });
}

/* ---------- 모델 성능 ---------- */
function renderModel(stats) {
  const m = stats.metrics, a = stats.analysis;
  const barOpts = (extra = {}) => ({ maintainAspectRatio: false, animation: false, plugins: { legend: { display: true, position: "top", align: "end", labels: { boxWidth: 10, boxHeight: 10 } } }, ...extra });
  let country = "korea", charts = [];
  const draw = () => {
    const path = m[country], env = (m.full || {})[country] || path;
    const lead = c => (a.model_warning_lead_full || {})[c] || a.model_warning_lead[c] || {};
    const fig = (name, v, sub) => `<div class="mf"><span>${name}</span><b>${v}</b><em>${sub}</em></div>`;
    $("modelFigs").innerHTML = fig("AUC", env.auc.toFixed(3), `경로 모델 ${path.auc.toFixed(3)}`)
      + fig("도착 시각 오차", `±${env.arrival_mae_h}<i>시간</i>`, `경로 모델 ±${path.arrival_mae_h}시간`)
      + fig("80% 범위 적중", `${Math.round(env.arrival_range80_coverage * 100)}<i>%</i>`, `범위 폭 ${Math.round(env.arrival_range80_median_width_h)}시간`)
      + fig("첫 도달 예측", `${lead(country).median_warning_lead_h ?? "–"}<i>시간 전</i>`, `검증 태풍 ${env.test_storms_hit}개`);
    charts.forEach(c => c.destroy());
    charts = [];
    if (!window.Chart) return;
    const vals = { 보정전: [path.brier_uncalibrated, env.brier_uncalibrated], 보정후: [path.brier, env.brier] };
    const lab = { id: "lab", afterDatasetsDraw(c) { const ctx = c.ctx; ctx.save(); ctx.font = `700 13px ${Chart.defaults.font.family}`; ctx.textAlign = "center"; c.data.datasets.forEach((d, di) => c.getDatasetMeta(di).data.forEach((b, i) => { ctx.fillStyle = d.backgroundColor; ctx.fillText(d.fmt(d.data[i]), b.x, b.y - 6); })); ctx.restore(); } };
    charts.push(new Chart($("brierChart"), {
      type: "bar", plugins: [lab],
      data: { labels: ["경로 모델", "환경 모델"], datasets: [
        { label: "보정 전", data: vals.보정전, backgroundColor: C.muted, fmt: v => v.toFixed(4), barPercentage: .7, categoryPercentage: .55 },
        { label: "보정 후", data: vals.보정후, backgroundColor: C.korea, fmt: v => v.toFixed(4), barPercentage: .7, categoryPercentage: .55 }] },
      options: barOpts({ layout: { padding: { top: 20 } }, scales: { y: { beginAtZero: true, grace: "15%", ticks: { maxTicksLimit: 4 } }, x: { grid: { display: false } } } }),
    }));
    const target = { id: "target", afterDatasetsDraw(c) { const { ctx, chartArea: ar, scales: { x } } = c, px = x.getPixelForValue(80); ctx.save(); ctx.strokeStyle = C.ink; ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(px, ar.top - 6); ctx.lineTo(px, ar.bottom); ctx.stroke(); ctx.setLineDash([]); ctx.fillStyle = C.ink; ctx.font = `600 12px ${Chart.defaults.font.family}`; ctx.textAlign = "center"; ctx.fillText("목표 80%", px, ar.top - 10); ctx.restore(); } };
    const labH = { id: "labH", afterDatasetsDraw(c) { const ctx = c.ctx; ctx.save(); ctx.font = `700 14px ${Chart.defaults.font.family}`; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillStyle = C.korea; c.getDatasetMeta(0).data.forEach((b, i) => ctx.fillText(`${Math.round(c.data.datasets[0].data[i])}%`, b.x + 8, b.y)); ctx.restore(); } };
    charts.push(new Chart($("coverChart"), {
      type: "bar", plugins: [target, labH],
      data: { labels: ["경로 모델", "환경 모델"], datasets: [{ label: "적중", data: [path.arrival_range80_coverage * 100, env.arrival_range80_coverage * 100], backgroundColor: C.korea, fmt: v => `${Math.round(v)}%`, barThickness: 26 }] },
      options: { indexAxis: "y", maintainAspectRatio: false, animation: false, layout: { padding: { top: 24, right: 40 } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${Math.round(c.parsed.x)}%` } } },
        scales: { x: { min: 50, max: 100, ticks: { callback: v => v + "%", stepSize: 10 } }, y: { grid: { display: false } } } },
    }));
    // 가로 막대는 값 라벨을 막대 끝에
    charts[1].data.datasets[0].fmt = v => `${Math.round(v)}%`;
  };
  const tabs = $("modelTabs");
  tabs.innerHTML = [["korea", "한국"], ["japan", "일본"]].map(([k, t]) => `<button type="button" data-k="${k}" aria-pressed="${k === country}">${t}</button>`).join("");
  tabs.addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; country = b.dataset.k; tabs.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", x === b)); draw(); });
  seen($("model"), draw, .05);
}

/* ---------- 경로 예측 ---------- */
function renderTrack(t) {
  const hrs = t.base.map(r => `${r.h}h`);
  const line = (label, data, color, o = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 3.5, pointHoverRadius: 5, tension: 0, ...o });
  const opts = (y, extra = {}) => ({ maintainAspectRatio: false, animation: false, layout: { padding: { right: 96, top: 8 } }, interaction: { mode: "index", intersect: false },
    scales: { y, x: { grid: { display: false } } }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.formattedValue}${y.unit || ""}` } } }, ...extra });
  const km = { beginAtZero: true, unit: "km", ticks: { callback: v => `${v.toLocaleString()}km`, maxTicksLimit: 5 } };
  seen($("track"), () => {
    if (!window.Chart) return;
    new Chart($("trackChart"), { type: "line", plugins: [endLabels], data: { labels: hrs, datasets: [
      line("단순 연장", t.base.map(r => r.extrap), C.muted, { borderDash: [5, 4] }),
      line("자체 모델", t.base.map(r => r.model), C.korea, { borderWidth: 2.6 })] }, options: opts(km) });
    new Chart($("gfsChart"), { type: "line", plugins: [endLabels], data: { labels: t.gfs.map(r => `${r.h}h`), datasets: [
      line("기존 모델", t.gfs.map(r => r.model), C.muted, { borderDash: [5, 4] }),
      line("GFS 결합", t.gfs.map(r => r.blend), C.korea, { borderWidth: 2.6 })] }, options: opts(km) });
    const ref = { id: "ref", afterDatasetsDraw(c) { const { ctx, chartArea: ar, scales: { y } } = c, py = y.getPixelForValue(50); ctx.save(); ctx.strokeStyle = C.grid.replace(/[\d.]+\)$/, ".35)"); ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(ar.left, py); ctx.lineTo(ar.right, py); ctx.stroke(); ctx.restore(); } };
    new Chart($("champiChart"), { type: "line", plugins: [ref, endLabels], data: { labels: t.champi.map(r => r.kst), datasets: [
      line("기존 분류", t.champi.map(r => r.clf * 100), C.muted, { borderDash: [5, 4], pointRadius: 0 }),
      line("GFS 결합", t.champi.map(r => r.blend * 100), C.korea, { borderWidth: 2.6, pointRadius: 0 })] },
      options: opts({ min: 0, max: 100, unit: "%", ticks: { callback: v => v + "%", stepSize: 50 } }, { scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%", stepSize: 50 } }, x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 7 } } } }) });
  }, .05);
  $("turnAuc").innerHTML = `<table class="auc"><thead><tr><th>방법</th><th>AUC</th><th>맞힘</th><th>예측 중 실제</th></tr></thead><tbody>`
    + t.turn.map((r, i) => `<tr${i === t.turn.length - 1 ? ' class="best"' : ""}><td>${r.name}</td><td><b>${r.auc.toFixed(3)}</b><i class="aucbar" style="--w:${(r.auc - .5) / .5 * 100}%"></i></td><td>${pct(r.hit)}</td><td>${pct(r.precision)}</td></tr>`).join("")
    + `</tbody></table>`;
}

/* ---------- 시작 ---------- */
document.querySelectorAll(".sec-head").forEach(el => seen(el, () => el.classList.add("in"), .9));

const FAIL_HTML = `자료를 불러오지 못했습니다. <button type="button" class="retry">다시 시도</button> <a href="${KMA_URL}" target="_blank" rel="noopener">기상청 태풍 정보</a>`;
document.addEventListener("click", e => { if (e.target.classList && e.target.classList.contains("retry")) location.reload(); });
function unavailable(id) {
  const w = document.querySelector(`#${id} .wrap`);
  if (!w) return;
  [...w.children].filter(c => !c.classList.contains("sec-head")).forEach(c => c.remove());
  w.insertAdjacentHTML("beforeend", `<p class="empty">${FAIL_HTML}</p>`);
}
function guard(id, data, fn) {
  try {
    if (!data) throw new Error("no data");
    fn(data);
  } catch (e) {
    console.warn(id, e);
    if (id === "now") {
      $("nowList").innerHTML = `<li class="empty" style="cursor:default;animation:none">${FAIL_HTML}</li>`;
      if (!$("nowMap").children.length) $("nowMap").innerHTML = `<p class="map-empty">자료가 없어 지도를 그리지 못했습니다</p>`;
    }
    else unavailable(id);
  }
}

// 자료마다 따로 그린다
const nowReady = soft("data/latest.json").then(latest => {
  let now = null;
  guard("now", latest && latest.storms && latest, l => { now = renderNow(l); });
  return now;
});
Promise.all(["history", "damage", "review"].map(n => soft(`data/${n}.json`))).then(([history, damage, review]) => {
  const dmg = (damage && damage.storms) || {};
  guard("history", history && history.length && history, h => renderHistory(h, dmg, review || {}));
  guard("review", review && history && history.length && review, r => renderReview(r, history));
  if (history && history.length) nowReady.then(now => { if (now && now.idle) now.showLast(history[0], dmg); });
});
Promise.all(["stats", "kma_lead"].map(n => soft(`data/${n}.json`))).then(([stats, kma]) => {
  guard("compare", stats && stats.analysis && stats, s => renderCompare(s, kma));
  guard("model", stats && stats.metrics && stats, renderModel);
});
soft("data/track.json").then(t => guard("track", t && t.base && t, renderTrack));
// 메뉴에 지금 보는 섹션 표시
if ("IntersectionObserver" in window) {
  const links = [...document.querySelectorAll(".top nav a")];
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    links.forEach(a => {
      const on = a.getAttribute("href") === `#${e.target.id}`;
      if (on) a.setAttribute("aria-current", "true");
      else a.removeAttribute("aria-current");
    });
  }), { rootMargin: "-45% 0px -50% 0px" });
  document.querySelectorAll("main > section").forEach(sec => io.observe(sec));
}
