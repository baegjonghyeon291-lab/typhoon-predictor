// 학습한 Gradient Boosting 트리를 브라우저에서 계산 (typhoon/features.py와 같은 특성)
const SEOUL = [37.57, 126.98], TOKYO = [35.68, 139.69];
const rad = d => d * Math.PI / 180, deg = r => r * 180 / Math.PI;

function haversine(a1, o1, a2, o2) {
  const [l1, g1, l2, g2] = [a1, o1, a2, o2].map(rad);
  const a = Math.sin((l2 - l1) / 2) ** 2 + Math.cos(l1) * Math.cos(l2) * Math.sin((g2 - g1) / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

function bearing(a1, o1, a2, o2) {
  const [l1, g1, l2, g2] = [a1, o1, a2, o2].map(rad);
  const x = Math.sin(g2 - g1) * Math.cos(l2);
  const y = Math.cos(l1) * Math.sin(l2) - Math.sin(l1) * Math.cos(l2) * Math.cos(g2 - g1);
  return (deg(Math.atan2(x, y)) + 360) % 360;
}

function destination(lat, lon, brg, km) {
  const d = km / 6371, b = rad(brg), l1 = rad(lat), g1 = rad(lon);
  const l2 = Math.asin(Math.sin(l1) * Math.cos(d) + Math.cos(l1) * Math.sin(d) * Math.cos(b));
  const g2 = g1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(l1), Math.cos(d) - Math.sin(l1) * Math.sin(l2));
  return [deg(l2), deg(g2)];
}

function makeFeatures(s) {
  const p6 = destination(s.lat, s.lon, (s.heading + 180) % 360, s.speed * 6);
  const p24 = destination(s.lat, s.lon, (s.heading + 180) % 360, s.speed * 24);
  const d = new Date(s.date + "T00:00:00Z");
  const doy = Math.floor((d - Date.UTC(d.getUTCFullYear(), 0, 0)) / 864e5);
  return {
    LAT: s.lat, LON: s.lon, wind: s.wind, pres: s.pres, dwind_24: s.dwind,
    dlat_6: s.lat - p6[0], dlon_6: s.lon - p6[1], dlat_24: s.lat - p24[0], dlon_24: s.lon - p24[1],
    speed_kmh: s.speed, heading: s.heading, age_h: s.age * 24, month: d.getUTCMonth() + 1, doy,
    dist_seoul: haversine(s.lat, s.lon, ...SEOUL), bearing_seoul: bearing(s.lat, s.lon, ...SEOUL),
    dist_tokyo: haversine(s.lat, s.lon, ...TOKYO), bearing_tokyo: bearing(s.lat, s.lon, ...TOKYO),
  };
}

function runTrees(m, x) {
  let raw = m.base;
  for (const nodes of m.trees) {
    let n = nodes[0];
    while (n.length > 1) {
      const v = x[n[0]];
      n = nodes[Number.isNaN(v) ? (n[2] ? n[3] : n[4]) : (v <= n[1] ? n[3] : n[4])];
    }
    raw += n[0];
  }
  return raw;
}

function predict(model, country, feats) {
  const x = model.features.map(f => (feats[f] ?? NaN));
  const m = model[country];
  return { p: 1 / (1 + Math.exp(-runTrees(m.clf, x))), hours: runTrees(m.reg, x) };
}

if (typeof module !== "undefined") module.exports = { makeFeatures, predict, runTrees };
