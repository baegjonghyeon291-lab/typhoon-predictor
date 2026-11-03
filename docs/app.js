const C = { korea: "#ff7a6b", japan: "#63aeff", kmap: "#c8283c", jmap: "#1860c0", muted: "#9bb8cd", text: "#d3e6f3", ink: "#7fc6e0", grid: "rgba(155,200,230,.14)", warn: "#f6b94e" };
const KOREA_CITIES = [[37.57, 126.98], [35.18, 129.08], [35.16, 126.85], [33.50, 126.53], [37.75, 128.90], [36.35, 127.38]];
const CITY_NAMES = ["서울", "부산", "광주", "제주", "강릉", "대전"];
const JAPAN_CITIES = [[33.59, 130.40], [31.60, 130.56], [34.69, 135.50], [35.68, 139.69], [38.27, 140.87], [43.06, 141.35], [26.21, 127.68], [33.56, 133.53], [37.90, 139.00]];
const JAPAN_NAMES = ["후쿠오카", "가고시마", "오사카", "도쿄", "센다이", "삿포로", "나하", "고치", "니가타"];
const KMA_URL = "https://www.weather.go.kr/w/typhoon/report.do", JMA_URL = "https://www.jma.go.jp/bosai/map.html#contents=typhoon";
const WARN_P = .4;      // 경보 기준(typhoon/analyze.py THRESHOLD)
const STALE_H = 18;     // 마지막 갱신이 이보다 오래되면 경고. 자동 갱신이 몇 시간씩 늦기도 한다
const QUIET_H = 48;     // 새 관측이 이만큼 없을 때: 실시간 피드를 정상으로 받았으면 태풍 없음, 아니면 자료를 못 받은 것으로 알린다
const FEATURE_KO = {
  doy: "날짜(계절)", month: "월", LAT: "위도", LON: "경도", wind: "최대풍속", pres: "중심기압", dwind_24: "하루 풍속 변화",
  dlat_6: "6시간 남북 이동", dlon_6: "6시간 동서 이동", dlat_24: "24시간 남북 이동", dlon_24: "24시간 동서 이동",
  speed_kmh: "이동 속도", heading: "진행 방향", age_h: "발생 후 시간",
  dist_seoul: "서울까지 거리", bearing_seoul: "서울 방위", dist_tokyo: "도쿄까지 거리", bearing_tokyo: "도쿄 방위",
  atm_u_steer: "태풍을 미는 동서 바람", atm_v_steer: "태풍을 미는 남북 바람", atm_u_steer_24: "미는 동서 바람의 하루 변화", atm_v_steer_24: "미는 남북 바람의 하루 변화",
  atm_dz_ew: "동서 기압 경도", atm_dz_n: "남북 기압 경도", atm_dz_max: "고기압까지의 고도 차", atm_ridge_dlon: "고기압 서쪽 끝까지 경도 차", atm_ridge_dlat: "고기압 축까지 위도 차",
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
  Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: "rgba(3,12,20,.97)", borderColor: "#35607f", borderWidth: 1, titleColor: "#fff", bodyColor: "#fff", padding: 10, cornerRadius: 3, boxPadding: 4 });
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
const leftText = t => { const h = hrsFromNow(t); return h <= 0 ? "예상 시각이 지났어요" : h < 1 ? "1시간 안" : `지금부터 약 ${Math.round(h)}시간`; };
const earlyText = t => hrsFromNow(t) > 0 ? `빠르면 ${kst(t)}` : "이미 올 수 있는 시간대";
const kn2ms = kt => Math.round(kt * .5144);
const todayKST = () => new Date(Date.now() + 9 * 36e5).toISOString().slice(0, 10);
// 기상청 한글 표기(사이트에 나오는 이름만). 없는 이름은 영문 그대로
const KO_NAME = { CHABA: "차바", "CHAN-HOM": "찬홈", CHANTHU: "찬투", DANAS: "다나스", FRANCISCO: "프란시스코", GONI: "고니", HAGUPIT: "하구핏", HAISHEN: "하이선", HINNAMNOR: "힌남노", JANGMI: "장미", JONGDARI: "종다리", KHANUN: "카눈", "KONG-REY": "콩레이", LEEPI: "리피", LINGLING: "링링", MAYSAK: "마이삭", MERANTI: "므란티", MITAG: "미탁", NAMTHEUN: "남테운", OMAIS: "오마이스", PRAPIROON: "쁘라삐룬", PULASAN: "풀라산", SOUDELOR: "사우델로르", SOULIK: "솔릭", TAPAH: "타파", TRASES: "뜨라세", CHAMPI: "참피", "IN-FA": "인파", KOGUMA: "고구마" };
const stormName = s => s.name === "UNNAMED" || s.name === "NOT_NAMED" ? "이름 없음" : KO_NAME[s.name] || s.name;
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

/* 화면 가까이 왔을 때 한 번 실행(무거운 자료를 처음부터 받지 않도록) */
function near(el, fn) {
  if (!el) return;
  if (!("IntersectionObserver" in window)) { fn(); return; }
  const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); fn(); } }, { rootMargin: "800px 0px" });
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
  const legendOpen = forecast && window.innerWidth > 860;      // 지난 태풍·직접 예측 지도는 접어 두어 경로를 가리지 않게
  const map = L.map(id, { zoomControl: false, scrollWheelZoom: false, worldCopyJump: true, dragging: !L.Browser.mobile }).setView(center, zoom);
  L.control.zoom({ position: "topright" }).addTo(map);
  const base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
  L.tileLayer(base + "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "&copy; Esri", maxZoom: 10 }).addTo(map);
  ensurePanes(map);
  const lg = L.control({ position: "bottomright" });
  lg.onAdd = () => {
    const d = L.DomUtil.create("details", "tc-legend");
    d.open = legendOpen;
    d.innerHTML = `<summary>범례</summary>` + LV.map(l => `<div><i style="background:${l.color}"></i>${l.name}</div>`).join("")
      + (forecast ? `<div class="sep"><i class="lg-line"></i>예보 경로(+24h는 24시간 뒤)</div><div><i class="lg-cone"></i>예보 오차 범위(70%)</div>` : "")
      + `<div${forecast ? "" : ' class="sep"'}><i class="lg-zone"></i>영향권(도시 200km)</div>`;
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

// 한국 6개·일본 9개 도시 영향권(200km)
function drawCities(map) {
  const pri = new Set(["서울", "부산", "제주", "도쿄", "오사카", "후쿠오카", "나하", "삿포로"]);
  const draw = (cities, names, color, cls) => cities.forEach((c, i) => {
    L.circle(c, { radius: 200000, color, weight: 1, opacity: .45, dashArray: "3 6", fillColor: color, fillOpacity: .025, interactive: false }).addTo(map);
    L.marker(c, { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-city ${cls}${pri.has(names[i]) ? " pri" : ""}`, iconSize: [0, 0], html: `<i></i><span>${names[i]}</span>` }) }).addTo(map);
  });
  draw(JAPAN_CITIES, JAPAN_NAMES, C.jmap, "jp");
  draw(KOREA_CITIES, CITY_NAMES, C.kmap, "kr");
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
  const col = i => LV[lvIndex(w[i], grade)].color;
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
  const pad = () => ({ paddingTopLeft: [wide() ? 420 : 64, 44], paddingBottomRight: [wide() ? 120 : 64, 44], maxZoom: 5 });
  const ENV_SRC = { gfs: "어제 상공 바람 자료 반영(NOAA GFS)", ncep: "어제 상공 바람 자료 반영(NOAA 재분석)" };
  const cards = [];
  let selected = null, view = null, allView = null;

  const highlight = n => {
    mapEl.classList.toggle("focus", n != null);
    mapEl.querySelectorAll(".tc-p").forEach(e => e.classList.toggle("hot", n != null && e.classList.contains("tc-s" + n)));
  };
  const fit = (b, o = {}) => {
    view = b;
    if (REDUCED) map.fitBounds(b, pad());
    else map.flyToBounds(b, { ...pad(), duration: 1.1, ...o });
  };
  const stormBounds = (s, live) => {
    const pts = s.track.map(t => [t[1], t[2]]);
    if (!live) return L.latLngBounds(pts);
    return L.latLngBounds([...pts, ...(s.forecast?.points || []).map(p => [p[1], p[2]]), ...KOREA_CITIES, ...JAPAN_CITIES]);
  };
  const scrollToEl = (e, off = 44) => window.scrollTo({ top: e.getBoundingClientRect().top + scrollY - off, behavior: REDUCED ? "auto" : "smooth" });
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
  const whenLines = (v, s) => {
    if (!v.range) return `<div class="eta"><span class="who">우리 예측(온다면)</span> ${kst(v.eta)}쯤 · <span data-left="${v.eta}"></span></div>`;
    // eta 는 시 단위로 버리고 range 는 반올림이라 1시간 어긋날 수 있어 eta 를 감싸게 맞춘다
    const lo = [addHours(s.end, v.range[0]), v.eta].sort()[0], hi = [addHours(s.end, v.range[1]), v.eta].sort()[1];
    return `<div class="eta"><span class="who">우리 예측(온다면)</span> <b data-early="${lo}"></b>, 늦으면 ${kst(hi)}</div>`
      + `<div class="eta">가장 가능성 높은 때 ${kst(v.eta)} · <span data-left="${v.eta}"></span></div>`;
  };
  // 일본 기상청 5일 예보 진로가 영향권(도시 200km)에 들어오는지: 중심 진입 > 오차 범위 닿음 > 최단 거리
  const fcLine = (a, v) => {
    if (!a) return "";
    const city = a.near || "가장 가까운 도시", km = a.min_km != null ? `약 ${a.min_km.toLocaleString()}km` : "";
    const cities = (a.cities || []).slice(0, 4).map(x => `${x.name} ${kst(x.t)}`).join(", ");
    const txt = a.center ? `<b>${kst(a.center)}쯤</b> 중심이 영향권에 들어옴(${city} ${km}까지 접근)${cities ? `<span class="cities">도시별 진입: ${cities}</span>` : ""}`
      : a.cone ? `중심은 ${city} ${km}까지만 접근. 예보 오차를 감안하면 ${kst(a.cone)}부터 영향권에 들 수 있음`
      : `가장 가까울 때도 ${city}에서 ${km}, 5일 안에는 영향권 밖`;
    const differs = (v.p >= .2 && !a.center && !a.cone) || (v.p < .2 && a.center);
    return `<div class="eta fcx"><span class="who">일본 기상청 예보 진로</span> ${txt}${differs ? `<em class="differ">두 예측이 달라요. 공식 예보를 확인하세요.</em>` : ""}</div>`;
  };
  const rowHtml = (s, c, label) => {
    const v = s[c];
    if (v.arrived) return `<div class="pr ${c}"><span class="pl">${label}</span><i class="bar"><u data-p="1"></u></i><b>도달</b></div><div class="eta">${kst(v.arrived)} 영향권 진입</div>`;
    return `<div class="pr ${c}${v.p < .2 ? " low" : ""}"><span class="pl">${label}</span><i class="bar"><u data-p="${v.p}"></u></i><b>${pctCard(v.p)}</b></div>`
      + (v.p >= .2 ? whenLines(v, s) : "") + fcLine(s.forecast?.approach?.[c], v);
  };

  // data_until 은 마지막 관측 시각이라 태풍이 없으면 멈춰 있다. 신선도는 갱신 시각(generated)으로 따진다
  const generatedAt = new Date(`${latest.generated}:00Z`).getTime();
  const longGap = (generatedAt - utc(latest.data_until).getTime()) / 36e5 >= QUIET_H;
  const quiet = longGap && latest.feed_ok === true, noFeed = longGap && !quiet;
  let liveN = 0, goneN = 0;
  const goneDraws = [], goneIdx = new Set();
  const liveStorms = [];
  latest.storms.forEach((s, n) => {
    const live = s.end === latest.data_until && !quiet;
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
      liveStorms.push(s);
      liveBounds.push(...s.track.map(t => [t[1], t[2]]));
      L.marker([last[1], last[2]], { title: `${stormName(s)} 현재 위치`, alt: `${stormName(s)} 현재 위치`, icon: tcIcon({ size: iconSize(idx), idx, live: true, label: chipLabel(stormName(s), kt, hpa), cls }), zIndexOffset: 1000 })
        .on("click", () => { select(n, s, true, false); li.scrollIntoView({ block: "nearest" }); })
        .addTo(layer);
      const color = s.korea.p >= .2 || s.korea.arrived ? C.kmap : C.jmap;
      liveBounds.push(...drawForecast(map, layer, s, last, color, cls).points);
      const L1 = LV[idx];
      li.style.animationDelay = `${liveN * 90}ms`;
      li.innerHTML = `<div class="s-head"><b>${stormFull(s)}</b>${kt != null || s.grade != null ? `<span class="lv" style="--c:${L1.color};--ci:${L1.ink}">${lvName(kt, s.grade)}</span>` : ""}</div>`
        + (kt != null || hpa != null ? `<div class="vit">${kt != null ? `풍속 <b>${kt}</b>kt(${kn2ms(kt)}m/s)` : ""}${kt != null && hpa != null ? " · " : ""}${hpa != null ? `기압 <b>${hpa}</b>hPa` : ""}</div>` : "")
        + rowHtml(s, "korea", "한국") + rowHtml(s, "japan", "일본")
        + `<div class="src${ENV_SRC[s.env] ? "" : " weak"}">${ENV_SRC[s.env] || "상공 자료 없이 경로만으로 계산해 정확도가 낮을 수 있어요"}</div><div class="to-map">지도에서 보기</div>`;
      list.appendChild(li); liveN++;
    } else {
      // 소멸한 태풍은 '최근 소멸'을 펼쳤을 때만 지도에 그린다(활동 중인 것처럼 보이지 않게)
      goneDraws.push(() => {
        layer.addTo(map);
        drawTrack(layer, s.track, { grade: s.grade, cls, dates: false, animate: false });
        L.marker([last[1], last[2]], { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-icon ${cls}`, iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="tc-end" style="color:${LV[idx].color}"><i></i><div class="tc-label">${stormName(s)}</div></div>` }) }).addTo(layer);
        return layer;
      });
      const hit = [s.korea.arrived ? `한국 ${kst(s.korea.arrived)} 도달` : "", s.japan.arrived ? `일본 ${kst(s.japan.arrived)} 도달` : ""].filter(Boolean).join(" · ");
      li.innerHTML = `<b>${stormFull(s)}</b><span>${kst(s.start)} – ${kst(s.end)}</span>${hit ? `<em>${hit}</em>` : ""}`;
      goneList.appendChild(li); goneN++;
      goneIdx.add(n);
    }
    bind(li, n, s, live);
  });
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

  // 맨 위 한 줄 답: 나라별로 가장 위험한 태풍 기준
  const inNow = (s, c) => { const t = s.track[s.track.length - 1]; return inZone([t[1], t[2]], c === "korea" ? KOREA_CITIES : JAPAN_CITIES); };
  // 영향권을 벗어난 태풍이 다가오는 다른 태풍을 가리지 않게, 도달한 태풍은 지금 영향권 안일 때만 최우선
  const score = (s, c) => { const a = s.forecast?.approach?.[c]; return s[c].arrived ? (inNow(s, c) ? 2 : 0) : Math.max(s[c].p, a?.center ? .6 : a?.cone ? .3 : 0); };
  const earliest = (s, c) => {
    const v = s[c], a = s.forecast?.approach?.[c], ts = [];
    if (v.range && v.p >= .2) ts.push(addHours(s.end, v.range[0]));
    if (a?.center) ts.push(a.center);
    ts.sort();
    return ts.length && hrsFromNow(ts[0]) > 0 ? ts[0] : null;
  };
  const official = { korea: `<a href="${KMA_URL}" target="_blank" rel="noopener">기상청 특보 확인</a>`, japan: `<a href="${JMA_URL}" target="_blank" rel="noopener">일본 기상청 확인</a>` };
  const verdictHtml = (stale, basis) => !liveStorms.length ? "" : [["korea", "한국"], ["japan", "일본"]].map(([c, label]) => {
    const top = liveStorms.reduce((a, b) => (score(b, c) > score(a, c) ? b : a));
    const sc = score(top, c), when = earliest(top, c);
    const others = liveStorms.filter(x => x !== top && score(x, c) >= .2).length;
    const [lvl, txt] = top[c].arrived && sc === 2 ? ["hi", `${stormName(top)} 영향권에 있어요`]
      : sc >= WARN_P ? ["hi", `${stormName(top)} 올 가능성이 있어요`]
      : sc >= .2 ? ["mid", `${stormName(top)} 주시가 필요해요`]
      : stale ? ["mid", "지금 상황은 공식 정보로 확인하세요"]
      : top[c].arrived ? ["low", `${stormName(top)} 영향권을 벗어났어요`] : ["low", "지금은 올 가능성이 낮아요"];
    const notes = [stale ? basis : "", when && !stale && lvl !== "low" ? `빠르면 ${kst(when)}` : "", others ? `다른 태풍 ${others}개도 아래에서 확인하세요` : ""].filter(Boolean);
    return `<div class="vd ${lvl}"><span class="k ${c}">${label}</span><p>${txt}${notes.map(n => `<small>${n}</small>`).join("")}${lvl !== "low" ? official[c] : ""}</p></div>`;
  }).join("");

  // 남은 시간과 자료 나이는 지금 시각 기준으로 계속 다시 계산한다
  let lastVerdict = null, lastNote = null;
  const tick = () => {
    const age = (Date.now() - generatedAt) / 36e5, stale = age >= STALE_H || noFeed, note = $("staleNote");
    const obs = agoText(-hrsFromNow(latest.data_until)), link = `<a href="${KMA_URL}" target="_blank" rel="noopener">기상청 태풍 정보</a>`;
    const noteHtml = !stale ? "" : age >= STALE_H ? `마지막 갱신이 ${agoText(age)}이에요. 갱신이 늦어져 지금 상황과 다를 수 있어요. ${link}`
      : `실시간 자료를 받지 못해 ${obs} 관측이 마지막이에요. 지금 상황과 다를 수 있어요. ${link}`;
    if (noteHtml !== lastNote) note.innerHTML = lastNote = noteHtml;       // 내용이 그대로면 초점과 낭독을 흔들지 않는다
    $("asof").textContent = `${kst(latest.data_until)} 자료 · ${agoText(age)} 갱신`;
    $("nowPanel").classList.toggle("stale", stale);
    const vh = verdictHtml(stale, age >= STALE_H ? `${agoText(age)} 갱신한 자료 기준` : `${obs} 관측 기준`);
    if (vh !== lastVerdict) $("verdict").innerHTML = lastVerdict = vh;      // 내용이 그대로면 링크 초점을 흔들지 않는다
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
    if (REDUCED) map.fitBounds(allView, pad());
    else setTimeout(() => map.flyToBounds(allView, { ...pad(), duration: 2.4, easeLinearity: .2 }), 700);
  }
  setTimeout(() => list.querySelectorAll(".bar u").forEach(u => setBar(u, +u.dataset.p)), 500);
  setTimeout(() => declutter(map), 1500);
}

/* ---------- 직접 예측 ---------- */
const inZone = (p, cities) => cities.some(c => haversine(p[0], p[1], c[0], c[1]) <= 200);
function renderSim(model) {
  const map = baseMap("simMap", [28, 130], 4);
  drawCities(map);
  const form = $("simForm");
  form.date.value = todayKST();
  let pos = [24, 128];
  const marker = L.marker(pos, { title: "가상 태풍 위치", alt: "가상 태풍 위치", icon: tcIcon({ size: 44, idx: 3, live: false }), draggable: true, zIndexOffset: 1000 }).addTo(map);
  const path = L.polyline([], { color: "#0a1f33", weight: 2.5, dashArray: "6 8", opacity: .7, lineCap: "round", interactive: false }).addTo(map);

  // 모델이 배운 적 없는 입력은 계산은 하되 알려 준다
  function warnings(s) {
    const out = [], month = +s.date.slice(5, 7);
    const est = 6.7 * Math.pow(Math.max(1010 - s.pres, 0), .644);       // 기압으로 어림한 풍속(kt)
    if (Math.abs(s.wind - est) > 35) out.push(`풍속 ${s.wind}kt와 기압 ${s.pres}hPa는 함께 나타나기 어려운 조합이에요.`);
    if (s.lat < 0 || s.lat > 50 || s.lon < 100 || s.lon > 180 || (s.lat > 40 && s.lon < 125) || (s.lat > 35 && s.lon < 118) || (s.lat > 22 && s.lon < 110)) out.push("태풍이 거의 지나지 않는 지역이라 결과를 믿기 어려워요.");
    if (month <= 4 || month === 12) out.push("이 시기에는 한국·일본에 오는 태풍이 드물어 확률이 낮게 나와요.");
    return out;
  }
  function update() {
    form.querySelectorAll("label").forEach(l => {
      const i = l.querySelector("input[type=range]"), o = l.querySelector("output");
      if (i && o) { o.textContent = i.value; i.setAttribute("aria-valuetext", o.parentElement.textContent.trim()); }
    });
    const s = {
      lat: pos[0], lon: pos[1], date: form.date.value || todayKST(),
      wind: +form.wind.value, pres: +form.pres.value, heading: +form.heading.value,
      speed: +form.speed.value, age: +form.age.value, dwind: +form.dwind.value,
    };
    const f = makeFeatures(s);
    const me = marker.getElement();
    if (me) me.firstChild.style.color = LV[lvIndex(s.wind, null)].color;
    [["korea", "simK", "simKeta", "simKbar", KOREA_CITIES], ["japan", "simJ", "simJeta", "simJbar", JAPAN_CITIES]].forEach(([c, id, eid, bid, cities]) => {
      if (inZone(pos, cities)) {          // 모델은 영향권에 들어오기 전 시점만 배웠다
        $(id).textContent = "영향권";
        $(eid).textContent = "이미 영향권 안이에요";
        setBar($(bid), 1);
        return;
      }
      const r = predict(model, c, f);
      $(id).textContent = r.p < .01 ? "<1%" : pct(r.p);
      $(eid).textContent = r.p >= .2 ? `온다면 약 ${Math.max(0, Math.round(r.hours))}시간 뒤${r.lo != null ? ` (${Math.round(Math.max(0, Math.min(r.lo, r.hours)))}~${Math.round(Math.max(r.hi, r.hours))}시간)` : ""}` : "";
      setBar($(bid), r.p);
    });
    const ahead = [pos];
    for (let h = 12; h <= 48; h += 12) ahead.push(destPoint(pos, s.heading, s.speed * h));
    path.setLatLngs(ahead);
    $("simPos").textContent = `${pos[0].toFixed(1)}°N ${pos[1].toFixed(1)}°E · 서울까지 ${Math.round(f.dist_seoul).toLocaleString()}km`;
    const w = warnings(s), box = $("simWarn");
    box.hidden = !w.length;
    box.innerHTML = w.map(t => `<li>${t}</li>`).join("");
  }
  map.on("click", e => { pos = [e.latlng.lat, e.latlng.lng]; marker.setLatLng(pos); update(); });
  marker.on("drag", e => { const l = e.target.getLatLng(); pos = [l.lat, l.lng]; update(); });
  form.addEventListener("input", update);
  update();
  setTimeout(() => declutter(map), 300);
  // 키보드: 표시에 초점을 두고 방향키로 1°씩 옮긴다
  marker.getElement().addEventListener("keydown", e => {
    const move = { ArrowUp: [1, 0], ArrowDown: [-1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[e.key];
    if (!move) return;
    e.preventDefault();
    e.stopPropagation();
    pos = [pos[0] + move[0], pos[1] + move[1]];
    marker.setLatLng(pos);
    update();
  });
}

/* ---------- 예측 시점 비교: 숫자 대결 ---------- */
function renderVis(root, D) {
  if (!root) return;
  const pair = (lab, unit, L, R, note) => {
    const side = (s, cls) => `<div class="side ${cls}" style="--c:${s.c}"><span class="nm">${s.n}</span><b class="num">${s.v}<small>${unit}</small></b></div>`;
    return `<section class="vs-row"><h4 class="vs-lab">${lab}</h4><div class="vs-cols">${side(L, "l")}${side(R, "r")}</div>${note ? `<p class="vs-note">${note}</p>` : ""}</section>`;
  };
  const diff = (a, b) => Math.abs(Math.round(a) - Math.round(b));
  let rows = "";
  if (D.natK != null && D.natJ != null) rows += pair("발생에서 도착까지(중앙값)", "시간", { n: "한국", v: Math.round(D.natK), c: "var(--korea)" }, { n: "일본", v: Math.round(D.natJ), c: "var(--japan)" },
    D.natK > D.natJ ? `태풍이 한국까지 오는 데 일본보다 ${diff(D.natK, D.natJ)}시간 더 걸려요.` : "");
  if (D.turnK != null && D.turnJ != null) rows += pair("도착 직전 48시간, 진행 방향이 꺾인 각도", "°", { n: "한국", v: D.turnK, c: "var(--korea)" }, { n: "일본", v: D.turnJ, c: "var(--japan)" },
    D.turnK > D.turnJ ? "한국에 올 때 진로가 더 많이 꺾여서, 시간이 더 있어도 미리 맞히기 어려워요." : "");
  if (D.fc != null && D.ours != null) rows += pair("한국 도착 전, 처음 경보한 시점(중앙값)", "시간 전", { n: "기상청 예보", v: Math.round(D.fc), c: "var(--warn)" }, { n: "우리 모델", v: Math.round(D.ours), c: "var(--ours)" },
    D.fc > D.ours ? `기상청 예보가 약 ${diff(D.fc, D.ours)}시간 먼저 알려요. 이 사이트보다 기상청 예보를 먼저 보세요.` : "");
  if (D.gap != null) rows += `<section class="vs-row gp"><h4 class="vs-lab">기상청 첫 발표가 일본 첫 기록보다 늦은 시간(중앙값)</h4><div class="gp-in"><span class="nm" style="--c:var(--japan)">일본 첫 기록</span><div class="gp-mid"><b class="num">${D.gap}<small>시간</small></b><i class="gp-ln"></i></div><span class="nm" style="--c:var(--warn)">기상청 첫 발표</span></div><p class="vs-note">일본은 약한 열대저압부 단계부터 기록하는 경우가 많아요${D.n != null ? `(비교 태풍 ${D.n}개)` : ""}.</p></section>`;
  root.innerHTML = `<div class="vs">${rows}</div>`;
  root.querySelectorAll(".vs-row").forEach((e, i) => { e.style.setProperty("--r", i); seen(e, () => e.classList.add("in"), .3); });
}

/* ---------- 예측 시점 비교 ---------- */
function renderCompare(stats, kma) {
  const a = stats.analysis, m = stats.metrics;
  const leadP = a.model_warning_lead, leadF = a.model_warning_lead_full || {};
  const warn = c => (leadF[c] || leadP[c]).median_warning_lead_h;
  const nat = a.natural_lead, turn = a.track_turn_last_48h;
  const kmaF = kma && kma.korea_lead && kma.korea_lead.since_2015_same_as_model && kma.korea_lead.since_2015_same_as_model.first_fcst_track_in_korea;
  const kmaFirst = kma && kma.kma_first_issue_vs_jma_first_record && kma.kma_first_issue_vs_jma_first_record.all;
  const agency = kmaFirst ? { n: kmaFirst.n, h: kmaFirst.median_h } : (a.agency_tracking_start ? { n: a.agency_tracking_start.storms, h: a.agency_tracking_start.median_kma_minus_jma_h } : null);
  const ours = warn("korea");       // 지표 표·차트와 같은 값(analysis.json)
  const ko = { korea: "한국", japan: "일본" };

  const sinceF = kma && kma.korea_lead && kma.korea_lead.since_2015_same_as_model;
  const issueL = sinceF && sinceF.first_issue;
  const D = {
    natK: nat && nat.korea && has(nat.korea.median_h) ? nat.korea.median_h : null,
    natJ: nat && nat.japan && has(nat.japan.median_h) ? nat.japan.median_h : null,
    turnK: turn && turn.korea && has(turn.korea.median_abs_turn_deg) ? Math.round(turn.korea.median_abs_turn_deg) : null,
    turnJ: turn && turn.japan && has(turn.japan.median_abs_turn_deg) ? Math.round(turn.japan.median_abs_turn_deg) : null,
    fc: kmaF && has(kmaF.median_warning_lead_h) ? kmaF.median_warning_lead_h : null,
    ours: has(ours) ? ours : null,
    issue: issueL && has(issueL.median_warning_lead_h) ? issueL.median_warning_lead_h : null,
    gap: agency && has(agency.h) ? agency.h : null,
    n: agency && has(agency.n) ? agency.n : null,
  };
  renderVis($("cmpVis"), D);

  // 경보 리드타임 차트 (선에 직접 이름)
  const leads = [24, 48, 72, 96, 120];
  const hasFull = ["korea", "japan"].some(c => leadF[c]);
  const sets = [];
  const line = (label, src, color, o = {}) => ({
    label, data: leads.map(h => src[`warned_${h}h_before`] * 100), borderColor: color, backgroundColor: color,
    tension: .3, borderWidth: 2.4, pointRadius: 3, pointHoverRadius: 5, fill: false, ...o,
  });
  if (kmaF) sets.push(line("기상청 예보", kmaF, C.warn, { borderDash: [2, 5], pointStyle: "rectRot", pointRadius: 4 }));
  if (hasFull) {
    if (leadF.korea) sets.push(line("한국 환경 모델", leadF.korea, C.korea, { borderWidth: 3 }));
    if (leadP.korea) sets.push(line("한국 경로 모델", leadP.korea, C.korea, { borderDash: [6, 5], borderWidth: 1.8, pointRadius: 2 }));
    if (leadF.japan) sets.push(line("일본 환경 모델", leadF.japan, C.japan, { borderWidth: 1.8, pointRadius: 2 }));
    if (leadP.japan) sets.push(line("일본 경로 모델", leadP.japan, C.japan, { borderDash: [6, 5], borderWidth: 1.4, pointRadius: 2 }));
  } else {
    ["korea", "japan"].forEach(c => leadP[c] && sets.push(line(`${ko[c]} 모델`, leadP[c], C[c])));
  }
  seen($("leadChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
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
  describeChart($("leadChart"), `도착 전 경보 비율 그래프. ${leadRows.map(r => `${r[0]} 48시간 전 ${r[2]}`).join(", ")}. 자세한 값은 아래 표에 있습니다.`);

  // 요인: 가는 막대 표
  const fi = a.feature_importance;
  const keys = [...new Set([...Object.keys(fi.korea), ...Object.keys(fi.japan)])]
    .sort((x, y) => (fi.korea[y] || 0) - (fi.korea[x] || 0)).slice(0, 6);
  const mx = Math.max(...keys.map(k => Math.max(fi.korea[k] || 0, fi.japan[k] || 0))) || 1;
  $("factors").innerHTML = `<div class="fac-head"><span style="flex:1">예측에 크게 작용한 요인</span><span>한국</span><span>일본</span></div>`
    + keys.map(k => `<div class="fac"><span>${FEATURE_KO[k] || "기타 요인"}</span><div class="bars"><i class="bar korea"><u data-p="${(fi.korea[k] || 0) / mx}" style="background:${C.korea}"></u></i><i class="bar japan"><u data-p="${(fi.japan[k] || 0) / mx}" style="background:${C.japan}"></u></i></div></div>`).join("");
  seen($("factors"), () => $("factors").querySelectorAll(".bar u").forEach(u => setBar(u, +u.dataset.p)), .2);

  // 모델 지표: 한 장의 표
  const full = m.full || {};
  const cell = v => has(v) ? v : "–";
  const trow = (nameHtml, auc, mae, lead) => `<tr><td>${nameHtml}</td><td>${cell(auc)}</td><td>${has(mae) ? mae + "시간" : "–"}</td><td>${has(lead) ? lead + "시간" : "–"}</td></tr>`;
  const rows = [];
  ["korea", "japan"].forEach(c => {
    const t = `<span class="${c === "korea" ? "k-t" : "j-t"}">${ko[c]}</span>`;
    if (full[c]) rows.push(trow(`${t} 환경 모델`, full[c].auc, full[c].arrival_mae_h, leadF[c] && leadF[c].median_warning_lead_h));
    if (m[c]) rows.push(trow(`${t} 경로 모델`, m[c].auc, m[c].arrival_mae_h, leadP[c] && leadP[c].median_warning_lead_h));
    if (c === "korea" && kmaF && has(kmaF.median_warning_lead_h)) rows.push(trow(`<span class="w-t">기상청</span> 예보`, null, null, kmaF.median_warning_lead_h));
  });
  $("metrics").innerHTML = `<table><thead><tr><th>2015년~</th><th>AUC</th><th>도착 시간 오차</th><th>첫 경보 중앙값</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
  $("metricsNote").textContent = "AUC는 1에 가까울수록 올 태풍과 안 올 태풍을 잘 가려낸다는 뜻이에요. 경로 모델은 태풍의 위치와 세기만, 환경 모델은 상공 바람까지 씁니다. 모델 설정과 경보 기준을 고를 때도 이 기간을 봐서 실제 성능은 조금 낮을 수 있어요. 2015년 이전 자료만으로 비교해도 같은 설정이 가장 좋았어요.";
}

/* ---------- 지난 태풍 ---------- */
function renderHistory(history) {
  const sel = $("histSel"), scrub = $("scrub"), play = $("play"), q = $("histQ");
  const fillOptions = (keep) => {
    const w = q.value.trim().toUpperCase();
    sel.length = 0;
    history.forEach((s, i) => {
      const title = `${s.season} ${stormFull(s)}`;
      if (!w || title.toUpperCase().includes(w)) sel.add(new Option(`${title} · ${kst(s.arrival).replace(/ \d+시$/, "")} 한국 도달`, i));
    });
    $("histCount").textContent = sel.length ? `${sel.length}개` : "찾는 태풍이 없어요. 2015년 이후 한국에 영향을 준 태풍만 있어요.";
    if (keep != null && [...sel.options].some(o => +o.value === keep)) sel.value = keep;
    sel.disabled = !sel.length;
  };
  fillOptions();
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
      draw(0, "rgba(255,122,107,.75)", [4, 4]);
      if (cur) draw(hoursTo(cur.track[+scrub.value]), C.ink, []);
    },
  };
  const hoursTo = t => (utc(t[0]) - utc(cur.arrival)) / 36e5;

  // 첫 경보(analyze.warning_lead 와 같은 정의): 도착 전 처음 확률이 기준을 넘긴 시점
  function summary() {
    const first = cur.track.find(t => (t[5] || 0) >= WARN_P && hoursTo(t) < 0);
    const lead = first ? -hoursTo(first) : null;
    return `<b>${cur.season} ${stormFull(cur)}</b> · 한국 영향권 진입 ${kst(cur.arrival)}. `
      + (first ? `모델은 ${lead}시간 전에 처음 경보(확률 40% 이상)를 냈어요.` : "모델은 도착 전에 경보를 내지 못했어요.")
      + (cur.arrival_japan ? ` 일본 영향권 진입 ${kst(cur.arrival_japan)}.` : "");
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

  function load(i) {
    stop();
    cur = history[i];
    tr && tr.cancel();
    layer.clearLayers();
    tr = drawTrack(layer, cur.track, { weight: 4, cls: "tc-hist", opacityOf: k => k < +scrub.value ? 1 : .25, done: () => declutter(map) });
    cursor.addTo(layer);
    map.flyToBounds(cur.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: .8 });
    scrub.max = cur.track.length - 1;
    scrub.value = 0;
    $("histSummary").innerHTML = summary();
    const sample = cur.track.filter((_, k) => k % 2 === 0);
    $("histTable").innerHTML = tableHtml(["시각", "한국 도착까지", "한국", "일본"], sample.map(t => [kst(t[0]), hoursTo(t) < 0 ? `${-hoursTo(t)}시간 전` : hoursTo(t) === 0 ? "도착" : `도착 ${hoursTo(t)}시간 뒤`, pct(t[5] || 0), pct(t[6] || 0)]));
    describeChart($("histChart"), `${cur.season} ${stormName(cur)}의 시간별 도달 확률 그래프. 자세한 값은 아래 표에 있습니다.`);
    const pts = c => cur.track.map(t => ({ x: hoursTo(t), y: (t[c] || 0) * 100 }));
    const data = {
      datasets: [
        { label: "한국", data: pts(5), borderColor: C.korea, backgroundColor: C.korea + "1f", fill: true },
        { label: "일본", data: pts(6), borderColor: C.japan, backgroundColor: "transparent" },
      ].map(d => ({ ...d, tension: .3, pointRadius: 0, borderWidth: 2.4 })),
    };
    if (chart) { chart.data = data; chart.update(); }
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
          x: { type: "linear", bounds: "data", title: { display: true, text: "한국 도착 전후 시간 (0이 도착, -48h는 48시간 전)" }, ticks: { callback: v => v === 0 ? "도착" : `${v > 0 ? "+" : ""}${v}h`, stepSize: 24, maxRotation: 0, autoSkip: true } },
          y: { min: 0, max: 100, title: { display: true, text: "5일 안 도달 확률" }, ticks: { callback: v => v + "%" } },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: i => i[0].parsed.x < 0 ? `도착 ${-i[0].parsed.x}시간 전` : i[0].parsed.x === 0 ? "도착" : `도착 ${i[0].parsed.x}시간 뒤`, label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
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
  sel.addEventListener("change", () => load(+sel.value));
  q.addEventListener("input", () => {
    const keep = cur ? history.indexOf(cur) : null;
    fillOptions(keep);
    if (!sel.length) $("histSummary").textContent = "";
    else if (+sel.value !== keep) load(+sel.value);
    else $("histSummary").innerHTML = summary();
  });
  seen($("histMap"), () => load(+sel.value || 0), .1);
}

/* ---------- 통계 ---------- */
function renderStats(stats) {
  const mo = stats.korea_by_month, jm = stats.japan_by_month, mpeak = mo.indexOf(Math.max(...mo));
  const mmax = Math.max(...mo, ...(jm || [])) || 1;
  $("monthList").innerHTML = `<div class="mrow head"><span></span><span></span><b class="k-t">한국</b><b class="j-t">${jm ? "일본" : ""}</b></div>`
    + mo.map((v, i) => `<div class="mrow${i === mpeak ? " peak" : ""}"><span>${i + 1}월</span><div class="bars"><i class="bar"><u data-p="${v / mmax}"></u></i>${jm ? `<i class="bar japan"><u data-p="${jm[i] / mmax}"></u></i>` : ""}</div><b>${v}</b><b class="j-t">${jm ? jm[i] : ""}</b></div>`).join("");
  seen($("monthList"), () => $("monthList").querySelectorAll(".bar u").forEach((u, i) => setTimeout(() => setBar(u, +u.dataset.p), REDUCED ? 0 : i * 25)), .2);

  const years = Object.keys(stats.korea_by_year), yv = years.map(y => stats.korea_by_year[y]);
  const yj = stats.japan_by_year ? years.map(y => stats.japan_by_year[y]) : null;
  const row = (y, i) => yj ? [y, yv[i], yj[i]] : [y, yv[i]];
  $("yearTable").innerHTML = tableHtml(yj ? ["연도", "한국", "일본"] : ["연도", "한국"], [...years].map(row).reverse());
  const mean = a => (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1);
  describeChart($("yearChart"), `연도별 영향 태풍 수 그래프. 한 해 평균 한국 ${mean(yv)}개${yj ? `, 일본 ${mean(yj)}개` : ""}. 자세한 값은 아래 표에 있습니다.`);
  seen($("yearChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
    const line = (label, data, color) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 1.8, pointRadius: 0, pointHoverRadius: 4, tension: .15 });
    new Chart($("yearChart"), {
      type: "line", plugins: [endLabels],
      data: { labels: years, datasets: [...(yj ? [line("일본", yj, C.japan)] : []), line("한국", yv, C.korea)] },
      options: {
        maintainAspectRatio: false, animation: { duration: REDUCED ? 0 : 900, easing: "easeOutCubic" }, interaction: { mode: "index", intersect: false }, layout: { padding: { right: 36 } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y}개` } } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } }, y: { min: 0, ticks: { stepSize: 2 } } },
      },
    });
  }, .2);
}

function renderReliability(rel) {
  const ko = { korea: "한국", japan: "일본" };
  const cs = ["korea", "japan"].filter(c => rel[c] && rel[c].length);
  if (!cs.length) return;
  $("reliability").hidden = false;
  $("relLink").hidden = false;
  // 모델 50%일 때 실제 비율(앞뒤 구간 사이 직선 보간)
  const at50 = rows => {
    const k = rows.findIndex(r => r[0] >= .5);
    if (k <= 0) return null;
    const [a, b] = [rows[k - 1], rows[k]];
    return a[1] + (b[1] - a[1]) * (.5 - a[0]) / (b[0] - a[0]);
  };
  const parts = cs.map(c => [ko[c], at50(rel[c])]).filter(x => x[1] != null).map(([n, v]) => `${n} 약 ${Math.round(v * 100)}%`);
  $("relLead").textContent = parts.length ? `모델이 50%라고 할 때 실제로 온 비율은 ${parts.join(", ")}였어요.` : "";
  $("relTable").innerHTML = tableHtml(["나라", "모델 확률", "실제 비율", "사례 수(6시간 단위)"],
    cs.flatMap(c => rel[c].map(([p, o, n]) => [ko[c], `${Math.round(p * 100)}%`, `${Math.round(o * 100)}%`, `${n.toLocaleString()}${n < 100 ? " · 사례 적음" : ""}`])));
  describeChart($("relChart"), `모델 확률과 실제 비율 비교 그래프. ${$("relLead").textContent} 자세한 값은 아래 표에 있습니다.`);
  seen($("relChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
    const sets = [{ label: "", data: [{ x: 0, y: 0 }, { x: 100, y: 100 }], borderColor: C.muted, borderDash: [4, 5], borderWidth: 1.2, pointRadius: 0, pointHitRadius: 0 }]
      .concat(cs.map(c => ({
        label: ko[c], data: rel[c].map(([p, o, n]) => ({ x: p * 100, y: o * 100, n })),
        borderColor: C[c], backgroundColor: C[c], borderWidth: 2, pointRadius: 4.5, pointHoverRadius: 6, pointHitRadius: 12,
        pointBorderColor: getComputedStyle(document.body).getPropertyValue("--s-stats").trim() || "#0c2a42", pointBorderWidth: 2,
      })));
    new Chart($("relChart"), {
      type: "line", plugins: [endLabels],
      data: { datasets: sets },
      options: {
        maintainAspectRatio: false, animation: false, layout: { padding: { right: 48, top: 6 } },
        scales: {
          x: { type: "linear", min: 0, max: 100, ticks: { callback: v => v + "%", stepSize: 20 }, title: { display: true, text: "모델이 말한 확률" } },
          y: { min: 0, max: 100, ticks: { callback: v => v + "%", stepSize: 20 }, title: { display: true, text: "실제로 온 비율" } },
        },
        plugins: {
          legend: { display: false },
          tooltip: { filter: i => i.dataset.label, callbacks: {
            title: () => "",
            label: i => `${i.dataset.label}: 모델 ${i.parsed.x.toFixed(0)}% → 실제 ${i.parsed.y.toFixed(0)}% (사례 ${i.raw.n.toLocaleString()}개)` } },
        },
      },
    });
  }, .2);
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
      if (!$("nowMap").children.length) $("nowMap").innerHTML = `<p class="map-empty">자료가 없어 지도를 그리지 못했어요</p>`;
    }
    else unavailable(id);
  }
}

// 자료마다 따로 그린다. 현재 태풍을 먼저 보여 주고, 큰 파일(직접 예측 모델)은 그 섹션 가까이 왔을 때 받는다
soft("data/latest.json").then(latest => guard("now", latest && latest.storms && latest, renderNow));
soft("data/history.json").then(history => guard("history", history && history.length && history, renderHistory));
Promise.all(["stats", "kma_lead"].map(n => soft(`data/${n}.json`))).then(([stats, kma]) => {
  guard("compare", stats && stats.analysis && stats, s => renderCompare(s, kma));
  guard("stats", stats && stats.korea_by_month && stats.korea_by_year && stats, renderStats);
  if (stats && stats.reliability) guard("reliability", stats.reliability, renderReliability);
});
near($("sim"), () => soft("data/model.json").then(model => guard("sim", model, renderSim)));
// 메뉴에 지금 보는 섹션 표시(좁은 화면에서는 그 메뉴가 보이도록 옆으로 밀기)
if ("IntersectionObserver" in window) {
  const links = [...document.querySelectorAll(".top nav a")], nav = document.querySelector(".top nav");
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    links.forEach(a => {
      const on = a.getAttribute("href") === `#${e.target.id}`;
      if (on) { a.setAttribute("aria-current", "true"); nav.scrollTo({ left: a.offsetLeft - nav.offsetLeft - 40, behavior: REDUCED ? "auto" : "smooth" }); }
      else a.removeAttribute("aria-current");
    });
  }), { rootMargin: "-45% 0px -50% 0px" });
  document.querySelectorAll("main > section").forEach(sec => io.observe(sec));
}
