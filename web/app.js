"use strict";
/* HS振り返り — 画面の動作(依存ライブラリなし) */

const S = { games: [], settings: {}, models: [], env: {}, systemPrompt: "", cur: null, game: null, tab: "replay", frame: 0 };
const IMG = "https://art.hearthstonejson.com/v1/render/latest/jaJP/256x/";
const ART = "https://art.hearthstonejson.com/v1/256x/";

/* ---------------- 小さな道具 */
const $ = (sel, el = document) => el.querySelector(sel);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
async function api(path, opts) {
  const r = await fetch(path, opts);
  let t = null;
  try { t = await r.json(); } catch (_) { /* 本文なし */ }
  if (!r.ok) throw new Error((t && t.error) || r.statusText);
  return t;
}
const post = (path, body) => api(path, {
  method: "POST", headers: { "Content-Type": "application/json", "X-HS-App": "1" }, body: JSON.stringify(body || {}),
});
const isWin = (g) => /^(勝|相手が投了)/.test(g.result);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let toastTimer = null;
function toast(msg, detail, keep) {
  const t = $("#toast");
  t.replaceChildren(h("div", {}, msg), detail ? h("pre", {}, detail) : null);
  t.hidden = false;
  clearTimeout(toastTimer);
  if (!keep) toastTimer = setTimeout(() => (t.hidden = true), 4500);
}
const hideToast = () => ($("#toast").hidden = true);
function copy(text, okMsg) {
  const done = () => toast(okMsg || "コピーしました");
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, done);
  else {
    const ta = h("textarea", { style: "position:fixed;left:-999px" }); ta.value = text; document.body.append(ta);
    ta.select(); document.execCommand("copy"); ta.remove(); done();
  }
}

/* ---------------- 起動・一覧 */
async function loadState() {
  const st = await api("/api/state");
  S.games = st.games; S.settings = st.settings; S.models = st.models; S.env = st.env;
  S.systemPrompt = st.system_prompt || "";
  S.consent = st.consent || { ok: true, version: "" };
  $("#ver").textContent = "v" + st.version;
}

function renderList() {
  const box = $("#list");
  if (!S.games.length) {
    box.replaceChildren(h("div", { class: "empty-list" }, "まだ試合がありません。右上の「試合を取り込む」を押してください。"));
    return;
  }
  box.replaceChildren(...S.games.map((g) =>
    h("button", { class: `game ${isWin(g) ? "win" : "loss"}${S.cur === g.id ? " active" : ""}`, onclick: () => openGame(g.id) },
      h("div", { class: "row1" },
        h("span", { class: `badge ${isWin(g) ? "win" : "loss"}` }, g.result),
        h("span", { class: "muted" }, g.date)),
      h("div", { class: "vs" }, h("b", {}, g.my), " vs ", g.opp),
      h("div", { class: "meta" },
        h("span", {}, `${g.turns}ターン`), h("span", {}, g.first ? "先攻" : "後攻"),
        g.analyzed ? h("span", { class: "badge ai" }, "AI振り返り済") : null))));
}

/* ---------------- 概要 / ようこそ */
function renderHome() {
  S.cur = null; S.game = null; renderList();
  const main = $("#main");
  if (!S.games.length) return renderWelcome(main);
  const done = S.games.filter((g) => g.my_turns >= 3);
  const wins = S.games.filter(isWin).length;
  const rate = (arr) => {
    const u = arr.reduce((a, g) => a + (g.unspent || 0), 0), t = arr.reduce((a, g) => a + (g.avail || 0), 0);
    return t ? Math.round((u / t) * 1000) / 10 + "%" : "-";
  };
  main.replaceChildren(h("div", { class: "hero" },
    h("h2", {}, "試合を選んで、振り返りましょう"),
    h("p", { class: "muted" }, "左の一覧から試合を選ぶと、盤面のリプレイ・グラフ・AIによる振り返りが見られます。"),
    h("div", { class: "tiles" },
      tile("取り込み済みの試合", `${S.games.length}`, "試合"),
      tile("勝率", `${Math.round((wins / S.games.length) * 100)}`, `% (${wins}勝${S.games.length - wins}敗)`),
      tile("余りマナ率 (勝ち)", rate(done.filter(isWin)), ""),
      tile("余りマナ率 (負け)", rate(done.filter((g) => !isWin(g))), "")),
    h("p", { class: "hint" }, "余りマナ率は、使えるマナのうち使わなかった割合です。「仕込み」でコストを下げたターンは使用済みとして数えています。")));
}
function tile(k, v, unit) { return h("div", { class: "tile" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v, h("small", {}, " " + unit))); }

function renderWelcome(main) {
  const e = S.env;
  const step = (ok, title, desc, btn) => h("li", {},
    h("span", { class: `dot ${ok ? "ok" : "ng"}` }, ok ? "✓" : "!"),
    h("div", { class: "grow" }, h("b", {}, title), h("div", { class: "muted" }, desc)), btn);
  main.replaceChildren(h("div", { class: "hero" },
    h("h2", {}, "はじめに"),
    h("p", { class: "muted" }, "ハースストーンを遊んだあとに、試合を取り込んで振り返るアプリです。まず、次の準備を確認します。"),
    h("ul", { class: "steps" },
      step(e.logconfig_ok, "ログ出力の設定", e.logconfig_ok ? "設定済みです。" : "ハースストーンが試合の記録を出力するための設定がありません。",
        e.logconfig_ok ? null : h("button", { onclick: fixLogConfig }, "自動で設定する")),
      step(e.logs_exist, "ログフォルダ", e.logs_exist ? e.logs_dir : "ハースストーンのログフォルダが見つかりません。「設定」でフォルダを指定してください。",
        e.logs_exist ? null : h("button", { onclick: openSettings }, "設定を開く")),
      step(false, "試合を遊ぶ", "ログ設定のあとに遊んだ試合から記録されます。遊び終わったら、右上の「試合を取り込む」を押します。")),
    h("p", { class: "hint" }, "ログ設定を変えたあとは、ハースストーンを一度再起動してください。")));
  // 3番目は常に「これから」なので色を中立に
  const dots = main.querySelectorAll(".dot"); const last = dots[dots.length - 1]; last.className = "dot"; last.textContent = "3";
}
async function fixLogConfig() {
  try {
    await post("/api/logconfig/fix");
    toast("ログ設定を作成しました。ハースストーンを再起動してください。");
    await refresh();
  } catch (e) { toast("設定に失敗しました: " + e.message); }
}

/* ---------------- 取り込み */
async function doSync() {
  const btn = $("#btn-sync"); btn.disabled = true; btn.textContent = "取り込み中…";
  try {
    await post("/api/sync");
    for (;;) {
      await sleep(900);
      const st = await api("/api/sync/status");
      toast(st.running ? "ログを読み込んでいます…" : "取り込みが完了しました", st.log.slice(-6).join("\n"), st.running);
      if (!st.running) {
        if (st.error) toast("取り込みに失敗しました", st.error, true);
        else toast(st.new ? `${st.new}試合を取り込みました` : "新しい試合はありませんでした", st.log.slice(-4).join("\n"));
        break;
      }
    }
    await refresh();
  } catch (e) { toast("取り込みに失敗しました: " + e.message); }
  btn.disabled = false; btn.textContent = "試合を取り込む";
}

async function refresh() {
  await loadState();
  if (S.cur) { renderList(); } else renderHome();
}

/* ---------------- 試合を開く */
async function openGame(id) {
  S.cur = id; renderList();
  $("#main").replaceChildren(h("p", { class: "muted" }, "読み込み中…"));
  try { S.game = await api("/api/game/" + id); }
  catch (e) { $("#main").replaceChildren(h("div", { class: "err" }, "試合を読み込めませんでした: " + e.message, h("div", { class: "muted" }, "（古い取り込み形式かもしれません。`python hsreview.py rebuild` を実行してください）"))); return; }
  const first = S.game.frames.findIndex((f) => f.phase === "start");
  S.frame = first >= 0 ? first : 0; S.tab = "replay";
  renderGame();
}

function renderGame() {
  const g = S.game, sm = g.summary, main = $("#main");
  const tabs = [["replay", "リプレイ"], ["stats", "グラフ・統計"], ["ai", "AI振り返り"], ["text", "棋譜テキスト"]];
  main.replaceChildren(
    h("div", { class: "gh" },
      h("span", { class: `badge ${isWin(sm) ? "win" : "loss"}` }, sm.result),
      h("h2", {}, sm.my, h("span", { class: "muted" }, "  vs  "), sm.opp),
      h("span", { class: "sub" }, `${sm.date} ・ ${sm.turns}ターン ・ 自分は${sm.first ? "先攻" : "後攻"}`)),
    h("div", { class: "tabs" }, tabs.map(([k, label]) =>
      h("button", { class: `tab${S.tab === k ? " on" : ""}`, onclick: () => { S.tab = k; renderGame(); } }, label))),
    h("div", { id: "pane" }));
  ({ replay: renderReplay, stats: renderStats, ai: renderAI, text: renderText })[S.tab]($("#pane"));
}

/* ---------------- リプレイ */
function cardEl(c, kind) {
  const info = (S.game.cards || {})[c.id] || {};
  const tip = [c.name, info.cost != null ? `${info.cost}マナ` : "", info.text || ""].filter(Boolean).join("\n");
  const el = h("div", { class: `card ${kind === "hand" ? "hand" : ""}${c.new ? " new" : ""}${c.loc ? " loc" : ""}`, title: tip });
  if (c.id) {
    const img = h("img", { src: IMG + c.id + ".png", alt: c.name, loading: "lazy" });
    img.addEventListener("error", () => el.classList.add("noimg"));
    el.append(img);
  } else el.classList.add("noimg");
  el.append(h("div", { class: "nm" }, c.name || "?"));
  if (kind === "hand") el.append(h("span", { class: "b cost" }, c.cost));
  if (c.new) el.append(h("span", { class: "newtag" }, "NEW"));
  if (kind === "board" && !c.loc) {
    el.append(h("span", { class: "b atk" }, c.atk), h("span", { class: `b hpv${c.hp < c.maxhp ? " dmg" : ""}` }, c.hp));
  }
  const wrap = h("div", { class: "cardwrap" }, el);
  if (c.kw && c.kw.length) wrap.append(h("div", { class: "kws" }, c.kw.map((k) => h("span", {}, k))));
  return wrap;
}
function cardRow(cards, kind, emptyMsg) {
  if (!cards.length) return h("div", { class: "row emptyrow" }, emptyMsg);
  return h("div", { class: `row ${kind === "hand" ? "hand" : ""}` }, cards.map((c) => cardEl(c, kind)));
}
function heroBar(side, who, d) {
  const hero = d.hero || {};
  const portrait = h("div", { class: "portrait" });
  if (hero.id) {
    const url = ART + hero.id + ".jpg";
    const im = new Image(); im.onload = () => (portrait.style.backgroundImage = `url(${url})`); im.src = url;
  }
  const hp = hero.hp != null ? hero.hp + (hero.armor || 0) : "?";
  return h("div", { class: "herobar" },
    portrait,
    h("div", {}, h("div", { class: "hname" }, who + "  ", h("span", { class: "muted" }, hero.name || "")),
      h("div", { class: "stat" }, hero.weapon ? `武器: ${hero.weapon.name} (${hero.weapon.atk}/${hero.weapon.dur})` : "武器なし",
        hero.power ? `　ヒーローパワー: ${hero.power.name}` : "")),
    h("span", { class: `hp${hp <= 10 ? " low" : ""}` }, "HP ", h("b", {}, hp), hero.armor ? h("small", {}, ` (護甲${hero.armor})`) : null),
    h("span", { class: "mana" }, `マナ ${d.mana.left}/${d.mana.max}`),
    h("span", { class: "stat" }, "デッキ ", h("b", {}, d.deck), "枚"),
    d.secrets_n ? h("span", { class: "stat" }, "秘策 ", h("b", {}, d.secrets_n), "枚",
      d.secrets && d.secrets.length ? `（${d.secrets.map((x) => x.name).join("、")}）` : "") : null);
}
function frameLabel(f) {
  const who = f.owner === "me" ? "自分" : "相手";
  const ph = f.phase === "start" ? "ターン開始時（ドロー後）" : f.final ? "決着時" : "ターン終了時";
  return h("span", { class: "flabel" }, `T${f.turn} `, h("span", { class: f.owner }, `${who}の手番`), ` ${ph}`);
}
function renderReplay(pane) {
  const fr = S.game.frames;
  if (!fr.length) { pane.replaceChildren(h("p", { class: "muted" }, "盤面データがありません。")); return; }
  S.frame = Math.max(0, Math.min(S.frame, fr.length - 1));
  const f = fr[S.frame];
  const slider = h("input", { type: "range", min: 0, max: fr.length - 1, value: S.frame, "aria-label": "コマ送り" });
  slider.addEventListener("input", () => { S.frame = +slider.value; renderReplay(pane); });
  const step = (d) => () => { S.frame += d; renderReplay(pane); };
  const stepTurn = (d) => () => {
    let i = S.frame + d; while (i >= 0 && i < fr.length && fr[i].turn === f.turn) i += d;
    if (i >= 0 && i < fr.length) { S.frame = fr[i].phase === "end" && d < 0 ? i : i; renderReplay(pane); }
  };
  const acts = f.actions || [];
  pane.replaceChildren(
    h("div", { class: "ctrl" },
      h("button", { onclick: stepTurn(-1), title: "前のターン" }, "« 前のターン"),
      h("button", { onclick: step(-1), disabled: S.frame === 0 }, "‹"),
      slider,
      h("button", { onclick: step(1), disabled: S.frame === fr.length - 1 }, "›"),
      h("button", { onclick: stepTurn(1), title: "次のターン" }, "次のターン »"),
      frameLabel(f)),
    h("div", { class: "board" },
      h("div", { class: "side" },
        heroBar("opp", "相手", f.opp),
        h("div", { class: "backs", title: "相手の手札" }, h("span", { class: "stat" }, `相手の手札 ${f.opp.hand_n}枚 `),
          Array.from({ length: f.opp.hand_n }, () => h("span", { class: "cardback" }))),
        cardRow(f.opp.board, "board", "相手の場にミニオンはいません")),
      h("div", { class: "divider" }, `T${f.turn}  ${f.owner === "me" ? "自分" : "相手"}の手番`),
      h("div", { class: "side" },
        cardRow(f.me.board, "board", "自分の場にミニオンはいません"),
        heroBar("me", "自分", f.me),
        h("div", { class: "stat" }, `自分の手札 ${f.me.hand.length}枚` + (f.phase === "start" ? "（金色の枠は今ターン手札に入ったカード）" : "（ターン終了時に残した手札）")),
        cardRow(f.me.hand, "hand", "手札なし"))),
    f.phase === "end"
      ? h("div", { class: "actions" }, h("h4", {}, "この手番の行動"),
          acts.length ? h("ul", {}, acts.map((a) => h("li", { class: a.startsWith("自分") ? "me" : a.startsWith("相手") ? "opp" : "" }, a)))
            : h("div", { class: "muted" }, "記録された行動はありません。"))
      : h("div", { class: "actions" }, h("h4", {}, "ターン開始時の状況"),
          h("div", { class: "muted" }, "ここから自分の手番です。「›」で、この手番の結果（ターン終了時）に進みます。")),
    h("p", { class: "hint" }, "カードにマウスを合わせると効果が出ます。キーボードの ← → でもコマ送りできます。画像はインターネットから表示します（表示できない場合は文字で表示）。"));
}
document.addEventListener("keydown", (e) => {
  if (S.tab !== "replay" || !S.game || e.target.closest("input[type=text], textarea, select")) return;
  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
    S.frame += e.key === "ArrowRight" ? 1 : -1; const p = $("#pane"); if (p) renderReplay(p);
    e.preventDefault();
  }
});

/* ---------------- グラフ・統計 */
function renderStats(pane) {
  const g = S.game, ts = g.turn_stats || [], sm = g.summary;
  const full = ts.filter((c) => !c.final);
  const unsp = full.reduce((a, c) => a + c.left_eff, 0), avail = full.reduce((a, c) => a + c.avail, 0);
  const hpUse = ts.reduce((a, c) => a + c.hp, 0);
  const prep = ts.filter((c) => c.prep && c.prep.length).length;
  pane.replaceChildren(
    h("div", { class: "tiles" },
      tile("余りマナ", `${unsp}/${avail}`, avail ? `（${Math.round((unsp / avail) * 100)}%）` : ""),
      tile("ヒーローパワー", `${hpUse}`, "回"),
      tile("コインを使った", g.coin_turn ? `自分の${g.coin_turn}回目` : "使わず", ""),
      tile("仕込みを使った", `${prep}`, "ターン")),
    h("div", { class: "chart" }, h("h4", {}, "HPの推移（ターン終了時）"), hpChart(g.hp_log),
      h("div", { class: "legend" }, h("span", {}, h("i", { style: "background:var(--me)" }), "自分"), h("span", {}, h("i", { style: "background:var(--opp)" }), "相手"))),
    h("div", { class: "chart" }, h("h4", {}, "マナの使い方（自分の各ターン）"), manaChart(ts, g.coin_turn),
      h("div", { class: "legend" },
        h("span", {}, h("i", { style: "background:#4a90e2" }), "使ったマナ"), h("span", {}, h("i", { style: "background:#e0614e" }), "余ったマナ"),
        h("span", {}, "H=ヒーローパワー　仕=仕込み　C=コイン"))),
    h("div", { class: "chart" }, h("h4", {}, "ターンごとの詳細"), h("div", { class: "scroll-x" }, statsTable(ts, g.coin_turn)),
      h("p", { class: "hint" }, "「総マナ内で出せたのに残したカード」は目安です。温存が正しい場面もあります。")),
    mulliganBlock(g));
}
function hpChart(log) {
  const W = 760, H = 240, L = 36, R = 12, T = 12, B = 26;
  const pts = (log || []).filter((p) => p[1] != null && p[2] != null);
  if (!pts.length) return h("p", { class: "muted" }, "データなし");
  const maxT = Math.max(...pts.map((p) => p[0])), maxH = Math.max(40, ...pts.map((p) => Math.max(p[1], p[2])));
  const x = (t) => L + ((W - L - R) * t) / Math.max(1, maxT), y = (v) => T + (H - T - B) * (1 - Math.max(0, v) / maxH);
  const line = (i, col) => `<polyline fill="none" stroke="${col}" stroke-width="2.5" points="${pts.map((p) => `${x(p[0])},${y(p[i])}`).join(" ")}"/>` +
    pts.map((p) => `<circle cx="${x(p[0])}" cy="${y(p[i])}" r="3" fill="${col}"><title>T${p[0]}: ${p[i]}</title></circle>`).join("");
  let grid = "";
  for (let v = 0; v <= maxH; v += 10) grid += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#323746"/><text x="${L - 6}" y="${y(v) + 4}" fill="#9aa0b0" font-size="11" text-anchor="end">${v}</text>`;
  for (let t = 1; t <= maxT; t += maxT > 16 ? 2 : 1) grid += `<text x="${x(t)}" y="${H - 8}" fill="#9aa0b0" font-size="11" text-anchor="middle">T${t}</text>`;
  const svg = h("div"); svg.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="HPの推移">${grid}${line(2, "#e0795f")}${line(1, "#5aa9e6")}</svg>`;
  return svg;
}
function manaChart(ts, coinTurn) {
  if (!ts.length) return h("p", { class: "muted" }, "データなし");
  const W = 760, H = 230, L = 30, R = 8, T = 12, B = 30;
  const maxM = Math.max(10, ...ts.map((c) => c.avail));
  const bw = Math.min(46, (W - L - R) / ts.length - 8), step = (W - L - R) / ts.length;
  const y = (v) => T + (H - T - B) * (1 - v / maxM);
  let s = "";
  for (let v = 0; v <= maxM; v += 2) s += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="#323746"/><text x="${L - 5}" y="${y(v) + 4}" fill="#9aa0b0" font-size="11" text-anchor="end">${v}</text>`;
  ts.forEach((c, i) => {
    const cx = L + step * i + step / 2, x0 = cx - bw / 2, left = c.left_eff, used = c.avail - left;
    s += `<rect x="${x0}" y="${y(used)}" width="${bw}" height="${y(0) - y(used)}" fill="#4a90e2" rx="3"><title>使った ${used}</title></rect>`;
    if (left > 0) s += `<rect x="${x0}" y="${y(c.avail)}" width="${bw}" height="${y(used) - y(c.avail)}" fill="${c.final ? "#7a4a44" : "#e0614e"}" rx="3"><title>余り ${left}${c.final ? "（途中終了）" : ""}</title></rect>`;
    const marks = (c.hp ? "H" : "") + (c.prep && c.prep.length ? "仕" : "") + (coinTurn === c.n ? "C" : "");
    s += `<text x="${cx}" y="${H - 14}" fill="#e8e6df" font-size="11" text-anchor="middle">${c.n}</text>`;
    if (marks) s += `<text x="${cx}" y="${H - 2}" fill="#e3b04b" font-size="10" text-anchor="middle">${marks}</text>`;
  });
  const svg = h("div"); svg.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="マナの使い方">${s}</svg>`; return svg;
}
function statsTable(ts, coinTurn) {
  return h("table", { class: "t" },
    h("thead", {}, h("tr", {}, ["自分のターン", "総マナ", "余り", "仕込み", "HP", "使ったカード", "総マナ内で出せたのに残したカード"].map((t) => h("th", {}, t)))),
    h("tbody", {}, ts.map((c) => h("tr", {},
      h("td", {}, `${c.n}回目 (T${c.turn})${c.final ? " 途中終了" : ""}${coinTurn === c.n ? " コイン" : ""}`),
      h("td", {}, c.avail), h("td", {}, c.left_eff), h("td", {}, (c.prep || []).join("、") || "-"), h("td", {}, c.hp ? "使用" : "-"),
      h("td", {}, (c.plays || []).join("、") || "-"), h("td", {}, (c.castable || []).join("、") || "-")))));
}
function mulliganBlock(g) {
  const m = g.mulligan || {}, off = m.offered || [], kept = new Set((m.kept || []).map((c) => c.name));
  if (!off.length) return h("div");
  return h("div", { class: "chart" }, h("h4", {}, "マリガン"),
    h("div", { class: "row", style: "justify-content:flex-start" }, off.map((c) => {
      const w = cardEl({ id: c.id, name: c.name }, "hand");
      const keep = kept.has(c.name);
      w.append(h("div", { class: "badge", style: `margin-top:4px;color:${keep ? "var(--win)" : "var(--muted)"}` }, keep ? "キープ" : "交換"));
      return w;
    })));
}

/* ---------------- AI振り返り */
let aiPoll = 0;
function modelInfo(id) { return S.models.find((m) => m.id === id) || { label: id, in: 0, out: 0 }; }
function costOf(model, tin, tout) { const m = modelInfo(model); return (tin * m.in + tout * m.out) / 1e6; }
function renderAI(pane) {
  const id = S.cur, st = S.settings, mdl = modelInfo(st.model);
  const out = h("div", { id: "ai-out" });
  const runBtn = h("button", { class: "primary", id: "ai-run", onclick: () => startAI(id) }, "AIに振り返らせる");
  pane.replaceChildren(
    h("div", { class: "ai-box" },
      h("div", { class: "ai-row" }, runBtn, h("span", { class: "muted" }, "使うAI: ", h("b", {}, mdl.label), "　"), h("button", { class: "ghost", onclick: openSettings }, "変更")),
      h("p", { class: "note" }, "この試合の棋譜が Anthropic（Claude）に送信されます。料金は、設定したあなたのAPIキーに請求されます。1試合あたりの目安は実行後に表示します。",
        S.env.fake_ai ? "（テスト用のダミー応答モードです）" : ""),
      !st.api_key_set && !S.env.fake_ai ? h("div", { class: "err" }, "APIキーが未設定です。「設定」から入力してください。キーが無い場合は、「棋譜テキスト」タブのコピー機能で、お使いのAIに貼り付けても振り返れます。") : null,
      out));
  showSaved(id, out);
  pollAI(id, out);
}
async function showSaved(id, out) {
  try {
    const r = await api(`/api/game/${id}/analysis`);
    if (r.job && r.job.status === "running") return;
    if (r.job && r.job.status === "error") { out.replaceChildren(h("div", { class: "err" }, r.job.error)); }
    const s = r.saved; if (!s || r.job && r.job.status === "error") return;
    paintAnalysis(out, s.text, s, false);
  } catch (_) { /* 何もしない */ }
}
function paintAnalysis(out, text, meta, live) {
  const box = h("div", { class: "md" + (live ? " cursor" : "") }); box.innerHTML = md(text);
  const cost = meta && meta.input_tokens != null && meta.model
    ? h("div", { class: "cost" }, `${meta.at ? meta.at + " ・ " : ""}${modelInfo(meta.model).label || meta.model} ・ 入力${meta.input_tokens.toLocaleString()} / 出力${meta.output_tokens.toLocaleString()}トークン ・ 料金の目安 約$${costOf(meta.model, meta.input_tokens, meta.output_tokens).toFixed(3)}（約${Math.max(1, Math.round(costOf(meta.model, meta.input_tokens, meta.output_tokens) * 150))}円）`)
    : null;
  out.replaceChildren(box, cost);
}
async function startAI(id) {
  const btn = $("#ai-run"); if (btn) btn.disabled = true;
  try { await post(`/api/game/${id}/analyze`); pollAI(id, $("#ai-out")); }
  catch (e) { toast(e.message); if (btn) btn.disabled = false; }
}
async function pollAI(id, out) {
  const my = ++aiPoll;
  for (;;) {
    let r; try { r = await api(`/api/game/${id}/analysis`); } catch (_) { return; }
    if (my !== aiPoll || S.cur !== id || S.tab !== "ai" || !document.body.contains(out)) return;
    const j = r.job, btn = $("#ai-run");
    if (!j || j.status === "done") { if (btn) { btn.disabled = false; btn.textContent = r.saved ? "もう一度振り返らせる" : "AIに振り返らせる"; }
      if (j && j.status === "done") { const sv = r.saved; paintAnalysis(out, (sv && sv.text) || j.text, sv || j, false); await loadState(); renderList(); } return; }
    if (btn) { btn.disabled = true; btn.textContent = "AIが考えています…"; }
    if (j.status === "error") { out.replaceChildren(h("div", { class: "err" }, j.error)); if (btn) { btn.disabled = false; btn.textContent = "もう一度試す"; } return; }
    if (j.text) paintAnalysis(out, j.text, null, true); else out.replaceChildren(h("p", { class: "muted cursor" }, "考え中"));
    await sleep(1000);
  }
}
/* 小さなMarkdown表示 */
function md(src) {
  const lines = String(src).replace(/\r/g, "").split("\n"); let out = "", list = null, para = [];
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  const flushP = () => { if (para.length) { out += "<p>" + inline(para.join(" ")) + "</p>"; para = []; } };
  const closeL = () => { if (list) { out += `</${list}>`; list = null; } };
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    let m;
    if ((m = l.match(/^(#{1,4})\s+(.*)/))) { flushP(); closeL(); const n = Math.min(3, Math.max(2, m[1].length)); out += `<h${n}>${inline(m[2])}</h${n}>`; }
    else if ((m = l.match(/^\s*[-*]\s+(.*)/))) { flushP(); if (list !== "ul") { closeL(); out += "<ul>"; list = "ul"; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = l.match(/^\s*\d+[.)]\s+(.*)/))) { flushP(); if (list !== "ol") { closeL(); out += "<ol>"; list = "ol"; } out += `<li>${inline(m[1])}</li>`; }
    else if (/^\s*\|/.test(l)) { flushP(); closeL(); const rows = []; while (i < lines.length && /^\s*\|/.test(lines[i])) { if (!/^\s*\|[\s:|-]+\|?\s*$/.test(lines[i])) rows.push(lines[i].trim().replace(/^\||\|$/g, "").split("|")); i++; } i--;
      out += "<table>" + rows.map((r, k) => "<tr>" + r.map((c) => (k ? "<td>" : "<th>") + inline(c.trim()) + (k ? "</td>" : "</th>")).join("") + "</tr>").join("") + "</table>"; }
    else if (!l.trim()) { flushP(); closeL(); }
    else { closeL(); para.push(l.trim()); }
  }
  flushP(); closeL(); return out;
}

/* ---------------- 棋譜テキスト */
async function renderText(pane) {
  pane.replaceChildren(h("p", { class: "muted" }, "読み込み中…"));
  const text = await (await fetch(`/api/game/${S.cur}/md`)).text();
  const wrapped = (S.systemPrompt ? S.systemPrompt + "\n\n" : "") + "以下が試合の記録です。上の指示に従って振り返ってください。\n\n" + text;
  pane.replaceChildren(
    h("div", { class: "ctrl" },
      h("button", { onclick: () => copy(text, "棋譜をコピーしました") }, "棋譜をコピー"),
      h("button", { class: "primary", onclick: () => copy(wrapped, "指示つきでコピーしました。お使いのAIに貼り付けてください") }, "指示つきでコピー（他のAIに貼り付ける用）")),
    h("pre", { class: "rec" }, text));
}

/* ---------------- 設定 */
function openSettings() {
  const st = S.settings, e = S.env;
  const key = h("input", { type: "password", id: "f-key", placeholder: st.api_key_set ? `設定済み（末尾 ${st.api_key_tail}）。変更する場合のみ入力` : "sk-ant-…", autocomplete: "off" });
  const model = h("select", { id: "f-model" }, S.models.map((m) => h("option", { value: m.id, selected: m.id === st.model }, `${m.label}　$${m.in}/$${m.out} per 100万トークン`)));
  const logs = h("input", { type: "text", id: "f-logs", value: st.logs_dir || "", placeholder: e.logs_dir });
  const memo = h("textarea", { id: "f-memo", placeholder: "例: 手札型ウォーロック。ゴッドフリーで手札があふれても引き続けて、壊滅で盤面を流して勝つ。" }); memo.value = st.memo || "";
  const m = $("#modal");
  m.replaceChildren(h("div", { class: "dialog", role: "dialog", "aria-label": "設定" },
    h("h3", {}, "設定"),
    h("div", { class: "field" }, h("label", {}, "AIのAPIキー（Anthropic）"), key,
      h("div", { class: "help" }, "AIの料金は、このキーのアカウントに請求されます。キーはAI（Anthropic）への通信以外には使いません。",
        st.key_store === "keyring" ? "キーは、このPCのWindows資格情報マネージャーに保存されます。"
          : "このPCでは安全な保管場所を使えないため、キーは設定ファイル（settings.json）に平文で保存されます。他人に渡さないでください。"),
      st.api_key_set ? h("button", { class: "ghost", style: "margin-top:6px", onclick: async () => { await post("/api/settings", { api_key: "__clear__" }); await refresh(); openSettings(); } }, "保存済みのキーを削除") : null),
    h("div", { class: "field" }, h("label", {}, "使うAIモデル"), model),
    h("div", { class: "field" }, h("label", {}, "デッキの狙いなどのメモ（AIに伝えます）"), memo,
      h("div", { class: "help" }, "書いておくと、AIがデッキの狙いを踏まえて指摘します。")),
    h("div", { class: "field" }, h("label", {}, "ハースストーンのログフォルダ"), logs,
      h("div", { class: "help" }, e.logs_exist ? `検出: ${e.logs_dir}` : "自動では見つかりませんでした。例: C:\\Program Files (x86)\\Hearthstone\\Logs")),
    h("div", { class: "field" }, h("label", {}, "ログ出力の設定"),
      h("div", { class: "ai-row" }, h("span", {}, e.logconfig_ok ? "設定済み" : "未設定"), e.logconfig_ok ? null : h("button", { onclick: async () => { await fixLogConfig(); openSettings(); } }, "自動で設定する")),
      h("div", { class: "help" }, "設定を変えたあとは、ハースストーンを再起動してください。")),
    h("div", { class: "btns" }, h("button", { class: "ghost", style: "margin-right:auto", onclick: () => showConsent(true) }, "注意事項・免責を見る"),
      h("button", { onclick: () => (m.hidden = true) }, "閉じる"),
      h("button", { class: "primary", onclick: async () => {
        try {
          await post("/api/settings", { api_key: key.value, model: model.value, memo: memo.value, logs_dir: logs.value });
          m.hidden = true; toast("設定を保存しました"); await refresh(); if (S.game) renderGame();
        } catch (err) { toast("保存に失敗しました: " + err.message); }
      } }, "保存"))));
  m.hidden = false;
  m.onclick = (ev) => { if (ev.target === m) m.hidden = true; };
}

/* ---------------- ご利用にあたって(初回の同意) */
const CONSENT_POINTS = [
  "非公式のファンツールです。Blizzard Entertainment とは無関係で、承認も受けていません。",
  "ハースストーンのログを読むだけで、ゲームには触れません。ただし、Blizzardの規約には、許可されていないソフトによるゲーム情報の読み取りを禁じる条項があります。ご利用は、ご自身の判断と責任でお願いします。",
  "「AI振り返り」では、その試合の記録が Anthropic（Claude）に送信され、料金は、設定したあなたのAPIキーに請求されます。AIの指摘は、間違うことがあります。",
  "生ログにはあなたのバトルタグが含まれます。取り込んだデータは、このPCの中にだけ保存されます。他人に渡さないでください。",
];
async function showConsent(viewOnly) {
  let text = "";
  try { text = await (await fetch("/api/disclaimer")).text(); } catch (_) { /* 取得できなくても、要点は表示する */ }
  const m = $("#modal");
  const box = h("div", { class: "md disc-scroll" });
  box.innerHTML = md(text || "（全文を読み込めませんでした）");
  const top = [h("h3", {}, viewOnly ? "注意事項・免責" : "ご利用にあたって"),
    h("ul", { class: "points" }, CONSENT_POINTS.map((p) => h("li", {}, p))),
    h("p", { class: "muted" }, "全文は次のとおりです（スクロールできます）。"), box];
  return new Promise((resolve) => {
    if (viewOnly) {
      m.replaceChildren(h("div", { class: "dialog", role: "dialog" }, ...top,
        h("div", { class: "btns" }, h("button", { class: "primary", onclick: () => { openSettings(); resolve(); } }, "戻る"))));
      m.hidden = false;
      return;
    }
    const agree = h("input", { type: "checkbox", id: "f-agree", style: "width:auto" });
    const btn = h("button", { class: "primary", disabled: true }, "同意して始める");
    agree.addEventListener("change", () => (btn.disabled = !agree.checked));
    btn.addEventListener("click", async () => {
      try {
        await post("/api/consent", { version: S.consent.version });
        S.consent.ok = true; m.hidden = true; m.onclick = null; resolve();
      } catch (e) { toast("保存に失敗しました: " + e.message); }
    });
    m.replaceChildren(h("div", { class: "dialog", role: "dialog", "aria-label": "ご利用にあたって" }, ...top,
      h("label", { class: "agree" }, agree, " 上の内容を読み、理解したうえで、自己責任で利用します"),
      h("div", { class: "btns" }, btn)));
    m.onclick = null;
    m.hidden = false;
  });
}

/* ---------------- 起動 */
$("#btn-sync").addEventListener("click", doSync);
$("#btn-settings").addEventListener("click", openSettings);
document.querySelector(".brand").style.cursor = "pointer";
document.querySelector(".brand").addEventListener("click", () => { S.cur = null; S.game = null; renderHome(); });
/* URLの #g=試合ID&t=replay|stats|ai|text&f=コマ番号 で、開く画面を指定できる(画面の共有・撮影用) */
async function applyHash() {
  const p = new URLSearchParams(location.hash.slice(1)), g = p.get("g");
  if (!g || !S.games.find((x) => x.id === g)) return;
  await openGame(g);
  if (p.get("t") && ["replay", "stats", "ai", "text"].includes(p.get("t"))) S.tab = p.get("t");
  if (p.get("f") != null && p.get("f") !== "") S.frame = +p.get("f");
  renderGame();
}
(async function init() {
  try {
    await loadState();
    if (!S.consent.ok) await showConsent(false);   // 初回(または免責の更新後)は、同意するまで先に進めない
    renderList(); renderHome(); await applyHash();
  }
  catch (e) { $("#main").replaceChildren(h("div", { class: "err" }, "アプリに接続できませんでした: " + e.message)); }
})();
