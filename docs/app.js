const C = { korea: "#d62f43", japan: "#1a6fd0", muted: "#5b6b82", text: "#0f1b2d", grid: "rgba(15,27,45,.08)", warn: "#a85a00" };
const KOREA_CITIES = [[37.57, 126.98], [35.18, 129.08], [35.16, 126.85], [33.50, 126.53], [37.75, 128.90], [36.35, 127.38]];
const CITY_NAMES = ["서울", "부산", "광주", "제주", "강릉", "대전"];
const FEATURE_KO = {
  doy: "날짜(계절)", month: "월", LAT: "위도", LON: "경도", wind: "최대풍속", pres: "중심기압", dwind_24: "하루 풍속 변화",
  dlat_6: "6시간 남북 이동", dlon_6: "6시간 동서 이동", dlat_24: "24시간 남북 이동", dlon_24: "24시간 동서 이동",
  speed_kmh: "이동 속도", heading: "진행 방향", age_h: "발생 후 시간",
  dist_seoul: "서울까지 거리", bearing_seoul: "서울 방위", dist_tokyo: "도쿄까지 거리", bearing_tokyo: "도쿄 방위",
  atm_u_steer: "동서 지향류", atm_v_steer: "남북 지향류", atm_u_steer_24: "하루 동서 지향류 변화", atm_v_steer_24: "하루 남북 지향류 변화",
  atm_shear: "연직 바람 시어", atm_z500: "500hPa 고도", atm_z500_n: "북쪽 500hPa 고도", atm_z500_e: "동쪽 500hPa 고도",
  atm_z500_w: "서쪽 500hPa 고도", atm_ridge_west: "북태평양 고기압 서쪽 끝", atm_ridge_lat: "고기압 축 위도", atm_z500_max: "고기압 세기",
  ocn_sst: "해수면 온도", ocn_sst_anom: "해수온 편차", ocn_sst_ahead: "북쪽 해수온", ocn_sst_ahead_diff: "북쪽 해수온 차이",
  ocn_oni: "엘니뇨 지수", ocn_pdo: "태평양 10년 진동", ocn_nino4: "니뇨4 편차",
};

Chart.defaults.color = C.muted;
Chart.defaults.font.family = "'Pretendard Variable', Pretendard, system-ui, sans-serif";
Chart.defaults.borderColor = C.grid;
Object.assign(Chart.defaults.plugins.tooltip, { backgroundColor: "rgba(15,27,45,.94)", titleColor: "#fff", bodyColor: "#fff", padding: 10, cornerRadius: 8, boxPadding: 4 });

const getJSON = p => fetch(p, { cache: "no-cache" }).then(r => r.json());
const utc = t => new Date(t + ":00:00Z");
function kst(t, withYear = false) {
  const d = new Date(utc(t).getTime() + 9 * 36e5);
  const s = `${d.getUTCMonth() + 1}월 ${d.getUTCDate()}일 ${String(d.getUTCHours()).padStart(2, "0")}시`;
  return withYear ? `${d.getUTCFullYear()}년 ${s}` : s;
}
const pct = p => `${Math.round(p * 100)}%`;

/* 강도 체계 (풍속 kt 기준, 없으면 JMA 등급) */
const LV = [
  { name: "열대저압부", color: "#6b8299", ink: "#475b71" },
  { name: "열대폭풍", color: "#2aa7d8", ink: "#0a6a96" },
  { name: "강한 열대폭풍", color: "#f08a1c", ink: "#9a5200" },
  { name: "태풍", color: "#e5383b", ink: "#c0262b" },
  { name: "강한 태풍", color: "#a4161a", ink: "#9b1218" },
  { name: "매우 강한 태풍", color: "#7b2cbf", ink: "#6a22a8" },
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

const DOT = 1.6;
function ensurePanes(map) {
  if (map._tcPanes) return;
  map.createPane("tcCone").style.zIndex = 380;
  map.createPane("tcCasing").style.zIndex = 395;
  map._tcPanes = true;
}

function baseMap(id, center = [30, 130], zoom = 4) {
  const map = L.map(id, { zoomControl: true, scrollWheelZoom: false, worldCopyJump: true }).setView(center, zoom);
  const base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
  L.tileLayer(base + "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "&copy; Esri", maxZoom: 10 }).addTo(map);
  L.tileLayer(base + "World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 10 }).addTo(map);
  ensurePanes(map);
  const lg = L.control({ position: "bottomleft" });
  lg.onAdd = () => {
    const d = L.DomUtil.create("div", "tc-legend");
    d.innerHTML = LV.map(l => `<div><i style="background:${l.color}"></i>${l.name}</div>`).join("");
    L.DomEvent.disableClickPropagation(d);
    return d;
  };
  lg.addTo(map);
  return map;
}

// 한국 6개 도시 영향권(200km)
function drawCities(map) {
  KOREA_CITIES.forEach((c, i) => {
    L.circle(c, { radius: 200000, color: C.korea, weight: 1, opacity: .45, dashArray: "3 6", fillColor: C.korea, fillOpacity: .025, interactive: false }).addTo(map);
    L.marker(c, { interactive: false, keyboard: false, icon: L.divIcon({ className: "tc-city", iconSize: [0, 0], html: `<i></i><span>${CITY_NAMES[i]}</span>` }) }).addTo(map);
  });
}

// 고급 태풍 아이콘: 구름띠 4+4가닥 + 고정된 눈
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
function drawTrack(layer, track, { grade = null, animate = true, weight = 3.5, cls = "", dates = true, opacityOf = null } = {}) {
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
  if (!animate) items.forEach(f => f());
  else {
    // 앞부분은 빠르게, 뒤로 갈수록 천천히
    let n = 0;
    const step = () => { if (!dead && n < items.length) { items[n++](); setTimeout(step, 6 + 30 * (n / items.length)); } };
    step();
  }
  return { restyle, cancel: () => { dead = true; } };
}

// 예보 경로 + 불확실성 원뿔(전용 pane, 합쳐서 보이게)
const MERC = L.Projection.SphericalMercator;
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

function reveal(el, fn) {
  const io = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) { io.unobserve(e.target); e.target.classList.add("in"); fn && fn(); }
  }), { threshold: .15 });
  io.observe(el);
}

function countUp(el, to, digits = 0) {
  const t0 = performance.now(), dur = 1400;
  const tick = now => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = (to * e).toFixed(digits);
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* 지금 */
function renderNow(latest) {
  document.getElementById("asof").textContent = `자료 기준 ${kst(latest.data_until, true)}${latest.live ? " · 실시간(일본 기상청 공개 자료 포함)" : ""}`;
  const map = baseMap("nowMap", [28, 132], 4);
  const mapEl = document.getElementById("nowMap");
  drawCities(map);
  const list = document.getElementById("nowList");
  const bounds = [];
  const focus = n => {
    mapEl.classList.toggle("focus", n != null);
    mapEl.querySelectorAll(".tc-p").forEach(e => e.classList.toggle("hot", n != null && e.classList.contains("tc-s" + n)));
  };
  latest.storms.forEach((s, n) => {
    const live = s.end === latest.data_until;
    const cls = `tc-p tc-s${n}`;
    const layer = L.layerGroup().addTo(map);
    const last = s.track[s.track.length - 1];
    const w = fillWind(s.track);
    const kt = last[3] ?? null, hpa = last[4] ?? null;
    const idx = lvIndex(kt ?? (live ? w[w.length - 1] : null), s.grade);
    drawTrack(layer, s.track, { grade: s.grade, cls, dates: live });
    if (live) {
      bounds.push(...s.track.map(t => [t[1], t[2]]));
      const nm = s.name === "UNNAMED" ? "이름 없음" : s.name;
      L.marker([last[1], last[2]], { icon: tcIcon({ size: iconSize(idx), idx, live: true, label: chipLabel(nm, kt, hpa), cls }), keyboard: false, zIndexOffset: 1000 }).addTo(layer);
      const color = s.korea.p >= .2 || s.korea.arrived ? C.korea : C.japan;
      const f = drawForecast(map, layer, s, last, color, cls);
      bounds.push(...f.points);
    } else {
      L.marker([last[1], last[2]], { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-icon ${cls}`, iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="tc-end" style="color:${LV[idx].color}"><i></i><div class="tc-label">${s.name === "UNNAMED" ? "이름 없음" : s.name}</div></div>` }) }).addTo(layer);
    }

    const card = document.createElement("article");
    card.className = "storm" + (live ? "" : " gone");
    card.tabIndex = 0;
    card.style.animationDelay = `${n * 80}ms`;
    const prob = (c, label) => {
      const v = s[c];
      if (v.arrived) return `<div class="prob ${c}"><span>${label}</span><strong>도달</strong><em>${kst(v.arrived)}</em></div>`;
      if (!live) return `<div class="prob ${c}"><span>${label}</span><strong>–</strong></div>`;
      return `<div class="prob ${c}"><span>${label} 5일 내</span><strong>${pct(v.p)}</strong>${v.p >= .2 ? `<em>약 ${Math.max(0, v.hours)}시간 뒤 · <span class="nw">${kst(v.eta)}</span></em>` : ""}<div class="meter"><i data-w="${v.p * 100}"></i></div></div>`;
    };
    const L1 = LV[idx];
    const vit = (kt != null || hpa != null) ? `<div class="vitals">${kt != null ? `<div><b>${kt}</b><span>kt 풍속</span></div>` : ""}${hpa != null ? `<div><b>${hpa}</b><span>hPa 기압</span></div>` : ""}</div>` : "";
    card.innerHTML = `<div class="storm-head"><b>${s.name === "UNNAMED" ? "이름 없음" : s.name}</b><span class="head-r">${kt != null || s.grade != null ? `<span class="badge lv" style="--c:${L1.color};--ci:${L1.ink}">${lvName(kt, s.grade)}</span>` : ""}<span class="badge ${live ? "live" : ""}">${live ? "활동 중" : "소멸"}</span></span></div>
      ${vit}<div class="probs">${prob("korea", "한국")}${prob("japan", "일본")}</div>
      <div class="storm-time">${kst(s.start)} – ${kst(s.end)}</div>`;
    const fly = () => map.flyToBounds(s.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: 1 });
    card.addEventListener("click", fly);
    card.addEventListener("keydown", e => { if (e.key === "Enter") fly(); });
    ["mouseenter", "focus"].forEach(ev => card.addEventListener(ev, () => focus(n)));
    ["mouseleave", "blur"].forEach(ev => card.addEventListener(ev, () => focus(null)));
    list.appendChild(card);
  });
  if (bounds.length) map.fitBounds([...bounds, ...KOREA_CITIES], { padding: [30, 30], maxZoom: 5 });
  setTimeout(() => list.querySelectorAll(".meter i").forEach(i => i.style.width = i.dataset.w + "%"), 400);
}

/* 직접 예측 */
function renderSim(model) {
  const map = baseMap("simMap", [28, 130], 4);
  drawCities(map);
  const form = document.getElementById("simForm");
  form.date.value = new Date().toISOString().slice(0, 10);
  let pos = [24, 128];
  const marker = L.marker(pos, { icon: tcIcon({ size: 44, idx: 3, live: false }), draggable: true, zIndexOffset: 1000 }).addTo(map);
  const path = L.polyline([], { color: C.text, weight: 2.5, dashArray: "6 8", opacity: .6, lineCap: "round", interactive: false }).addTo(map);
  const ring = (c, p) => {
    const bar = document.querySelector(`.ring[data-c=${c}] .bar`);
    bar.style.strokeDashoffset = 326.7 * (1 - p);
    bar.style.opacity = p < .01 ? 0 : 1;
  };

  function update() {
    form.querySelectorAll("label").forEach(l => {
      const i = l.querySelector("input[type=range]"), o = l.querySelector("output");
      if (i && o) o.textContent = i.value;
    });
    const s = {
      lat: pos[0], lon: pos[1], date: form.date.value || new Date().toISOString().slice(0, 10),
      wind: +form.wind.value, pres: +form.pres.value, heading: +form.heading.value,
      speed: +form.speed.value, age: +form.age.value, dwind: +form.dwind.value,
    };
    const f = makeFeatures(s);
    const me = marker.getElement();
    if (me) me.firstChild.style.color = LV[lvIndex(s.wind, null)].color;
    [["korea", "simK", "simKeta"], ["japan", "simJ", "simJeta"]].forEach(([c, id, eid]) => {
      const r = predict(model, c, f);
      document.getElementById(id).textContent = pct(r.p);
      document.getElementById(eid).textContent = r.p >= .2 ? `약 ${Math.max(0, Math.round(r.hours))}시간 뒤` : "";
      ring(c, r.p);
    });
    const ahead = [pos];
    for (let h = 12; h <= 48; h += 12) ahead.push(destinationLL(pos, s.heading, s.speed * h));
    path.setLatLngs(ahead);
    document.getElementById("simPos").textContent =
      `${pos[0].toFixed(1)}°N ${pos[1].toFixed(1)}°E · 서울까지 ${Math.round(f.dist_seoul).toLocaleString()}km`;
  }
  const destinationLL = destPoint;
  map.on("click", e => { pos = [e.latlng.lat, e.latlng.lng]; marker.setLatLng(pos); update(); });
  marker.on("drag", e => { const l = e.target.getLatLng(); pos = [l.lat, l.lng]; update(); });
  form.addEventListener("input", update);
  reveal(document.querySelector(".sim-panel"), update);
}

/* 한국 vs 일본 */
function renderCompare(stats, kma) {
  const a = stats.analysis, m = stats.metrics;
  const leadP = a.model_warning_lead, leadF = a.model_warning_lead_full || {};
  const warn = c => (leadF[c] || leadP[c]).median_warning_lead_h;
  const nums = [
    ["natK", a.natural_lead.korea.median_h], ["natJ", a.natural_lead.japan.median_h],
    ["warnK", warn("korea")], ["warnJ", warn("japan")],
    ["turnK", a.track_turn_last_48h.korea.median_abs_turn_deg, 1], ["turnJ", a.track_turn_last_48h.japan.median_abs_turn_deg, 1],
    ["agency", a.agency_tracking_start.median_kma_minus_jma_h],
  ];
  nums.forEach(([id, v, d]) => {
    const el = document.getElementById(id);
    el.textContent = "0";
    reveal(el.closest(".stat"), () => countUp(el, v, d || 0));
  });
  [["korea", "warnK"], ["japan", "warnJ"]].forEach(([c, id]) => {
    const p = document.getElementById(id).parentElement;
    p.querySelector(".was")?.remove();
    if (leadF[c]) p.insertAdjacentHTML("beforeend", `<small class="was">경로 모델 ${leadP[c].median_warning_lead_h}시간</small>`);
  });
  const kmaF = kma && kma.korea_lead && kma.korea_lead.since_2015_same_as_model && kma.korea_lead.since_2015_same_as_model.first_fcst_track_in_korea;
  if (kmaF && kmaF.median_warning_lead_h != null) {
    document.getElementById("warnK").parentElement.insertAdjacentHTML("beforeend", `<small class="was kma">기상청 예보 ${kmaF.median_warning_lead_h}시간</small>`);
  }
  document.getElementById("agencyRatio").textContent =
    `${a.agency_tracking_start.storms}개 태풍 중 ${Math.round(a.agency_tracking_start.kma_later_ratio * 100)}%`;

  const leads = [24, 48, 72, 96, 120];
  const ko = { korea: "한국", japan: "일본" };
  const hasFull = ["korea", "japan"].some(c => leadF[c]);
  const sets = [];
  ["korea", "japan"].forEach(c => {
    const line = (src, full) => ({
      label: hasFull ? `${ko[c]} ${full ? "환경" : "경로"}` : ko[c],
      data: leads.map(h => src[c][`warned_${h}h_before`] * 100),
      borderColor: C[c], tension: .35, borderWidth: full || !hasFull ? 2.5 : 1.8,
      backgroundColor: full || !hasFull ? C[c] + "22" : C[c], fill: full || !hasFull,
      borderDash: hasFull && !full ? [6, 5] : [], pointRadius: hasFull && !full ? 2.5 : 4,
      pointStyle: full || !hasFull ? "circle" : "line",
    });
    sets.push(line(leadP, false));
    if (leadF[c]) sets.push(line(leadF, true));
  });
  if (kmaF) {
    sets.push({
      label: "기상청 예보", data: leads.map(h => kmaF[`warned_${h}h_before`] * 100),
      borderColor: C.warn, backgroundColor: C.warn, tension: .35, borderWidth: 2.5, borderDash: [2, 5],
      pointRadius: 4, pointStyle: "rectRot", fill: false,
    });
  }
  const usePS = hasFull || !!kmaF;
  reveal(document.getElementById("leadChart").closest(".card"), () => new Chart(document.getElementById("leadChart"), {
    type: "line",
    data: { labels: leads.map(h => `${h}시간 전`), datasets: sets },
    options: {
      maintainAspectRatio: false, animation: { duration: 1600 },
      scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
      plugins: {
        legend: { labels: { usePointStyle: usePS, boxWidth: 28, boxHeight: 8 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } },
      },
    },
  }));

  const keys = [...new Set([...Object.keys(a.feature_importance.korea), ...Object.keys(a.feature_importance.japan)])]
    .sort((x, y) => (a.feature_importance.korea[y] || 0) - (a.feature_importance.korea[x] || 0)).slice(0, 8);
  reveal(document.getElementById("impChart").closest(".card"), () => new Chart(document.getElementById("impChart"), {
    type: "bar",
    data: {
      labels: keys.map(k => FEATURE_KO[k] || k),
      datasets: ["korea", "japan"].map(c => ({
        label: ko[c], data: keys.map(k => a.feature_importance[c][k] || 0),
        backgroundColor: C[c], borderRadius: 4, barPercentage: .8,
      })),
    },
    options: {
      indexAxis: "y", maintainAspectRatio: false, animation: { duration: 1600 },
      scales: { x: { ticks: { display: false } }, y: { grid: { display: false } } },
    },
  }));

  const full = m.full || {};
  const chip = (c, name, key, unit = "") => full[c] && full[c][key] != null
    ? `<span class="cmp">${ko[c]} ${name}<em>경로</em><s>${m[c][key]}${unit}</s><em>환경</em><b>${full[c][key]}${unit}</b></span>`
    : `<span>${ko[c]} ${name}<b>${m[c][key]}${unit}</b></span>`;
  const kmaChip = kmaF && kmaF.median_warning_lead_h != null
    ? `<span class="cmp">한국 첫 경보 중앙값<em>기상청 예보</em><s>${kmaF.median_warning_lead_h}시간</s><em>우리 모델</em><b>${warn("korea")}시간</b></span>` : "";
  document.getElementById("metrics").innerHTML = `<span>검증 2015년~</span>` + kmaChip +
    ["korea", "japan"].map(c => chip(c, "AUC", "auc") + chip(c, "도착 시간 오차", "arrival_mae_h", "시간")).join("");
}

/* 지난 태풍 */
function renderHistory(history) {
  const sel = document.getElementById("histSel"), scrub = document.getElementById("scrub"), play = document.getElementById("play");
  history.forEach((s, i) => sel.add(new Option(`${s.season} ${s.name}`, i)));
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
      draw(0, "rgba(214,47,67,.6)", [4, 4]);
      if (cur) draw(hoursTo(cur.track[+scrub.value]), C.text, []);
    },
  };
  const hoursTo = t => (utc(t[0]) - utc(cur.arrival)) / 36e5;

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
    document.getElementById("scrubTime").textContent = `${kst(t[0])} · 한국 ${pct(t[5] || 0)}`;
    chart && chart.update("none");
  }

  function load(i) {
    stop();
    cur = history[i];
    tr && tr.cancel();
    layer.clearLayers();
    tr = drawTrack(layer, cur.track, { weight: 4, cls: "tc-hist", opacityOf: k => k < +scrub.value ? 1 : .25 });
    cursor.addTo(layer);
    map.flyToBounds(cur.track.map(t => [t[1], t[2]]), { padding: [30, 30], duration: .8 });
    scrub.max = cur.track.length - 1;
    scrub.value = 0;
    const pts = c => cur.track.map(t => ({ x: hoursTo(t), y: (t[c] || 0) * 100 }));
    const data = {
      datasets: [
        { label: "한국", data: pts(5), borderColor: C.korea, backgroundColor: C.korea + "22", fill: true },
        { label: "일본", data: pts(6), borderColor: C.japan, backgroundColor: "transparent" },
      ].map(d => ({ ...d, tension: .3, pointRadius: 0, borderWidth: 2.5 })),
    };
    if (chart) { chart.data = data; chart.update(); }
    else chart = new Chart(document.getElementById("histChart"), {
      type: "line", data, plugins: [cursorLine],
      options: {
        maintainAspectRatio: false, animation: { duration: 1200 }, interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: {
          x: { type: "linear", ticks: { callback: v => v === 0 ? "도착" : `${v > 0 ? "+" : ""}${v}h`, stepSize: 24, maxRotation: 0, autoSkip: true } },
          y: { min: 0, max: 100, ticks: { callback: v => v + "%" } },
        },
        plugins: { tooltip: { callbacks: { title: i => `도착 ${i[0].parsed.x}시간`, label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
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
  reveal(document.getElementById("histMap"), () => load(0));
}

/* 통계 */
function renderStats(stats) {
  const years = Object.keys(stats.korea_by_year);
  reveal(document.getElementById("monthChart").closest(".card"), () => new Chart(document.getElementById("monthChart"), {
    type: "bar",
    data: { labels: [...Array(12)].map((_, i) => `${i + 1}월`), datasets: [{ data: stats.korea_by_month, backgroundColor: C.korea, borderRadius: 6 }] },
    options: { maintainAspectRatio: false, animation: { duration: 1400 }, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true } } } },
  }));
  reveal(document.getElementById("yearChart").closest(".card"), () => new Chart(document.getElementById("yearChart"), {
    type: "bar",
    data: { labels: years, datasets: [{ data: years.map(y => stats.korea_by_year[y]), backgroundColor: C.japan, borderRadius: 2 }] },
    options: {
      maintainAspectRatio: false, animation: { duration: 1400 }, plugins: { legend: { display: false } },
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } }, y: { ticks: { stepSize: 1 } } },
    },
  }));
}

/* 서리 */
function renderFrost(fr) {
  const form = document.getElementById("frostForm");
  const COL = ["#d62f43", "#1a6fd0", "#c27803", "#0b86c2", "#7c3aed", "#15803d"];
  const ST = fr.stations;
  ST.forEach(s => form.stn.add(new Option(s.name, s.stn)));
  const y = new Date().getFullYear();
  form.date.value = `${y}-11-15`;
  const bar = document.querySelector(".ring[data-c=frost] .bar");
  const outEl = document.getElementById("frostP");
  const val = n => +form[n].value;
  function update() {
    form.querySelectorAll("label").forEach(l => {
      const i = l.querySelector("input[type=range]"), o = l.querySelector("output");
      if (i && o) o.textContent = i.value;
    });
    const st = ST.find(s => String(s.stn) === form.stn.value) || ST[0];
    const d = new Date((form.date.value || `${y}-11-15`) + "T00:00:00Z");
    const doy = Math.floor((d - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5);
    const o = {
      TA_AVG: val("TA_AVG"), TA_MAX: val("TA_MAX"), TA_MIN: val("TA_MIN"), TD_AVG: val("TD_AVG"),
      TS_AVG: val("TA_AVG"), TG_MIN: val("TA_MIN") - 3, HM_AVG: val("HM_AVG"), HM_MIN: Math.max(5, val("HM_AVG") - 20),
      WS_AVG: val("WS_AVG"), WS_MAX: val("WS_AVG") * 2.2, PS_AVG: 1015, CA_TOT: val("CA_TOT"),
      SS_DAY: Math.max(0, (10 - val("CA_TOT")) * 0.9), RN_DAY: 0, SD_MAX: 0,
      d_TA_MIN: 0, d_PS_AVG: 0, d_TD_AVG: 0,
      doy_sin: Math.sin(2 * Math.PI * doy / 365.25), doy_cos: Math.cos(2 * Math.PI * doy / 365.25),
      stn_lat: st.lat, stn_lon: st.lon,
    };
    const x = fr.features.map(f => (o[f] ?? NaN));
    const p = 1 / (1 + Math.exp(-runTrees(fr.clf, x)));
    outEl.textContent = pct(p);
    bar.style.strokeDashoffset = 326.7 * (1 - p);
    bar.style.opacity = p < .01 ? 0 : 1;
  }
  form.addEventListener("input", update);
  reveal(document.querySelector("#frost .sim-panel"), update);

  const mmdd = t => { const [m, d] = String(t).split("-"); return `${+m}월 ${+d}일`; };
  reveal(document.getElementById("frostChart").closest(".card"), () => new Chart(document.getElementById("frostChart"), {
    type: "line",
    data: {
      labels: [...Array(12)].map((_, i) => `${i + 1}월`),
      datasets: ST.map((s, i) => ({
        label: s.name, data: s.rate.map(v => v * 100), borderColor: COL[i % 6], backgroundColor: COL[i % 6],
        tension: .35, borderWidth: 2.2, pointRadius: 2.5,
      })),
    },
    options: {
      maintainAspectRatio: false, animation: { duration: 1600 }, interaction: { mode: "index", intersect: false },
      scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8 } },
        tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } },
      },
    },
  }));
  document.getElementById("frostStations").innerHTML = ST.map((s, i) =>
    `<div class="fs"><b><i style="background:${COL[i % 6]}"></i>${s.name}</b><p>첫서리<span>${mmdd(s.first_avg)}</span></p><p>끝서리<span>${mmdd(s.last_avg)}</span></p></div>`).join("");
  const m = fr.metrics || {};
  document.getElementById("frostMetrics").innerHTML = `<span>평균 첫서리·끝서리 2005년~</span>` +
    (m.auc != null ? `<span>AUC<b>${m.auc}</b></span>` : "") +
    (m.f1 != null && m.persistence_f1 != null ? `<span class="cmp">F1<em>단순 지속</em><s>${m.persistence_f1}</s><em>모델</em><b>${m.f1}</b></span>` : "");
}

document.querySelectorAll(".reveal").forEach(el => reveal(el));

const soft = p => getJSON(p).catch(() => null);
soft("data/frost.json").then(fr => {
  if (fr && fr.stations && fr.stations.length && fr.clf) renderFrost(fr);
  else document.getElementById("frost").hidden = true;
});
Promise.all(["data/latest.json", "data/stats.json", "data/history.json", "data/model.json"].map(getJSON).concat(soft("data/kma_lead.json")))
  .then(([latest, stats, history, model, kma]) => {
    renderNow(latest);
    renderSim(model);
    renderCompare(stats, kma);
    renderHistory(history);
    renderStats(stats);
  });
