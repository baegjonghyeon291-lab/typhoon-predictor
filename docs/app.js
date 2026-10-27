const C = { korea: "#ff5d6c", japan: "#4cc9f0", muted: "#8592a8", grid: "rgba(255,255,255,.06)" };
const KOREA_CITIES = [[37.57, 126.98], [35.18, 129.08], [35.16, 126.85], [33.50, 126.53], [37.75, 128.90], [36.35, 127.38]];
const FEATURE_KO = {
  doy: "날짜(계절)", month: "월", LAT: "위도", LON: "경도", wind: "최대풍속", pres: "중심기압", dwind_24: "하루 풍속 변화",
  dlat_6: "6시간 남북 이동", dlon_6: "6시간 동서 이동", dlat_24: "24시간 남북 이동", dlon_24: "24시간 동서 이동",
  speed_kmh: "이동 속도", heading: "진행 방향", age_h: "발생 후 시간",
  dist_seoul: "서울까지 거리", bearing_seoul: "서울 방위", dist_tokyo: "도쿄까지 거리", bearing_tokyo: "도쿄 방위",
};

Chart.defaults.color = C.muted;
Chart.defaults.font.family = "'Pretendard Variable', Pretendard, system-ui, sans-serif";
Chart.defaults.borderColor = C.grid;

const getJSON = p => fetch(p, { cache: "no-cache" }).then(r => r.json());
const utc = t => new Date(t + ":00:00Z");
function kst(t, withYear = false) {
  const d = new Date(utc(t).getTime() + 9 * 36e5);
  const s = `${d.getUTCMonth() + 1}월 ${d.getUTCDate()}일 ${String(d.getUTCHours()).padStart(2, "0")}시`;
  return withYear ? `${d.getUTCFullYear()}년 ${s}` : s;
}
const pct = p => `${Math.round(p * 100)}%`;

function probColor(p) {
  const stops = [[0, [91, 112, 150]], [0.35, [255, 183, 3]], [1, [255, 77, 90]]];
  for (let i = 1; i < stops.length; i++) {
    if (p <= stops[i][0]) {
      const [a, ca] = stops[i - 1], [b, cb] = stops[i], t = (p - a) / (b - a);
      return `rgb(${ca.map((v, k) => Math.round(v + (cb[k] - v) * t)).join(",")})`;
    }
  }
  return "rgb(255,77,90)";
}

function baseMap(id, center = [30, 130], zoom = 4) {
  const map = L.map(id, { zoomControl: true, scrollWheelZoom: false, worldCopyJump: true }).setView(center, zoom);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
    attribution: "&copy; Esri", maxZoom: 10,
  }).addTo(map);
  return map;
}

const tcIcon = (size = 34, color = C.korea) => L.divIcon({
  className: "tc-icon", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
  html: `<svg viewBox="0 0 40 40" style="color:${color}"><path d="M20 20m-3 0a3 3 0 1 0 6 0a6 6 0 1 0-12 0a9 9 0 1 0 18 0a12 12 0 1 0-24 0" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>`,
});

// 경로를 확률 색으로 칠하며 점점 그려지는 애니메이션
function drawTrack(layer, track, idx = 5, { animate = true, weight = 3 } = {}) {
  const segs = [];
  for (let i = 1; i < track.length; i++) {
    const a = track[i - 1], b = track[i];
    segs.push(L.polyline([[a[1], a[2]], [b[1], b[2]]], { color: probColor(b[idx] ?? 0), weight, opacity: .95, lineCap: "round" }));
  }
  if (!animate) { segs.forEach(s => s.addTo(layer)); return; }
  let i = 0;
  const step = () => { if (i < segs.length) { segs[i++].addTo(layer); setTimeout(step, 900 / segs.length); } };
  step();
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
  document.getElementById("asof").textContent = `자료 기준 ${kst(latest.data_until, true)}`;
  const map = baseMap("nowMap", [28, 132], 4);
  KOREA_CITIES.forEach(c => L.circle(c, { radius: 200000, color: C.korea, weight: 0, fillOpacity: .05 }).addTo(map));
  const list = document.getElementById("nowList");
  const bounds = [];
  latest.storms.forEach((s, n) => {
    const live = s.end === latest.data_until;
    const layer = L.layerGroup().addTo(map);
    drawTrack(layer, s.track);
    const last = s.track[s.track.length - 1];
    if (live) bounds.push(...s.track.map(t => [t[1], t[2]]));
    L.marker([last[1], last[2]], { icon: live ? tcIcon(36) : L.divIcon({ className: "", html: "" }) })
      .bindTooltip(s.name, { direction: "top", offset: [0, -14] }).addTo(layer);

    const card = document.createElement("article");
    card.className = "storm";
    card.style.animationDelay = `${n * 80}ms`;
    const prob = (c, label) => {
      const v = s[c];
      if (v.arrived) return `<div class="prob ${c}"><span>${label}</span><strong>도달</strong><em>${kst(v.arrived)}</em></div>`;
      if (!live) return `<div class="prob ${c}"><span>${label}</span><strong>–</strong></div>`;
      return `<div class="prob ${c}"><span>${label} 5일 내</span><strong>${pct(v.p)}</strong>${v.p >= .2 ? `<em>약 ${Math.max(0, v.hours)}시간 뒤 · <span class="nw">${kst(v.eta)}</span></em>` : ""}<div class="meter"><i data-w="${v.p * 100}"></i></div></div>`;
    };
    card.innerHTML = `<div class="storm-head"><b>${s.name === "UNNAMED" ? "이름 없음" : s.name}</b><span class="badge ${live ? "live" : ""}">${live ? "활동 중" : "소멸"}</span></div>
      <div class="probs">${prob("korea", "한국")}${prob("japan", "일본")}</div>
      <div class="storm-time">${kst(s.start)} – ${kst(s.end)}${last[3] ? ` · ${last[3]}kt` : ""}${last[4] ? ` · ${last[4]}hPa` : ""}</div>`;
    card.addEventListener("click", () => map.flyToBounds(s.track.map(t => [t[1], t[2]]), { padding: [40, 40], duration: 1 }));
    list.appendChild(card);
  });
  if (bounds.length) map.fitBounds([...bounds, ...KOREA_CITIES], { padding: [30, 30], maxZoom: 5 });
  setTimeout(() => list.querySelectorAll(".meter i").forEach(i => i.style.width = i.dataset.w + "%"), 400);
}

/* 직접 예측 */
function renderSim(model) {
  const map = baseMap("simMap", [28, 130], 4);
  KOREA_CITIES.forEach(c => L.circle(c, { radius: 200000, color: C.korea, weight: 0, fillOpacity: .06 }).addTo(map));
  const form = document.getElementById("simForm");
  form.date.value = new Date().toISOString().slice(0, 10);
  let pos = [24, 128];
  const marker = L.marker(pos, { icon: tcIcon(40), draggable: true }).addTo(map);
  const path = L.polyline([], { color: "#fff", weight: 2, dashArray: "6 8", opacity: .6 }).addTo(map);
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
  const destinationLL = (p, brg, km) => {
    const R = 6371, d = km / R, b = brg * Math.PI / 180, l1 = p[0] * Math.PI / 180, g1 = p[1] * Math.PI / 180;
    const l2 = Math.asin(Math.sin(l1) * Math.cos(d) + Math.cos(l1) * Math.sin(d) * Math.cos(b));
    const g2 = g1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(l1), Math.cos(d) - Math.sin(l1) * Math.sin(l2));
    return [l2 * 180 / Math.PI, g2 * 180 / Math.PI];
  };
  map.on("click", e => { pos = [e.latlng.lat, e.latlng.lng]; marker.setLatLng(pos); update(); });
  marker.on("drag", e => { const l = e.target.getLatLng(); pos = [l.lat, l.lng]; update(); });
  form.addEventListener("input", update);
  reveal(document.querySelector(".sim-panel"), update);
}

/* 한국 vs 일본 */
function renderCompare(stats) {
  const a = stats.analysis, m = stats.metrics;
  const nums = [
    ["natK", a.natural_lead.korea.median_h], ["natJ", a.natural_lead.japan.median_h],
    ["warnK", a.model_warning_lead.korea.median_warning_lead_h], ["warnJ", a.model_warning_lead.japan.median_warning_lead_h],
    ["turnK", a.track_turn_last_48h.korea.median_abs_turn_deg, 1], ["turnJ", a.track_turn_last_48h.japan.median_abs_turn_deg, 1],
    ["agency", a.agency_tracking_start.median_kma_minus_jma_h],
  ];
  nums.forEach(([id, v, d]) => {
    const el = document.getElementById(id);
    el.textContent = "0";
    reveal(el.closest(".stat"), () => countUp(el, v, d || 0));
  });
  document.getElementById("agencyRatio").textContent =
    `${a.agency_tracking_start.storms}개 태풍 중 ${Math.round(a.agency_tracking_start.kma_later_ratio * 100)}%`;

  const leads = [24, 48, 72, 96, 120];
  reveal(document.getElementById("leadChart").closest(".card"), () => new Chart(document.getElementById("leadChart"), {
    type: "line",
    data: {
      labels: leads.map(h => `${h}시간 전`),
      datasets: ["korea", "japan"].map(c => ({
        label: c === "korea" ? "한국" : "일본",
        data: leads.map(h => a.model_warning_lead[c][`warned_${h}h_before`] * 100),
        borderColor: C[c], backgroundColor: C[c] + "22", fill: true, tension: .35, pointRadius: 4, borderWidth: 2.5,
      })),
    },
    options: {
      maintainAspectRatio: false, animation: { duration: 1600 },
      scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } }, x: { grid: { display: false } } },
      plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label} ${c.parsed.y.toFixed(0)}%` } } },
    },
  }));

  const keys = [...new Set([...Object.keys(a.feature_importance.korea), ...Object.keys(a.feature_importance.japan)])]
    .sort((x, y) => (a.feature_importance.korea[y] || 0) - (a.feature_importance.korea[x] || 0)).slice(0, 7);
  reveal(document.getElementById("impChart").closest(".card"), () => new Chart(document.getElementById("impChart"), {
    type: "bar",
    data: {
      labels: keys.map(k => FEATURE_KO[k] || k),
      datasets: ["korea", "japan"].map(c => ({
        label: c === "korea" ? "한국" : "일본", data: keys.map(k => a.feature_importance[c][k] || 0),
        backgroundColor: C[c], borderRadius: 4, barPercentage: .8,
      })),
    },
    options: {
      indexAxis: "y", maintainAspectRatio: false, animation: { duration: 1600 },
      scales: { x: { ticks: { display: false } }, y: { grid: { display: false } } },
    },
  }));

  document.getElementById("metrics").innerHTML =
    `<span>검증 2015년~</span>` +
    `<span>한국 AUC<b>${m.korea.auc}</b></span><span>한국 도착 시간 오차<b>${m.korea.arrival_mae_h}시간</b></span>` +
    `<span>일본 AUC<b>${m.japan.auc}</b></span><span>일본 도착 시간 오차<b>${m.japan.arrival_mae_h}시간</b></span>`;
}

/* 지난 태풍 */
function renderHistory(history) {
  const sel = document.getElementById("histSel"), scrub = document.getElementById("scrub"), play = document.getElementById("play");
  history.forEach((s, i) => sel.add(new Option(`${s.season} ${s.name}`, i)));
  const map = baseMap("histMap", [30, 128], 4);
  KOREA_CITIES.forEach(c => L.circle(c, { radius: 200000, color: C.korea, weight: 0, fillOpacity: .07 }).addTo(map));
  const layer = L.layerGroup().addTo(map);
  const cursor = L.marker([0, 0], { icon: tcIcon(38) });
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
      draw(0, "rgba(255,93,108,.6)", [4, 4]);
      if (cur) draw(hoursTo(cur.track[+scrub.value]), "#fff", []);
    },
  };
  const hoursTo = t => (utc(t[0]) - utc(cur.arrival)) / 36e5;

  function setFrame(i) {
    const t = cur.track[i];
    cursor.setLatLng([t[1], t[2]]);
    document.getElementById("scrubTime").textContent = `${kst(t[0])} · 한국 ${pct(t[5] || 0)}`;
    chart && chart.update("none");
  }

  function load(i) {
    stop();
    cur = history[i];
    layer.clearLayers();
    drawTrack(layer, cur.track, 5, { weight: 4 });
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

document.querySelectorAll(".reveal").forEach(el => reveal(el));

Promise.all(["data/latest.json", "data/stats.json", "data/history.json", "data/model.json"].map(getJSON))
  .then(([latest, stats, history, model]) => {
    renderNow(latest);
    renderSim(model);
    renderCompare(stats);
    renderHistory(history);
    renderStats(stats);
  });
