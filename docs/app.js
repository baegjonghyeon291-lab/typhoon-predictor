const C = { korea: "#ff7a6b", japan: "#63aeff", kmap: "#c8283c", jmap: "#1860c0", muted: "#9bb8cd", text: "#d3e6f3", ink: "#7fc6e0", grid: "rgba(155,200,230,.14)", warn: "#f6b94e", frost: "#3fd2e6" };
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
const has = v => v != null && !Number.isNaN(v);
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

/* 강도 체계 (풍속 kt 기준, 없으면 JMA 등급) */
const LV = [
  { name: "열대저압부", color: "#6b8299", ink: "#a9c0d4" },
  { name: "열대폭풍", color: "#2aa7d8", ink: "#6fd3f5" },
  { name: "강한 열대폭풍", color: "#f08a1c", ink: "#ffb35c" },
  { name: "태풍", color: "#e5383b", ink: "#ff8a8d" },
  { name: "강한 태풍", color: "#a4161a", ink: "#ff9a9e" },
  { name: "매우 강한 태풍", color: "#7b2cbf", ink: "#d3a6f7" },
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

// 지도마다 범례 하나(오른쪽 아래), 확대 버튼은 오른쪽 위
function baseMap(id, center = [30, 130], zoom = 4) {
  const map = L.map(id, { zoomControl: false, scrollWheelZoom: false, worldCopyJump: true, dragging: !L.Browser.mobile }).setView(center, zoom);
  L.control.zoom({ position: "topright" }).addTo(map);
  const base = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/";
  L.tileLayer(base + "World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "&copy; Esri", maxZoom: 10 }).addTo(map);
  L.tileLayer(base + "World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 10 }).addTo(map);
  ensurePanes(map);
  const lg = L.control({ position: "bottomright" });
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
    L.circle(c, { radius: 200000, color: C.kmap, weight: 1, opacity: .45, dashArray: "3 6", fillColor: C.kmap, fillOpacity: .025, interactive: false }).addTo(map);
    L.marker(c, { interactive: false, keyboard: false, icon: L.divIcon({ className: "tc-city", iconSize: [0, 0], html: `<i></i><span>${CITY_NAMES[i]}</span>` }) }).addTo(map);
  });
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
  if (!animate || REDUCED) items.forEach(f => f());
  else {
    // 앞부분은 빠르게, 뒤로 갈수록 천천히
    let n = 0;
    const step = () => { if (!dead && n < items.length) { items[n++](); setTimeout(step, 6 + 30 * (n / items.length)); } };
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
  $("asof").textContent = `${kst(latest.data_until, true)} 기준`;
  const map = baseMap("nowMap", [27, 134], 4);
  const mapEl = $("nowMap");
  drawCities(map);
  const list = $("nowList"), goneList = $("goneList");
  const bounds = [];
  const wide = () => window.innerWidth > 860;
  const pad = () => ({ paddingTopLeft: [wide() ? 372 : 24, 40], paddingBottomRight: [24, 40], maxZoom: 5 });
  const focus = n => {
    mapEl.classList.toggle("focus", n != null);
    mapEl.querySelectorAll(".tc-p").forEach(e => e.classList.toggle("hot", n != null && e.classList.contains("tc-s" + n)));
  };
  const name = s => s.name === "UNNAMED" ? "이름 없음" : s.name;
  const bind = (el, n, s) => {
    const fly = () => map.flyToBounds(s.track.map(t => [t[1], t[2]]), { ...pad(), duration: 1.1 });
    el.addEventListener("click", fly);
    el.addEventListener("keydown", e => { if (e.key === "Enter") fly(); });
    ["mouseenter", "focus"].forEach(ev => el.addEventListener(ev, () => focus(n)));
    ["mouseleave", "blur"].forEach(ev => el.addEventListener(ev, () => focus(null)));
  };
  let liveN = 0, goneN = 0;
  latest.storms.forEach((s, n) => {
    const live = s.end === latest.data_until;
    const cls = `tc-p tc-s${n}`;
    const layer = L.layerGroup().addTo(map);
    const last = s.track[s.track.length - 1];
    const w = fillWind(s.track);
    const kt = last[3] ?? null, hpa = last[4] ?? null;
    const idx = lvIndex(kt ?? (live ? w[w.length - 1] : null), s.grade);
    drawTrack(layer, s.track, { grade: s.grade, cls, dates: live });
    const li = document.createElement("li");
    li.tabIndex = 0;
    if (live) {
      bounds.push(...s.track.map(t => [t[1], t[2]]));
      L.marker([last[1], last[2]], { icon: tcIcon({ size: iconSize(idx), idx, live: true, label: chipLabel(name(s), kt, hpa), cls }), keyboard: false, zIndexOffset: 1000 }).addTo(layer);
      const color = s.korea.p >= .2 || s.korea.arrived ? C.kmap : C.jmap;
      const f = drawForecast(map, layer, s, last, color, cls);
      bounds.push(...f.points);

      const row = (c, label) => {
        const v = s[c];
        if (v.arrived) return `<div class="pr ${c}"><span class="pl">${label}</span><i class="bar"><u data-p="1"></u></i><b>도달</b></div><div class="eta">${kst(v.arrived)}</div>`;
        return `<div class="pr ${c}"><span class="pl">${label}</span><i class="bar"><u data-p="${v.p}"></u></i><b>${pct(v.p)}</b></div>`
          + (v.p >= .2 ? `<div class="eta">약 ${Math.max(0, v.hours)}시간 뒤 · ${kst(v.eta)} 도착 예상</div>` : "");
      };
      const L1 = LV[idx];
      li.style.animationDelay = `${liveN * 90}ms`;
      li.innerHTML = `<div class="s-head"><b>${name(s)}</b>${kt != null || s.grade != null ? `<span class="lv" style="--c:${L1.color};--ci:${L1.ink}">${lvName(kt, s.grade)}</span>` : ""}</div>`
        + (kt != null || hpa != null ? `<div class="vit">${kt != null ? `풍속 <b>${kt}</b>kt` : ""}${kt != null && hpa != null ? " · " : ""}${hpa != null ? `기압 <b>${hpa}</b>hPa` : ""}</div>` : "")
        + row("korea", "한국") + row("japan", "일본");
      list.appendChild(li); liveN++;
    } else {
      L.marker([last[1], last[2]], { interactive: false, keyboard: false, icon: L.divIcon({ className: `tc-icon ${cls}`, iconSize: [18, 18], iconAnchor: [9, 9], html: `<div class="tc-end" style="color:${LV[idx].color}"><i></i><div class="tc-label">${name(s)}</div></div>` }) }).addTo(layer);
      const hit = [s.korea.arrived ? `한국 ${kst(s.korea.arrived)} 도달` : "", s.japan.arrived ? `일본 ${kst(s.japan.arrived)} 도달` : ""].filter(Boolean).join(" · ");
      li.innerHTML = `<b>${name(s)}</b><span>${kst(s.start)} – ${kst(s.end)}</span>${hit ? `<em>${hit}</em>` : ""}`;
      goneList.appendChild(li); goneN++;
    }
    bind(li, n, s);
  });
  if (!liveN) list.innerHTML = `<li class="empty" style="cursor:default;animation:none">활동 중인 태풍 없음</li>`;
  if (goneN) { $("goneBox").hidden = false; $("goneN").textContent = `(${goneN})`; }

  // 첫 로드: 넓게 보다가 활동 중인 태풍 쪽으로 한 번 부드럽게 이동
  if (bounds.length) {
    const target = L.latLngBounds([...bounds, ...KOREA_CITIES]);
    if (REDUCED) map.fitBounds(target, pad());
    else setTimeout(() => map.flyToBounds(target, { ...pad(), duration: 2.4, easeLinearity: .2 }), 700);
  }
  setTimeout(() => list.querySelectorAll(".bar u").forEach(u => setBar(u, +u.dataset.p)), 500);
}

/* ---------- 직접 예측 ---------- */
function renderSim(model) {
  const map = baseMap("simMap", [28, 130], 4);
  drawCities(map);
  const form = $("simForm");
  form.date.value = new Date().toISOString().slice(0, 10);
  let pos = [24, 128];
  const marker = L.marker(pos, { icon: tcIcon({ size: 44, idx: 3, live: false }), draggable: true, zIndexOffset: 1000 }).addTo(map);
  const path = L.polyline([], { color: "#0a1f33", weight: 2.5, dashArray: "6 8", opacity: .7, lineCap: "round", interactive: false }).addTo(map);

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
    [["korea", "simK", "simKeta", "simKbar"], ["japan", "simJ", "simJeta", "simJbar"]].forEach(([c, id, eid, bid]) => {
      const r = predict(model, c, f);
      $(id).textContent = pct(r.p);
      $(eid).textContent = r.p >= .2 ? `약 ${Math.max(0, Math.round(r.hours))}시간 뒤` : "";
      setBar($(bid), r.p);
    });
    const ahead = [pos];
    for (let h = 12; h <= 48; h += 12) ahead.push(destPoint(pos, s.heading, s.speed * h));
    path.setLatLngs(ahead);
    $("simPos").textContent = `${pos[0].toFixed(1)}°N ${pos[1].toFixed(1)}°E · 서울까지 ${Math.round(f.dist_seoul).toLocaleString()}km`;
  }
  map.on("click", e => { pos = [e.latlng.lat, e.latlng.lng]; marker.setLatLng(pos); update(); });
  marker.on("drag", e => { const l = e.target.getLatLng(); pos = [l.lat, l.lng]; update(); });
  form.addEventListener("input", update);
  seen(form.closest(".sim-panel"), update, .1);
}

/* ---------- 예측 시점 비교: 숫자 대결 ---------- */
function renderVis(root, D) {
  if (!root) return;
  const pair = (lab, unit, L, R) => {
    const m = Math.max(L.v, R.v) || 1;
    const side = (s, cls) => `<div class="side ${cls}" style="--c:${s.c}"><span class="nm">${s.n}</span><b class="num">${s.v}<small>${unit}</small></b><span class="va"><i style="--h:${(s.v / m * 100).toFixed(1)}"></i></span></div>`;
    return `<section class="vs-row"><h4 class="vs-lab">${lab}</h4><div class="vs-cols">${side(L, "l")}${side(R, "r")}</div></section>`;
  };
  let rows = "";
  if (D.natK != null && D.natJ != null) rows += pair("발생에서 도착까지", "시간", { n: "한국", v: Math.round(D.natK), c: "var(--korea)" }, { n: "일본", v: Math.round(D.natJ), c: "var(--japan)" });
  if (D.turnK != null && D.turnJ != null) rows += pair("도착 직전 48시간, 진행 방향이 꺾인 각도", "°", { n: "한국", v: D.turnK, c: "var(--korea)" }, { n: "일본", v: D.turnJ, c: "var(--japan)" });
  if (D.fc != null && D.ours != null) rows += pair("도착 전 첫 경보 시점", "시간", { n: "기상청 예보", v: Math.round(D.fc), c: "var(--warn)" }, { n: "우리 모델", v: Math.round(D.ours), c: "var(--frost)" });
  if (D.gap != null) rows += `<section class="vs-row gp"><h4 class="vs-lab">기상청 첫 발표가 일본 기록보다 늦은 시간</h4><div class="gp-in"><span class="nm" style="--c:var(--japan)">일본 첫 기록</span><div class="gp-mid"><b class="num">${D.gap}<small>시간</small></b><i class="gp-ln"></i></div><span class="nm" style="--c:var(--warn)">기상청 첫 발표</span></div></section>`;
  const note = rows ? `<p class="cmp-note">일본 기록은 열대저압부 단계부터 시작될 수 있음${D.n != null ? ` · 기상청 첫 발표 비교 ${D.n}개 태풍` : ""}</p>` : "";
  root.innerHTML = `<div class="vs">${rows}</div>${note}`;
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
  const ours = kma && kma.our_model_korea_full && has(kma.our_model_korea_full.median_warning_lead_h) ? kma.our_model_korea_full.median_warning_lead_h : warn("korea");
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

  // 요인: 가는 막대 표
  const fi = a.feature_importance;
  const keys = [...new Set([...Object.keys(fi.korea), ...Object.keys(fi.japan)])]
    .sort((x, y) => (fi.korea[y] || 0) - (fi.korea[x] || 0)).slice(0, 6);
  const mx = Math.max(...keys.map(k => Math.max(fi.korea[k] || 0, fi.japan[k] || 0))) || 1;
  $("factors").innerHTML = `<div class="fac-head"><span style="flex:1">예측에 크게 작용한 요인</span><span>한국</span><span>일본</span></div>`
    + keys.map(k => `<div class="fac"><span>${FEATURE_KO[k] || k}</span><div class="bars"><i class="bar korea"><u data-p="${(fi.korea[k] || 0) / mx}" style="background:${C.korea}"></u></i><i class="bar japan"><u data-p="${(fi.japan[k] || 0) / mx}" style="background:${C.japan}"></u></i></div></div>`).join("");
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
  $("metrics").innerHTML = `<table><thead><tr><th>검증 2015년~</th><th>AUC</th><th>도착 시간 오차</th><th>첫 경보 중앙값</th></tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

/* ---------- 지난 태풍 ---------- */
function renderHistory(history) {
  const sel = $("histSel"), scrub = $("scrub"), play = $("play");
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
      draw(0, "rgba(255,122,107,.75)", [4, 4]);
      if (cur) draw(hoursTo(cur.track[+scrub.value]), C.ink, []);
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
    $("scrubTime").textContent = `${kst(t[0])} · 한국 ${pct(t[5] || 0)}`;
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
        scales: {
          x: { type: "linear", bounds: "data", ticks: { callback: v => v === 0 ? "도착" : `${v > 0 ? "+" : ""}${v}h`, stepSize: 24, maxRotation: 0, autoSkip: true } },
          y: { min: 0, max: 100, ticks: { callback: v => v + "%" } },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { title: i => `도착 ${i[0].parsed.x}시간`, label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
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
  seen($("histMap"), () => load(0), .1);
}

/* ---------- 통계 ---------- */
function renderStats(stats) {
  const mo = stats.korea_by_month, mmax = Math.max(...mo) || 1, mpeak = mo.indexOf(Math.max(...mo));
  $("monthList").innerHTML = mo.map((v, i) => `<div class="mrow${i === mpeak ? " peak" : ""}"><span>${i + 1}월</span><i class="bar"><u data-p="${v / mmax}"></u></i><b>${v}</b></div>`).join("");
  seen($("monthList"), () => $("monthList").querySelectorAll(".bar u").forEach((u, i) => setTimeout(() => setBar(u, +u.dataset.p), REDUCED ? 0 : i * 45)), .2);

  const years = Object.keys(stats.korea_by_year), yv = years.map(y => stats.korea_by_year[y]), ymax = Math.max(...yv);
  seen($("yearChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
    new Chart($("yearChart"), {
      type: "bar",
      data: { labels: years, datasets: [{ data: yv, backgroundColor: yv.map(v => v === ymax ? C.korea : C.ink), borderRadius: 0, barPercentage: .62, categoryPercentage: 1 }] },
      options: {
        maintainAspectRatio: false, animation: { duration: REDUCED ? 0 : 1400, easing: "easeOutCubic" },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.parsed.y}개` } } },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } }, y: { ticks: { stepSize: 1 } } },
      },
    });
  }, .2);
}

/* ---------- 서리 ---------- */
function renderFrost(fr) {
  const form = $("frostForm");
  const COL = ["#9fd8ea", "#ff7a6b", "#63aeff", "#f6b94e", "#5fd6a0", "#c39bff"];
  const ST = fr.stations;
  ST.forEach(s => form.stn.add(new Option(s.name, s.stn)));
  const y = new Date().getFullYear();
  form.date.value = `${y}-11-15`;
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
    $("frostP").textContent = pct(p);
    setBar($("frostBar"), p);
  }
  form.addEventListener("input", update);
  seen(form.closest(".sim-panel"), update, .1);

  seen($("frostChart").closest(".chart-box"), () => {
    if (!window.Chart) return;
    const ch = new Chart($("frostChart"), {
      type: "line", plugins: [wipePlugin, endLabels],
      data: {
        labels: [...Array(12)].map((_, i) => `${i + 1}월`),
        datasets: ST.map((s, i) => ({
          label: s.name, data: s.rate.map(v => v * 100), borderColor: COL[i % 6], backgroundColor: COL[i % 6],
          tension: .35, borderWidth: i === 0 ? 2.8 : 1.8, pointRadius: 0, pointHoverRadius: 4,
        })),
      },
      options: {
        maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
        layout: { padding: { right: 52, top: 6 } },
        scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
      },
    });
    runWipe(ch, 2000);
  }, .25);

  const mmdd = t => { const [m, d] = String(t).split("-"); return `${+m}월 ${+d}일`; };
  $("frostStations").innerHTML = `<table><thead><tr><th>지점</th><th>첫서리 평균</th><th>끝서리 평균</th></tr></thead><tbody>`
    + ST.map((s, i) => `<tr><td><i style="background:${COL[i % 6]}"></i>${s.name}</td><td>${mmdd(s.first_avg)}</td><td>${mmdd(s.last_avg)}</td></tr>`).join("") + `</tbody></table>`;
  const m = fr.metrics || {};
  const cols = [];
  if (m.auc != null) cols.push(["AUC", m.auc]);
  if (m.f1 != null) cols.push(["F1", m.f1]);
  if (m.persistence_f1 != null) cols.push(["F1 단순 지속", m.persistence_f1]);
  $("frostMetrics").innerHTML = cols.length ? `<table><thead><tr><th>검증</th>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody><tr><td>서리 모델</td>${cols.map(c => `<td>${c[1]}</td>`).join("")}</tr></tbody></table>` : "";
}

/* ---------- 시작 ---------- */
document.querySelectorAll(".sec-head").forEach(el => seen(el, () => el.classList.add("in"), .9));

const FAIL = `<p class="empty">자료를 불러오지 못했습니다</p>`;
function unavailable(id) {
  const w = document.querySelector(`#${id} .wrap`);
  if (!w) return;
  [...w.children].filter(c => !c.classList.contains("sec-head")).forEach(c => c.remove());
  w.insertAdjacentHTML("beforeend", FAIL);
}
function guard(id, data, fn) {
  try {
    if (!data) throw new Error("no data");
    fn(data);
  } catch (e) {
    console.warn(id, e);
    if (id === "now") { $("nowList").innerHTML = `<li class="empty" style="cursor:default;animation:none">자료를 불러오지 못했습니다</li>`; }
    else unavailable(id);
  }
}

Promise.all(["latest", "stats", "history", "model", "kma_lead", "frost"].map(n => soft(`data/${n}.json`)))
  .then(([latest, stats, history, model, kma, frost]) => {
    guard("now", latest && latest.storms && latest, renderNow);
    guard("history", history && history.length && history, renderHistory);
    guard("compare", stats && stats.analysis && stats, s => renderCompare(s, kma));
    guard("sim", model, renderSim);
    if (frost && frost.stations && frost.stations.length && frost.clf) guard("frost", frost, renderFrost);
    else { $("frost").hidden = true; $("navFrost").hidden = true; }
    guard("stats", stats && stats.korea_by_month && stats.korea_by_year && stats, renderStats);
  });
