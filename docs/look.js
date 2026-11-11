/* 지도 중심 디자인 훅. index.html 의 인라인 훅 다음, app.js 전에 로드된다(기능은 app.js 그대로). */
(() => {
  const INK = "#14171a", TEXT = "#2c3137", MUTE = "#6d747c", FAINT = "#a0a6ad", ACC = "#1d4ed8", GRID = "rgba(20,23,26,.07)";
  const F = "'Pretendard Variable', Pretendard, system-ui, sans-serif";

  // 그래프·지도 색: 파랑 = 자체 모델(한국), 회색 = 비교 대상(일본)
  window.THEME_C = { korea: ACC, japan: "#8b929a", jmap: "#475569", muted: FAINT, text: TEXT, ink: INK, grid: GRID, warn: MUTE };
  window.THEME_TIP = { backgroundColor: INK, borderColor: INK, borderWidth: 0, titleColor: "#fff", bodyColor: "#fff", cornerRadius: 0, padding: 10 };
  window.THEME_FONT = F;
  window.THEME_TENSION = 0;
  window.THEME_REFLINE = "rgba(20,23,26,.4)";
  window.PAD_L = 56; window.PAD_R = 56; window.PAD_B = 72; window.MAP_MAXZ = 6;      // 지도 맞춤 여백과 최대 확대

  window.HIST_DATA = data => {
    data.datasets.forEach(d => { d.borderWidth = 1.75; d.fill = false; d.backgroundColor = "transparent"; d.pointHoverRadius = 4; d.pointHoverBackgroundColor = d.borderColor; });
    data.datasets[1].borderWidth = 1.25;
  };

  // 기상청 비교: 선 두 줄. 5일 전 → 1일 전, 값은 점 옆에, 이름은 오른쪽 끝에
  window.LEAD_CHART = ({ sets, leads }) => {
    const rev = a => a.slice().reverse();
    const ds = sets.map(d => {
      const me = d.label === "자체 모델";
      return { label: d.label, data: rev(d.data), _me: me, borderColor: me ? ACC : MUTE, borderWidth: me ? 2.5 : 1.5, borderDash: me ? [] : [5, 4],
        pointRadius: 4, pointHoverRadius: 6, pointBorderWidth: 1.5, pointBackgroundColor: me ? ACC : "#fff", pointBorderColor: me ? ACC : MUTE, tension: 0, fill: false };
    });
    const tags = {
      id: "tags",
      afterDatasetsDraw(c) {
        const ctx = c.ctx, a = c.chartArea, m = c.data.datasets.map((_, i) => c.getDatasetMeta(i).data);
        ctx.save();
        ctx.textAlign = "center";
        c.data.datasets.forEach((d, di) => m[di].forEach((p, i) => {
          const other = m[1 - di] && m[1 - di][i];
          const up = !other || p.y <= other.y;                 // 위에 있는 선의 값은 위에, 아래 선의 값은 아래에
          ctx.fillStyle = d._me ? ACC : MUTE;
          ctx.font = `${d._me ? 700 : 500} 13px ${F}`;
          ctx.textBaseline = up ? "bottom" : "top";
          ctx.fillText(Math.round(d.data[i]) + "%", p.x, p.y + (up ? -9 : 9));
        }));
        ctx.textAlign = "left"; ctx.textBaseline = "middle";
        const ends = c.data.datasets.map((d, di) => ({ d, y: m[di][m[di].length - 1].y })).sort((p, q) => p.y - q.y);
        for (let k = 1; k < ends.length; k++) if (ends[k].y - ends[k - 1].y < 18) ends[k].y = ends[k - 1].y + 18;
        ends.forEach(({ d, y }) => { ctx.fillStyle = d._me ? ACC : MUTE; ctx.font = `${d._me ? 700 : 500} 13px ${F}`; ctx.fillText(d.label, a.right + 14, y); });
        ctx.restore();
      },
    };
    return {
      type: "line", data: { labels: rev(leads).map(h => `${h / 24}일 전`), datasets: ds }, plugins: [tags],
      options: {
        maintainAspectRatio: false, animation: false, layout: { padding: { top: 26, right: 80, bottom: 4 } },
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { offset: true, grid: { display: false }, border: { color: INK }, ticks: { color: TEXT, font: { size: 13 }, padding: 8 } },
          y: { min: 0, max: 100, border: { display: false }, grid: { color: GRID }, ticks: { stepSize: 25, callback: v => v + "%", color: MUTE, font: { size: 12 }, padding: 8 } },
        },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label} ${Math.round(c.parsed.y)}%` } } },
      },
    };
  };

  // 왼쪽 패널 머리: 결론 한 줄 → 나라별 확률. 빨강은 한국 확률 20% 이상이거나 이미 진입했을 때만
  window.KEYFIGS = (latest, live, u) => {
    const box = document.getElementById("keyfigs");
    if (!box) return;
    if (!live.length) { box.innerHTML = `<h1 class="kf-name cv-quiet">활동 중인 태풍 없음</h1>`; return; }
    const [s, ...rest] = live, last = s.track[s.track.length - 1], k = s.korea;
    const say = k.arrived ? "한국 영향권 진입" : k.p >= .4 ? "한국 영향 가능성 높음" : k.p >= .2 ? "한국 영향 가능성 있음" : "한국 영향 가능성 낮음";
    const hot = v => v.arrived || v.p >= .2;
    const row = (c, label) => {
      const v = s[c], p = v.arrived ? 1 : v.p;
      const eta = v.arrived ? `<em>${u.kst(v.arrived)} 진입</em>` : v.p >= .2 && v.eta ? `<em>${u.kst(v.eta)} 도착 예상 · ${u.leftText(v.eta)}</em>` : "";
      // 지난 5일 확률 추이(6시간 간격 관측마다). 점선은 도달 예측 기준 40%
      const hist = s.track.slice(-21).map(t => t[c === "korea" ? 5 : 6] || 0), n = Math.max(hist.length - 1, 1);
      const pts = hist.map((q, i) => `${(i / n * 120).toFixed(1)},${(27 - q * 25).toFixed(1)}`).join(" ");
      const spark = `<svg class="spark" viewBox="0 0 120 28" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="120" y1="17" y2="17"/><polyline points="${hist.length > 1 ? pts : `0,${27 - p * 25} 120,${27 - p * 25}`}"/></svg>`;
      return `<div class="${c}${c === "korea" && hot(v) ? " warn" : ""}"><dt>${label}</dt><dd>${spark}<b>${v.arrived ? "진입" : u.pctCard(v.p)}</b>${eta}</dd></div>`;
    };
    const ko = u.stormName(s), vit = [u.lvName(last[3] ?? null, s.grade), last[3] != null ? `${last[3]}kt` : "", last[4] != null ? `${last[4]}hPa` : ""].filter(Boolean).join(" · ");
    box.innerHTML = `<p class="kf-lv">${vit}</p>`
      + `<h1 class="kf-name">${ko}${ko.toUpperCase() !== s.name.toUpperCase() ? `<span>${s.name}</span>` : ""}</h1>`
      + `<p class="kf-say${hot(k) ? " warn" : ""}">${say}</p>`
      + `<dl class="kf-p">${row("korea", "한국")}${row("japan", "일본")}</dl>`
      + (rest.length ? `<ul class="kf-more">${rest.map(r => `<li><b>${u.stormName(r)}</b><span>한국 ${r.korea.arrived ? "진입" : u.pctCard(r.korea.p)}</span><span>일본 ${r.japan.arrived ? "진입" : u.pctCard(r.japan.p)}</span></li>`).join("")}</ul>` : "");
  };

  // 전향 복기: 세 가지 맞대결을 탭으로, 고른 대결의 태풍을 표로(renderReview 와 같은 기준)
  const SRC = [["kma", "기상청"], ["own", "자체 경로"], ["clf", "자체 분류"]], NAME = Object.fromEntries(SRC), PER = 8;
  const cell = v => v === "none" || v === undefined ? { st: "na" } : (v && v.lead_h != null) ? { st: "hit", h: v.lead_h } : { st: "miss" };
  const val = c => c.st === "hit" ? `${c.h}시간 전` : `<span class="${c.st}">${c.st === "miss" ? "놓침" : "자료 없음"}</span>`;
  const lead = c => c.st === "hit" ? c.h : -1;
  document.addEventListener("DOMContentLoaded", () => {
    const sec = document.getElementById("review"), title = sec && sec.querySelector(".wrap .sec-title");
    if (!title) return;
    const get = n => fetch(`data/${n}.json`, { cache: "no-cache" }).then(r => r.ok ? r.json() : null).catch(() => null);
    Promise.all([get("review"), get("history")]).then(([rv, hs]) => {
      if (!rv || !hs || !hs.length || !document.body.contains(title)) return;
      const by = Object.fromEntries(hs.map(s => [s.sid, s]));
      const rows = Object.entries(rv).filter(([sid, r]) => r.turn && by[sid]).sort((a, b) => b[1].turn.localeCompare(a[1].turn))
        .map(([sid, r]) => ({ sid, season: by[sid].season, name: by[sid].name, v: Object.fromEntries(SRC.map(([k]) => [k, cell(r[k])])) }));
      if (!rows.length) return;
      const pairs = [["kma", "own"], ["kma", "clf"], ["own", "clf"]].map(([a, b]) => {
        const rs = rows.filter(x => x.v[a].st !== "na" && x.v[b].st !== "na").map(x => { const p = lead(x.v[a]), q = lead(x.v[b]); return { x, r: p > q ? a : q > p ? b : "eq" }; });
        return { a, b, rs, wa: rs.filter(o => o.r === a).length, wb: rs.filter(o => o.r === b).length };
      });
      const root = document.createElement("div");
      root.className = "rv";
      title.after(root);
      let cur = 0, pg = 0;
      const draw = () => {
        const p = pairs[cur], n = Math.ceil(p.rs.length / PER), show = p.rs.slice(pg * PER, pg * PER + PER);
        root.innerHTML = `<div class="rv-tabs" role="group" aria-label="비교 대상">` + pairs.map((q, i) => `<button type="button" data-p="${i}" aria-pressed="${i === cur}">${NAME[q.a]} <b>${q.wa} : ${q.wb}</b> ${NAME[q.b]}</button>`).join("") + `</div>`
          + `<table class="rv-table"><thead><tr><th>태풍</th><th>${NAME[p.a]}</th><th>${NAME[p.b]}</th><th>먼저 예측</th></tr></thead><tbody>`
          + show.map(({ x, r }) => `<tr><td><button type="button" class="nm" data-sid="${x.sid}">${KO[x.name] || x.name}</button><span class="yr">${x.season}</span></td>`
            + `<td${r === p.a ? ' class="w"' : ""}>${val(x.v[p.a])}</td><td${r === p.b ? ' class="w"' : ""}>${val(x.v[p.b])}</td><td class="res ${r}">${r === "eq" ? "같음" : NAME[r]}</td></tr>`).join("")
          + `</tbody></table>`
          + (n > 1 ? `<nav class="rv-pg" aria-label="쪽">${Array.from({ length: n }, (_, i) => `<button type="button" data-pg="${i}" aria-pressed="${i === pg}">${i + 1}</button>`).join("")}</nav>` : "");
      };
      root.addEventListener("click", e => {
        const b = e.target.closest("[data-p]"), g = e.target.closest("[data-pg]"), s = e.target.closest("[data-sid]");
        if (b) { cur = +b.dataset.p; pg = 0; draw(); }
        else if (g) { pg = +g.dataset.pg; draw(); }
        else if (s) { try { if (typeof histOpen === "function") histOpen(s.dataset.sid); } catch (_) { /* 지난 태풍 준비 전 */ } }
      });
      draw();
      sec.classList.add("rv-on");
    });
  });
  const KO = { CHABA: "차바", "CHAN-HOM": "찬홈", CHANTHU: "찬투", DANAS: "다나스", FRANCISCO: "프란시스코", GONI: "고니", HAGUPIT: "하구핏", HAISHEN: "하이선", HINNAMNOR: "힌남노", JANGMI: "장미", JONGDARI: "종다리", KHANUN: "카눈", "KONG-REY": "콩레이", LEEPI: "리피", LINGLING: "링링", MAYSAK: "마이삭", MERANTI: "므란티", MITAG: "미탁", NAMTHEUN: "남테운", OMAIS: "오마이스", PRAPIROON: "쁘라삐룬", PULASAN: "풀라산", SOUDELOR: "사우델로르", SOULIK: "솔릭", TAPAH: "타파", TRASES: "뜨라세", CHAMPI: "참피", "IN-FA": "인파", KOGUMA: "고구마", HALOLA: "할롤라", NANMADOL: "난마돌", BAVI: "바비", AERE: "에어리", SONGDA: "송다", SHANSHAN: "산산" };
})();
