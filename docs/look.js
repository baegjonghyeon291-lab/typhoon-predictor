/* 디자인(파트별 시안 합본) 훅: 디자인 훅 인라인 스크립트 다음, app.js 전에 로드 */
/* p1 v08 가운데 축: 한국 확률 | 태풍 이름 | 일본 확률을 좌우 대칭으로, 그 아래 넓은 지도, 맨 아래 가운데 좁은 비교표 */
window.PAD_L = 90;
window.KEYFIGS = (latest, live, u) => {
  const box = document.getElementById("keyfigs");
  if (!box) return;
  if (!live.length) { box.innerHTML = `<h1 class="cv-name cv-quiet">활동 중인 태풍 없음</h1>`; return; }
  const [s, ...rest] = live, last = s.track[s.track.length - 1];
  const big = t => t.replace(/(\d+)(%.*)$/, "$1<i>$2</i>");
  const eta = v => v.p >= .2 && v.eta ? `<em>${u.kst(v.eta)} 도착 예상 · ${u.leftText(v.eta)}</em>` : "";
  const val = c => s[c].arrived ? `<b class="in">진입</b><em>${u.kst(s[c].arrived)}</em>` : `<b>${big(u.pctCard(s[c].p))}</b>${eta(s[c])}`;
  box.innerHTML =
    `<div class="cv-p">`
    + `<div class="korea"><span>한국</span>${val("korea")}</div>`
    + `<div class="cv-id"><p class="cv-lv">${u.lvName(last[3] ?? null, s.grade)}</p><h1 class="cv-name">${u.stormName(s)}</h1><p class="cv-en">${s.name}</p>`
    + `<p class="cv-vit">${last[3] != null ? `<span><b>${last[3]}</b>kt</span>` : ""}${last[4] != null ? `<span><b>${last[4]}</b>hPa</span>` : ""}</p></div>`
    + `<div class="japan"><span>일본</span>${val("japan")}</div>`
    + `</div>`
    + (rest.length ? `<ul class="cv-more">${rest.map(r => `<li><b>${u.stormName(r)}</b><span>한국 ${r.korea.arrived ? "진입" : u.pctCard(r.korea.p)}</span><span>일본 ${r.japan.arrived ? "진입" : u.pctCard(r.japan.p)}</span></li>`).join("")}</ul>` : "");
};
document.addEventListener("DOMContentLoaded", () => setTimeout(() => window.dispatchEvent(new Event("resize")), 300));

/* p2 v06 · 표목록: 목록 항목을 표의 칸으로 나눔. 그래프는 계단선, 흑백 */
(() => {
  window.HIST_DATA = (data) => {
    const [kr, jp] = data.datasets;
    Object.assign(kr, { stepped: true, borderColor: "#0b0b0b", backgroundColor: "transparent", fill: false, borderWidth: 2 });
    Object.assign(jp, { stepped: true, borderColor: "#9a9a96", backgroundColor: "transparent", fill: false, borderWidth: 1.25 });
  };
  const tune = fn => { let n = 0; const t = setInterval(() => { const c = window.Chart && Chart.getChart("histChart"); if (c) { clearInterval(t); fn(c); c.update("none"); } else if (++n > 720) clearInterval(t); }, 250); };
  tune(c => {
    const s = c.options.scales, f = { family: "'Pretendard Variable', Pretendard, sans-serif", size: 10.5 };
    s.x.grid = { color: "#d6d6d2" }; s.x.ticks.color = "#6f6f6c"; s.x.ticks.font = f; s.x.title.color = "#6f6f6c"; s.x.title.font = { family: "'Pretendard Variable', sans-serif", size: 11 };
    s.y.grid = { color: "#d6d6d2" }; s.y.ticks.color = "#6f6f6c"; s.y.ticks.font = f;
    s.x.border = { color: "#0b0b0b" }; s.y.border = { color: "#0b0b0b" };
  });
  const span = (cls, kids) => { const s = document.createElement("span"); s.className = cls; s.append(...kids); return s; };
  document.addEventListener("DOMContentLoaded", () => {
    const list = document.getElementById("histList");
    const head = document.createElement("div"); head.className = "thead";
    ["", "이름", "연도", "단계", "한국까지 거리", "재산피해"].forEach(t => { const s = document.createElement("span"); s.textContent = t; head.append(s); });
    list.parentNode.insertBefore(head, list);
    // 목록이 다시 그려질 때마다 항목을 칸으로 나눈다(버튼과 data-i 는 그대로)
    const fix = () => list.querySelectorAll("button[data-i]:not([data-t])").forEach(b => {
      b.dataset.t = "1";
      const name = b.querySelector(".h-top b"), lv = b.querySelector(".lv"), sub = b.querySelector(".h-sub")?.textContent || "";
      const p = sub.split(" · "), ym = (p[0] || "").match(/(\d{4})년\s*(\d+)월/), km = (p[1] || "").match(/최단\s*([\d,]+km)/);
      const t = x => document.createTextNode(x);
      b.replaceChildren(
        span("c-nm", [t(name ? name.textContent : "")]),
        span("c-yr", [t(ym ? `${ym[1]}.${ym[2].padStart(2, "0")}` : "")]),
        span("c-lv", lv ? [lv] : []),
        span("c-km", [t(km ? km[1] : (p[1] || ""))]),
        span("c-dm", [t(p.slice(2).join(" · ") || "—")]),
      );
    });
    new MutationObserver(fix).observe(list, { childList: true });
    fix();
    setTimeout(() => window.dispatchEvent(new Event("resize")), 300);
  });
})();

/* p3 전향 복기 시안 — 공통: review.json·history.json 을 renderReview 와 같은 기준으로 정리 */
const P3 = (() => {
  const SRC = [["kma", "기상청"], ["own", "자체 경로"], ["clf", "자체 분류"]];
  const NAME = Object.fromEntries(SRC);
  const KO = { CHABA: "차바", "CHAN-HOM": "찬홈", CHANTHU: "찬투", DANAS: "다나스", FRANCISCO: "프란시스코", GONI: "고니", HAGUPIT: "하구핏", HAISHEN: "하이선", HINNAMNOR: "힌남노", JANGMI: "장미", JONGDARI: "종다리", KHANUN: "카눈", "KONG-REY": "콩레이", LEEPI: "리피", LINGLING: "링링", MAYSAK: "마이삭", MERANTI: "므란티", MITAG: "미탁", NAMTHEUN: "남테운", OMAIS: "오마이스", PRAPIROON: "쁘라삐룬", PULASAN: "풀라산", SOUDELOR: "사우델로르", SOULIK: "솔릭", TAPAH: "타파", TRASES: "뜨라세", CHAMPI: "참피", "IN-FA": "인파", KOGUMA: "고구마", HALOLA: "할롤라", NANMADOL: "난마돌", BAVI: "바비", AERE: "에어리", SONGDA: "송다", SHANSHAN: "산산" };
  const stormName = s => (s.name === "UNNAMED" || s.name === "NOT_NAMED") ? "이름 없음" : KO[s.name] || s.name;
  const kd = t => { const d = new Date(new Date(t + ":00:00Z").getTime() + 9 * 36e5); return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(), h: d.getUTCHours() }; };
  const median = a => { if (!a.length) return null; const b = a.slice().sort((x, y) => x - y), m = b.length >> 1; return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2; };
  // {t, lead_h} → 예측, null → 놓침, "none"·키 없음 → 자료 없음
  const cell = v => v === "none" || v === undefined ? { st: "na" } : (v && v.lead_h != null) ? { st: "hit", h: v.lead_h, t: v.t } : { st: "miss" };
  const txt = c => c.st === "hit" ? `${c.h}시간 전` : c.st === "miss" ? "놓침" : "자료 없음";
  const val = c => c.st === "hit" ? `<span class="v hit"><b>${c.h}</b>시간 전</span>` : `<span class="v ${c.st}">${txt(c)}</span>`;
  const SPAN = 120;
  function prep(review, history) {
    const by = Object.fromEntries(history.map(s => [s.sid, s]));
    const rows = Object.entries(review).filter(([sid, r]) => r.turn && by[sid]).sort((a, b) => b[1].turn.localeCompare(a[1].turn)).map(([sid, r]) => {
      const s = by[sid], v = {};
      SRC.forEach(([k]) => { v[k] = cell(r[k]); });
      const hs = SRC.filter(([k]) => v[k].st === "hit").map(([k]) => v[k].h);
      const best = hs.length ? Math.max(...hs) : null;
      const win = best == null ? [] : SRC.map(([k]) => k).filter(k => v[k].st === "hit" && v[k].h === best);
      const k = kd(r.turn);
      return { sid, name: stormName(s), season: s.season, turn: r.turn, k, day: `${k.m}월 ${k.d}일`, v, best, win };
    });
    const stats = {};
    SRC.forEach(([k]) => {
      const have = rows.filter(x => x.v[k].st !== "na"), hit = have.filter(x => x.v[k].st === "hit");
      const med = median(hit.map(x => x.v[k].h));
      stats[k] = { have: have.length, hit: hit.length, miss: have.length - hit.length, na: rows.length - have.length, med: med == null ? null : Math.round(med), first: rows.filter(x => x.win.includes(k)).length, solo: rows.filter(x => x.win.length === 1 && x.win[0] === k).length };
    });
    return { rows, stats };
  }
  function run(draw) {
    document.addEventListener("DOMContentLoaded", () => {
      const sec = document.getElementById("review"), wrap = sec && sec.querySelector(".wrap");
      if (!wrap) return;
      const get = n => fetch(`data/${n}.json`, { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null);
      Promise.all([get("review"), get("history")]).then(([rv, hs]) => {
        if (!rv || !hs || !hs.length || !document.body.contains(wrap)) return;
        const d = prep(rv, hs);
        if (!d.rows.length) return;
        const root = document.createElement("div");
        root.className = "p3x";
        const title = wrap.querySelector(".sec-title");
        if (title) title.after(root); else wrap.prepend(root);
        try { draw(root, d); } catch (e) { console.warn("p3", e); root.remove(); return; }
        sec.classList.add("p3-on");
        root.addEventListener("click", e => {
          const b = e.target.closest("[data-sid]");
          if (!b || !root.contains(b)) return;
          try { if (typeof histOpen === "function") histOpen(b.dataset.sid); } catch (_) { /* 지난 태풍 준비 전 */ }
        });
      });
    });
  }
  const nm = x => `<button type="button" class="nm" data-sid="${x.sid}">${x.name}</button>`;
  // 공통 페이지 넘김 버튼 묶음
  const pages = (n, per, cur) => { const c = Math.ceil(n / per); if (c < 2) return ""; return `<nav class="pg">${Array.from({ length: c }, (_, i) => `<button type="button" data-pg="${i}" aria-pressed="${i === cur}">${i + 1}</button>`).join("")}</nav>`; };
  return { SRC, NAME, SPAN, txt, val, run, nm, pages };
})();
/* v05 승패표: 기관끼리 1:1 세 쌍의 점수만 크게, 한 쌍을 고르면 그 대결 태풍을 6개씩 */
P3.run((root, d) => {
  const { NAME, nm, val, pages } = P3;
  const PER = 6;
  const lead = c => c.st === "hit" ? c.h : -1;          // 놓침은 어떤 예측보다 늦은 것으로
  const pairs = [["kma", "own"], ["kma", "clf"], ["own", "clf"]].map(([a, b]) => {
    const rows = d.rows.filter(x => x.v[a].st !== "na" && x.v[b].st !== "na").map(x => {
      const p = lead(x.v[a]), q = lead(x.v[b]);
      return { x, r: p > q ? a : q > p ? b : "=" };
    });
    return { a, b, rows, wa: rows.filter(o => o.r === a).length, wb: rows.filter(o => o.r === b).length, eq: rows.filter(o => o.r === "=").length };
  });
  let cur = 0, pg = 0;
  root.innerHTML = `<div class="pairs"></div><div class="det"></div>`;
  const P = root.querySelector(".pairs"), det = root.querySelector(".det");
  const draw = () => {
    P.innerHTML = pairs.map((p, i) => `<button type="button" class="pair" data-p="${i}" aria-pressed="${i === cur}">`
      + `<span class="side k-${p.a}"><span class="n">${NAME[p.a]}</span><b>${p.wa}</b></span><span class="colon">:</span>`
      + `<span class="side r k-${p.b}"><span class="n">${NAME[p.b]}</span><b>${p.wb}</b></span>`
      + `<span class="eq">같음 ${p.eq}</span></button>`).join("");
    const p = pairs[cur], show = p.rows.slice(pg * PER, pg * PER + PER);
    det.innerHTML = `<table><thead><tr><th>태풍</th><th class="k-${p.a}">${NAME[p.a]}</th><th class="k-${p.b}">${NAME[p.b]}</th><th>먼저</th></tr></thead><tbody>`
      + show.map(({ x, r }) => `<tr><td>${nm(x)}<span class="dt">${x.k.y}</span></td>`
        + `<td class="k-${p.a}${r === p.a ? " w" : ""}">${val(x.v[p.a])}</td><td class="k-${p.b}${r === p.b ? " w" : ""}">${val(x.v[p.b])}</td>`
        + `<td class="res${r === "=" ? "" : ` k-${r}`}">${r === "=" ? "같음" : NAME[r]}</td></tr>`).join("")
      + `</tbody></table>${pages(p.rows.length, PER, pg)}`;
  };
  root.addEventListener("click", e => {
    const b = e.target.closest("[data-p]"), g = e.target.closest("[data-pg]");
    if (b) { cur = +b.dataset.p; pg = 0; draw(); } else if (g) { pg = +g.dataset.pg; draw(); }
  });
  draw();
});

/* p4 v06 야간: 어두운 판, 비교 그래프를 면 그래프(단색 면 + 선)로 */
window.LEAD_CHART = ({ sets, leads }) => {
  const F = "'Pretendard Variable', Pretendard, sans-serif", K = "'Pretendard Variable', Pretendard, sans-serif";
  const n = leads.length, order = leads.map((_, i) => n - 1 - i);
  const ds = sets.map(d => {
    const me = d.label === "자체 모델";
    return {
      label: d.label, data: order.map(i => d.data[i]), tension: 0,
      borderColor: me ? "#ff4d5a" : "#9a9a9a", borderWidth: me ? 2.5 : 1.5, borderDash: me ? [] : [3, 3],
      backgroundColor: me ? "rgba(255,77,90,.16)" : "rgba(255,255,255,.06)", fill: "origin",
      pointRadius: me ? 3.5 : 3, pointHoverRadius: 6, pointBackgroundColor: me ? "#ff4d5a" : "#121212", pointBorderColor: me ? "#ff4d5a" : "#9a9a9a", pointBorderWidth: 1.5,
    };
  });
  const tags = {
    id: "tags",
    afterDatasetsDraw(c) {
      const ctx = c.ctx, a = c.chartArea;
      ctx.save(); ctx.textAlign = "center"; ctx.textBaseline = "bottom";
      c.data.datasets.forEach((d, di) => c.getDatasetMeta(di).data.forEach((p, i) => {
        const me = d.label === "자체 모델";
        const o = c.data.datasets.find(x => x !== d);
        const below = o && o.data[i] > d.data[i];
        ctx.textBaseline = below ? "top" : "bottom";
        ctx.fillStyle = me ? "#ff4d5a" : "#b8b8b8"; ctx.font = `${me ? 700 : 500} 14px ${F}`;
        ctx.fillText(Math.round(d.data[i]), p.x, p.y + (below ? 9 : -9));
      }));
      ctx.textAlign = "left"; ctx.textBaseline = "middle";
      const t = c.data.datasets.map((d, di) => { const m = c.getDatasetMeta(di).data; return { d, y: m[m.length - 1].y }; }).sort((p, q) => p.y - q.y);
      for (let k = 1; k < t.length; k++) if (t[k].y - t[k - 1].y < 18) t[k].y = t[k - 1].y + 18;
      t.forEach(({ d, y }) => { const me = d.label === "자체 모델"; ctx.fillStyle = me ? "#ff4d5a" : "#b8b8b8"; ctx.font = `${me ? 700 : 500} 13px ${K}`; ctx.fillText(d.label, a.right + 14, y); });
      ctx.restore();
    },
  };
  return {
    type: "line", data: { labels: order.map(i => `${leads[i] / 24}일 전`), datasets: ds }, plugins: [tags],
    options: {
      maintainAspectRatio: false, animation: false, layout: { padding: { top: 20, right: 84 } },
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { grid: { color: "rgba(255,255,255,.07)" }, border: { color: "rgba(255,255,255,.5)" }, ticks: { color: "#e8e8e8", font: { family: K, size: 13, weight: 600 }, padding: 8 } },
        y: { min: 0, max: 100, border: { display: false }, grid: { color: "rgba(255,255,255,.07)" },
          afterBuildTicks: ax => { ax.ticks = [0, 25, 50, 75, 100].map(value => ({ value })); },
          ticks: { callback: v => v + "%", color: "#7d7d7d", font: { family: F, size: 11 } } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: "#e8e8e8", titleColor: "#121212", bodyColor: "#121212", borderWidth: 0, cornerRadius: 0, padding: 10,
          callbacks: { label: c => `${c.dataset.label} ${Math.round(c.parsed.y)}%` } },
      },
    },
  };
};

/* p5 v03: 경로 예측 그래프 모양만 바꾼다(데이터 값은 그대로) */
(function () {
  // 차트가 화면에 들어와 만들어질 때까지 짧게 재시도한 뒤 모양만 바꾼다(데이터 값은 그대로)
  const wait = (id, fn) => {
    let n = 0;
    const t = setInterval(() => {
      const c = window.Chart && Chart.getChart(id);
      if (c) { clearInterval(t); try { fn(c, id); c.update("none"); } catch (e) { console.warn(id, e); } }
      else if (++n > 5000) clearInterval(t);
    }, 120);
  };
  const mix = (o, k, v) => { if (v) o[k] = Object.assign({}, o[k], v); };
  // o: { base, model, xg, yg, xb, yb, xt, yt, pad, x, y }
  const tune = (c, o) => {
    const [b, m] = c.data.datasets;
    if (o.base) Object.assign(b, o.base);
    if (o.model) Object.assign(m, o.model);
    const S = c.config.options.scales;
    mix(S.x, "grid", o.xg); mix(S.y, "grid", o.yg);
    mix(S.x, "border", o.xb); mix(S.y, "border", o.yb);
    mix(S.x, "ticks", o.xt); mix(S.y, "ticks", o.yt);
    if (o.x) Object.assign(S.x, o.x);
    if (o.y) Object.assign(S.y, o.y);
    if (o.pad) mix(c.config.options.layout, "padding", o.pad);
  };
  // 표 칸마다 머리글 글자를 data-k 로(이미 있는 글자를 다시 쓰는 것뿐)
  const labelAuc = () => {
    const box = document.getElementById("turnAuc");
    if (!box) return;
    const go = () => {
      const ths = [...box.querySelectorAll("thead th")].map(t => t.textContent);
      box.querySelectorAll("tbody tr").forEach(tr => [...tr.children].forEach((td, i) => { td.dataset.k = ths[i] || ""; }));
    };
    new MutationObserver(go).observe(box, { childList: true, subtree: true });
    go();
  };
  // 푸터를 '안내 문구'와 '자료 링크 목록' 두 덩어리로 나눈다(글자는 그대로)
  const splitFoot = () => {
    const p = document.querySelector(".foot p");
    if (!p || p.dataset.split) return;
    const links = [...p.querySelectorAll("a")];
    const note = (p.firstChild && p.firstChild.textContent || "").trim();
    p.dataset.split = "1";
    p.textContent = "";
    const a = document.createElement("span"); a.className = "f-note"; a.textContent = note;
    const b = document.createElement("span"); b.className = "f-src";
    const k = document.createElement("em"); k.textContent = "자료";
    b.append(k, ...links);
    p.append(a, b);
  };
  const ready = fn => document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", fn) : fn();

  const ACC = "#d90429", BASE = "#a8a8a8", F = "'Pretendard Variable', Pretendard, sans-serif";
  const tk = { color: "#777", font: { family: F, size: 11 } };
  ["trackChart", "gfsChart", "champiChart"].forEach(id => wait(id, c => {
    const ch = id === "champiChart";
    tune(c, {
      base: { borderColor: BASE, backgroundColor: BASE, borderWidth: 1.5, borderDash: [4, 3], pointRadius: 0 },
      model: { borderColor: ACC, backgroundColor: ACC, borderWidth: 2.5, pointRadius: ch ? 0 : 3.5, pointBackgroundColor: "#fff", pointBorderWidth: 2 },
      xg: { display: false }, yg: { color: "#efefef" }, yb: { display: false }, xb: { color: "#1a1a1a" },
      xt: Object.assign({ maxTicksLimit: ch ? 4 : 5, maxRotation: 0 }, tk), yt: Object.assign({ maxTicksLimit: 4 }, tk),
      pad: { right: 78 },
    });
  }));
})();

