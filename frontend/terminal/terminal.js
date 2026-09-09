/* MoEngage Agentic Brain Terminal — vanilla runtime over the brain API. No external resources (CSP self-only). */
(() => {
'use strict';
const TOKEN = (document.querySelector('meta[name="local-token"]') || {}).content || '';
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = (n, d = 2) => (n === null || n === undefined || n === '' || isNaN(Number(n))) ? '—' : Number(n).toLocaleString(undefined, {maximumFractionDigits: d});
const pct = (n, d = 1) => (n === null || n === undefined || isNaN(Number(n))) ? '—' : `${Number(n) > 0 ? '+' : ''}${Number(n).toFixed(d)}%`;
const cls = (n) => Number(n) > 0 ? 'up' : Number(n) < 0 ? 'down' : 'flat';
const C = {cy: '#6fe3ff', dim: '#7fd8ec', gr: '#4dffa8', am: '#ffb84d', mg: '#ff5c9e', txt: '#d3e7ef'};
const NAV = [
  {id: 'brain', code: '01', label: 'BRAIN', title: 'Brain', crumb: 'ENGINE / CORE', sub: 'Live cognition state, ranked directives and everything the brain decided in the last cycle.'},
  {id: 'intel', code: '02', label: 'COMPETITOR INTEL', title: 'Competitor Intelligence', crumb: 'ENGINE / INTEL', sub: 'Venue moves detected from public tickers and exchange pages. Internal only: nothing here is ever named in copy.'},
  {id: 'ideas', code: '03', label: 'CAMPAIGN IDEAS', title: 'Campaign Ideas', crumb: 'ENGINE / IDEATION', sub: 'Generated hypotheses with expected impact, confidence and the signal that produced them. Stale ideas expire on their own.'},
  {id: 'exp', code: '04', label: 'EXPERIMENTS', title: 'Experiments', crumb: 'ENGINE / EXPERIMENTS', sub: 'Idea → simulation → approval → live → learning. Every proposal is an experiment; nothing sends without a human.'},
  {id: 'sops', code: '05', label: 'SOP LIBRARY', title: 'Campaign SOPs', crumb: 'ENGINE / SOPS', sub: 'Versioned operating procedures per campaign family: sequence, caps, holdout, kill rules.'},
  {id: 'anom', code: '06', label: 'ANOMALIES', title: 'Anomalies', crumb: 'ENGINE / TELEMETRY', sub: 'Daily snapshots diffed against each campaign’s own baseline, ranked by urgency, with the cause and the option that comes first.'},
  {id: 'market', code: '07', label: 'MARKET FEED', title: 'Market Feed', crumb: 'ENGINE / EXOGENOUS', sub: 'Crypto, tokenised markets, web3, macro and news compiled into regime-aware campaign hooks.'},
];
const S = {screen: 'brain', state: null, sop: null, sops: [], filter: 'all', trace: [], focus: 0, directives: [], busy: false, seenTrace: new Set()};
try { S.scan = localStorage.getItem('moe.scan') !== 'off'; } catch (_) { S.scan = true; }
document.body.classList.toggle('noscan', !S.scan);

async function api(path, {method = 'GET', body} = {}) {
  const init = {method, headers: {'X-Local-Token': TOKEN}};
  if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
  const r = await fetch(path, init); let data = null; try { data = await r.json(); } catch (_) {}
  if (r.status === 401) { toast('server restarted · reload the page for a fresh session token', 'warn', 9000); throw new Error('session token expired'); }
  if (!r.ok) { const m = (data && (data.detail || data.error)) || `${r.status} ${r.statusText}`; throw new Error(typeof m === 'string' ? m : JSON.stringify(m)); }
  return data;
}
function toast(msg, type = 'error', ms = 5000) { const t = document.createElement('div'); t.className = `toast ${type}`; t.textContent = msg; $('#toasts').appendChild(t); setTimeout(() => t.remove(), ms); }
function pushTrace(text, level = 'info') { const d = new Date(); const ts = [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, '0')).join(':'); S.trace.push({ts, text, level, local: true}); S.trace = S.trace.slice(-40); renderTrace(); }
const loading = () => `<div class="panel"><div class="ph"><span class="t">loading</span></div><div class="loading"><div class="dash"></div></div></div>`;
const empty = (fact, next) => `<div class="empty"><b>${esc(fact)}</b>${next ? ` · ${esc(next)}` : ''}</div>`;
const err = (m) => `<div class="err">${esc(m)}</div>`;
const md = (src) => { let h = esc(src || ''); h = h.replace(/^### (.*)$/gm, '<b>$1</b>').replace(/^## (.*)$/gm, '<b>$1</b>').replace(/^# (.*)$/gm, '<b>$1</b>').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/^\s*[-•] (.*)$/gm, '<li>$1</li>').replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`).replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>'); return `<div class="md"><p>${h}</p></div>`; };

// ── chrome ────────────────────────────────────────────────────────────────────
function tick() { const d = new Date(); $('#clock').textContent = [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, '0')).join(':'); }
setInterval(tick, 1000); tick();
function renderChrome() {
  const st = S.state || {}; const cur = NAV.find(n => n.id === S.screen) || NAV[0];
  $('#bus').innerHTML = (st.bus || []).map(b => `<div class="chip"><span class="k">${esc(b.k)}</span><span class="v" style="color:${esc(b.c)}">${esc(b.v)}</span></div>`).join('');
  const badges = st.badges || {};
  $('#nav').innerHTML = NAV.map(n => `<button class="item ${n.id === S.screen ? 'on' : ''}" data-go="${n.id}"><span class="code">${n.code}</span><span>${n.label}</span>${badges[n.id] ? `<span class="badge">${badges[n.id]}</span>` : ''}</button>`).join('');
  $('#load').innerHTML = (st.load || []).map(l => `<div class="meter" title="${esc(l.note || '')}"><div class="r"><span>${esc(l.name)}</span><span style="color:${esc(l.c)}">${esc(l.val)}</span></div><div class="bar"><i style="width:${l.pct}%;background:${esc(l.c)}"></i></div></div>`).join('');
  $('#facts').textContent = `bind 127.0.0.1 · secrets keychain · region ${st.region || '—'}`;
  $('#crumb').textContent = cur.crumb; $('#h1').textContent = cur.title; $('#sub').textContent = cur.sub;
  const on = !!st.autopilot; const t = $('#auto'); t.classList.toggle('on', on); t.setAttribute('aria-pressed', String(on)); $('#auto-lab').textContent = on ? 'ON · approval still required for sends' : 'OFF';
  const stats = (st.stats || {})[S.screen] || [];
  $('#strip').innerHTML = stats.length ? stats.map(s => `<div class="tile"><div class="k">${esc(s.k)}</div><div class="v" style="color:${esc(s.c || C.txt)}">${esc(s.v)}</div><div class="s">${esc(s.sub || '')}</div></div>`).join('') : `<div class="tile"><div class="k">STATE</div><div class="v">—</div><div class="s">loading</div></div>`;
  $('#scan-toggle').textContent = `scanlines: ${S.scan ? 'on' : 'off'}`;
}
async function refreshState() { try { S.state = await api('/api/brain/state'); renderChrome(); } catch (e) { if (!S.state) $('#strip').innerHTML = `<div class="tile"><div class="k">STATE</div><div class="v" style="color:${C.mg}">unreachable</div><div class="s">${esc(e.message)}</div></div>`; } }

// ── modules ───────────────────────────────────────────────────────────────────
const views = {};
views.brain = async () => {
  const body = $('#body'); body.innerHTML = `<div class="cols"><div class="c3"><div class="panel"><div class="ph"><span class="t">Priority directives</span><span class="meta">ranked by severity · a approves the focused one</span></div><div id="dirs">${loading()}</div></div>
    <div class="panel"><div class="ph"><span class="t">Cognition pipeline</span><span class="meta">this cycle</span></div><div class="pb pipe" id="pipe"></div></div></div>
    <div class="c2"><div class="panel"><div class="ph"><span class="t">Brain trace · live</span><span class="meta">audit log · receipts</span></div><div class="trace" id="trace"></div></div>
    <div class="panel"><div class="ph"><span class="t">Morning brief</span><span class="meta">plain english</span></div><div class="pb brief" id="brief"></div></div></div></div>`;
  renderPipeline(); renderBrief(); renderTrace();
  try { const d = await api('/api/brain/directives'); S.directives = d.directives || []; S.focus = 0; renderDirectives(); } catch (e) { $('#dirs').innerHTML = err(e.message); }
};
function renderPipeline() { const p = (S.state || {}).pipeline || []; const el = $('#pipe'); if (!el) return; el.innerHTML = p.map(x => `<div class="st"><div class="lab10">${esc(x.stage)}</div><div class="n" style="color:${esc(x.c)}">${esc(x.count)}</div><div class="bar"><i style="width:${x.pct}%;background:${esc(x.c)}"></i></div><div class="note">${esc(x.note)}</div></div>`).join(''); }
function renderBrief() { const b = (S.state || {}).brief || []; const el = $('#brief'); if (!el) return; const col = {act_now: C.mg, high_ev: C.cy, counter: C.am, cleanup: C.gr, watch: C.am, good: C.gr, info: C.dim}; el.innerHTML = b.length ? b.map(x => `<div class="b"><span class="g" style="color:${col[x.sev] || C.dim}">▸</span><span>${esc(x.t)}</span></div>`).join('') : empty('no brief yet', 'run the daily cycle'); }
function renderDirectives() {
  const el = $('#dirs'); if (!el) return; const ds = S.directives;
  el.innerHTML = ds.length ? ds.map((d, i) => `<div class="dir ${i === S.focus ? 'focus' : ''}" data-i="${i}"><div class="railx" style="background:${esc(d.color)}"></div><div class="bd"><span class="chip sev" style="color:${esc(d.color)};border-color:${esc(d.color)}">${esc(d.sev_label)}</span><div class="tt">${esc(d.title)}</div><div class="mm">${esc(d.meta)}</div><div class="rr">${esc(d.rationale)}</div>
    <div class="ac">${d.actions.includes('approve') ? `<button class="btn sm g" data-act="approve" data-id="${esc(d.id)}">Approve + execute</button>` : ''}${d.actions.includes('simulate') ? `<button class="btn sm c" data-act="simulate" data-id="${esc(d.id)}">Simulate</button>` : ''}${d.actions.includes('hold') ? `<button class="btn sm m" data-act="hold" data-id="${esc(d.id)}">Hold</button>` : ''}<button class="btn sm" data-ask="${esc(d.id)}">Ask the brain</button></div><div class="sim" id="sim-${i}"></div></div></div>`).join('') : empty('no directives right now', 'next cycle at ' + (((S.state || {}).bus || []).find(b => b.k === 'SCHEDULER') || {}).v);
}
async function actDirective(id, action, i) {
  if (action === 'approve' && !confirm('Approve and execute? The previewed write will be sent to MoEngage.')) return;
  const note = action === 'hold' ? (prompt('Why hold it? (recorded on the proposal)') || '') : '';
  try {
    const r = await api(`/api/brain/directives/${encodeURIComponent(id)}/${action}`, {method: 'POST', body: {note}});
    pushTrace(r.trace || `directive/${action} ${id}`, r.ok ? (action === 'approve' ? 'ok' : 'info') : 'alert');
    if (r.error) toast(r.error);
    const box = $(`#sim-${i}`);
    if (box && action === 'simulate') box.innerHTML = `<div class="hair" style="margin-top:8px;padding-top:8px;font-size:11px;color:#93aab7">${r.brief_check ? `brief ${r.brief_check.ok ? '<span class="up">ok</span>' : '<span class="down">problems</span>'} ${esc((r.brief_check.problems || []).join('; '))} ${esc((r.brief_check.warnings || []).slice(0, 2).join('; '))}` : ''}${r.preview ? ` · preview <code>${esc(r.preview.mode || '')}</code> ${esc(r.preview.transport || '')}` : ''}${r.diagnosis ? `<div>${esc(((r.diagnosis.options || [])[0] || {}).action || '')} — ${esc(((r.diagnosis.options || [])[0] || {}).how_in_moengage || '')}</div>` : ''}${r.pair_battle ? `<div>${(r.pair_battle.venues || []).slice(0, 5).map(v => `${esc(v.exchange)} ${fmt(v.share_pct, 0)}%`).join(' · ')}</div>` : ''}${r.agent_prompt ? `<div><button class="btn sm c" data-ask-text="${esc(r.agent_prompt)}">Hand to the brain</button></div>` : ''}</div>`;
    if (action !== 'simulate') { await refreshState(); await views.brain(); }
  } catch (e) { toast(e.message); pushTrace(`directive/${action} failed · ${e.message}`, 'alert'); }
}
function renderTrace() { const el = $('#trace'); if (!el) return; el.innerHTML = S.trace.map(l => `<div class="ln"><span class="ts">${esc(l.ts)}</span><span class="tx lv-${esc(l.level)}">${esc(l.text)}</span></div>`).join('') + `<div class="ln"><span class="ts"></span><span class="cursor"></span></div>`; el.scrollTop = el.scrollHeight; }
async function pollTrace() { try { const d = await api('/api/brain/trace?limit=40'); const server = (d.lines || []).map(l => ({...l, key: l.ts + '|' + l.text})); const local = S.trace.filter(l => l.local); S.trace = [...server, ...local].slice(-40); renderTrace(); } catch (_) {} }

views.intel = async () => {
  const body = $('#body'); body.innerHTML = loading();
  try {
    const d = await api('/api/brain/intel');
    body.innerHTML = `<div class="cards" style="margin-bottom:16px">${(d.rivals || []).length ? d.rivals.map(r => `<div class="card"><div class="nm"><span>${esc(r.name)}</span><span class="chip sev" style="color:${esc(r.color)};border-color:${esc(r.color)}">${esc(r.threat)}</span></div>
      <div class="pi"><div class="r"><span>PRESSURE INDEX</span><span style="color:${esc(r.color)}">${r.pressureIndex}</span></div><div class="bar"><i style="width:${r.pressureIndex}%;background:${esc(r.color)}"></i></div></div>
      <div class="mv">${esc(r.latestMove)}</div><div>${(r.tactics || []).map(t => `<span class="chip">${esc(t)}</span> `).join('')}</div>
      <div class="foot"><span class="lab10">counter play</span><span style="color:${C.cy};text-align:right">${esc(r.counterPlay)}</span></div>
      <div class="foot" style="border-top:0;padding-top:2px;font-size:10px;color:var(--text-ghost)"><span>${esc(r.source || '')}</span><span>vol ${r.vol_24h_usd ? '$' + fmt(r.vol_24h_usd / 1e6, 1) + 'M' : '—'}${r.share != null ? ' · share ' + fmt(r.share, 1) + '%' : ''}</span></div></div>`).join('') : empty('no venues loaded yet', 'the market feed refreshes every 15 minutes')}</div>
      <div class="cols"><div class="c3"><div class="panel"><div class="ph"><span class="t">Moves detected · 24–48h</span><span class="meta">surges · venue jumps · listing gaps</span></div><div class="pb"><div class="grid-tbl moves"><div class="h">time</div><div class="h">signal</div><div class="h">channel</div><div class="h">impact</div>${(d.moves || []).map(m => `<div class="row" style="display:contents"><div class="r ghost">${esc(m.t)}</div><div class="r">${esc(m.what)}</div><div class="r mute">${esc(m.chan)}</div><div class="r" style="color:${esc(m.c)}">${esc(m.impact)}</div></div>`).join('') || `<div class="r" style="grid-column:1/-1">${empty('no moves above threshold', 'surges need a snapshot ≥ 45 min old')}</div>`}</div></div></div></div>
      <div class="c1"><div class="panel"><div class="ph"><span class="t">Share of INR spot</span><span class="meta">tracked venues</span></div><div class="pb sov">${(d.sov || []).map(s => `<div class="r"><span>${esc(s.name)}</span><span style="color:${esc(s.c)}">${fmt(s.pct, 1)}%</span></div><div class="bar"><i style="width:${Math.min(100, s.pct || 0)}%;background:${esc(s.c)}"></i></div>`).join('') || empty('no share data')}</div></div>
      <div class="panel"><div class="ph"><span class="t">Actions</span><span class="meta">owner · sop</span></div><div class="pb">${(d.actions || []).slice(0, 6).map(a => `<div style="padding:6px 0;border-bottom:1px solid var(--line-soft);font-size:12px"><span class="chip" style="color:${a.priority >= 80 ? C.mg : a.priority >= 60 ? C.am : C.dim}">${esc(a.type)}</span> <b>${esc(a.symbol)}</b> <span class="mute">${esc(a.owner)}</span><div class="muted" style="font-size:11px;margin-top:3px">${esc(a.what)}</div>${a.sop ? `<div class="ghost" style="font-size:10px">${esc(a.sop)} · <a href="#" data-ask-text="Act on competitive action ${esc(a.type)} for ${esc(a.symbol)}: ${esc(a.what)}. Use ${esc(a.sop)} via run_sop dry-run then real; no venue names in copy.">ask the brain</a></div>` : ''}</div>`).join('') || empty('no actions')}</div></div></div></div>
      <div class="ghost" style="font-size:10px;margin-top:4px">${esc(d.note || '')}</div>
      <div class="panel" id="bench"><div class="ph"><span class="t">Best in industry · by category</span><span class="meta" id="bench-tabs"></span></div><div class="pb" id="bench-body"><div class="loading"><div class="dash"></div></div></div></div>`;
    renderBench();
  } catch (e) { body.innerHTML = err(e.message); }
};
S.benchCat = S.benchCat || 'perps';
async function renderBench() {
  const money = (v) => v == null ? '—' : v >= 1e9 ? '$' + fmt(v / 1e9, 2) + 'B' : v >= 1e6 ? '$' + fmt(v / 1e6, 1) + 'M' : '$' + fmt(v, 0);
  try {
    if (!S.bench) S.bench = await api('/api/market/benchmarks');
    const cats = Object.keys(S.bench.categories || {}); const cat = S.bench.categories[S.benchCat] ? S.benchCat : cats[0]; const c = S.bench.categories[cat] || {};
    $('#bench-tabs').innerHTML = cats.map(k => `<button class="f ${k === cat ? 'on' : ''}" data-bench="${k}" style="margin-left:4px">${k.replace('_', ' / ').toUpperCase()}</button>`).join('');
    const ours = c.ours || {};
    $('#bench-body').innerHTML = `<div class="strip" style="margin-bottom:12px"><div class="tile"><div class="k">GLOBAL LEADER</div><div class="v" style="color:${C.txt}">${esc((c.leader || {}).name || '—')}</div><div class="s">${money((c.leader || {}).vol_24h_usd)} / 24h</div></div><div class="tile"><div class="k">INDIA LEADER</div><div class="v" style="color:${C.am}">${esc((c.india_leader || {}).name || '—')}</div><div class="s">${money((c.india_leader || {}).vol_24h_usd)} / 24h</div></div><div class="tile"><div class="k">OURS</div><div class="v" style="color:${C.cy}">${ours.unknown ? 'unknown' : money(ours.vol_24h_usd)}</div><div class="s">${esc(ours.source || '')}</div></div><div class="tile"><div class="k">GAP · INDIA / GLOBAL</div><div class="v" style="color:${C.mg}">${c.gap_to_india_leader_x ? c.gap_to_india_leader_x + '×' : '—'} / ${c.gap_to_leader_x ? c.gap_to_leader_x + '×' : '—'}</div><div class="s">share of top-5 ${c.our_share_of_top5_pct ?? '—'}%</div></div></div>
      <div class="grid-tbl" style="grid-template-columns:minmax(0,1.4fr) 60px 90px 90px 70px 70px minmax(0,1.6fr)"><div class="h">venue</div><div class="h">scope</div><div class="h">24h vol</div><div class="h">open int.</div><div class="h">mkts</div><div class="h">taker</div><div class="h">top pairs</div>${(c.venues || []).slice(0, 14).map(v => `<div class="row" style="display:contents"><div class="r ${v.us ? '' : ''}" style="color:${v.us ? C.cy : v.reference ? 'var(--text-ghost)' : 'var(--text)'}">${esc(v.name)}${v.reference ? ' <span class="ghost">(ref)</span>' : ''}</div><div class="r mute">${esc(v.scope)}</div><div class="r">${v.unknown ? '—' : money(v.vol_24h_usd)}</div><div class="r">${money(v.oi_usd)}</div><div class="r mute">${v.markets ?? '—'}</div><div class="r mute">${v.taker_fee_pct != null ? v.taker_fee_pct + '%' : '—'}</div><div class="r ghost" style="font-size:10px">${(v.top_pairs || []).slice(0, 4).map(p => esc(p.symbol) + ' ' + money(p.vol_24h_usd)).join(' · ') || '—'}</div></div>`).join('')}</div>
      <div class="lab10" style="margin:12px 0 6px">match their numbers</div>${(c.targets || []).length ? c.targets.map(t => t.pair ? `<div style="font-size:12px;padding:4px 0;border-bottom:1px solid var(--line-soft)"><b>${esc(t.pair)}</b> <span class="mute">vs ${esc(t.leader)}</span> · them ${money(t.their_vol_24h_usd)} · us ${money(t.our_vol_24h_usd)} · ${t.multiple ? `<span class="down">${t.multiple}×</span>` : `<span class="down">${esc(t.note || '')}</span>`}${t.sop ? ` <a href="#" class="ghost" style="font-size:10px" data-ask-text="Close the gap on ${esc(t.pair)} (${esc(t.leader)} does ${money(t.their_vol_24h_usd)}/24h vs our ${money(t.our_vol_24h_usd)}): pair_battle, then run_sop('${esc(t.sop)}') for our ${esc(t.pair)} watchers/holders; product asks if liquidity is the constraint. No venue names in copy.">ask the brain</a>` : ''}</div>` : `<div style="font-size:12px;padding:4px 0;border-bottom:1px solid var(--line-soft)"><b>${esc(t.to_match)}</b> · them ${money(t.their_vol_24h_usd)} · us ${money(t.our_vol_24h_usd)} · ${t.multiple ? t.multiple + '×' : '—'} · need +${money(t.daily_volume_needed_usd)}/day${t.step_25pct_usd ? ` (first step to 25%: +${money(t.step_25pct_usd)})` : ''}</div>`).join('') : '<div class="ghost" style="font-size:11px">no comparable figure for us in this category yet</div>'}
      <div class="ghost" style="font-size:10px;margin-top:8px">${esc(S.bench.note || '')}</div>`;
    $('#bench-tabs').onclick = e => { const b = e.target.closest('[data-bench]'); if (!b) return; S.benchCat = b.dataset.bench; renderBench(); };
  } catch (e) { const el = $('#bench-body'); if (el) el.innerHTML = err(e.message); }
}

views.ideas = async () => {
  const body = $('#body'); const filters = ['all', 'audience', 'counter', 'sop', 'market', 'cadence'];
  body.innerHTML = `<div class="filters">${filters.map(f => `<button class="f ${S.filter === f ? 'on' : ''}" data-f="${f}">${f.toUpperCase()}</button>`).join('')}</div><div id="idea-grid">${loading()}</div>`;
  try {
    const d = await api(`/api/brain/ideas?filter=${encodeURIComponent(S.filter)}`);
    $('#idea-grid').innerHTML = (d.ideas || []).length ? `<div class="ideas">${d.ideas.map(i => `<div class="card idea"><div class="tt">${esc(i.title)}<span class="id">${esc(i.id)}</span></div><div style="margin:6px 0">${(i.tags || []).map(t => `<span class="tag">${esc(t)}</span>`).join('')}</div><div class="hyp">${esc(i.hypothesis)}</div>${i.how ? `<div class="muted" style="font-size:11px">${esc(i.how)}</div>` : ''}
      <div class="metrics"><div class="m"><div class="k">EXPECTED</div><div class="v up">${esc(i.projectedLift)}</div></div><div class="m"><div class="k">CONFIDENCE</div><div class="v" style="color:${C.cy}">${esc(i.confidence)}</div></div><div class="m"><div class="k">EFFORT</div><div class="v" style="color:${C.am}">${esc(i.effort)}</div></div></div>
      <div class="src">source · ${esc(i.sourceSignal)}</div><div class="ac" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn sm c" data-promote="${esc(i.raw_id)}">Promote → experiment</button><button class="btn sm" data-ask-text="Improve idea ${esc(i.id)} '${esc(i.title)}': ${esc(i.hypothesis)}. Sharpen audience, KPI, holdout and copy per our skills; then record_ideas.">Ask brain to improve</button></div></div>`).join('')}</div>` : empty('no ideas for this filter', 'the feed refreshes with each cycle; expired ideas are hidden');
  } catch (e) { $('#idea-grid').innerHTML = err(e.message); }
};

views.exp = async () => {
  const body = $('#body'); body.innerHTML = loading();
  try {
    const d = await api('/api/brain/experiments');
    body.innerHTML = `<div class="kanban">${(d.columns || []).map(c => `<div class="col"><div class="ch" style="color:${esc(c.c)}"><span>${esc(c.name)}</span><span>${c.cards.length}</span></div>${c.cards.length ? c.cards.slice(0, 20).map(x => `<div class="xcard"><div class="tt">#${x.id} ${esc(x.title)}</div><div class="nt">${esc(x.note)}</div><div class="ft"><span class="mute">${esc(x.tag)}${x.product ? ' · ' + esc(x.product) : ''}</span><span style="color:${esc(c.c)}">${esc(x.metric)}</span></div>${c.key === 'proposed' ? `<div style="display:flex;gap:6px;margin-top:6px"><button class="btn sm g" data-act="approve" data-id="proposal:${x.id}">Approve</button><button class="btn sm c" data-act="simulate" data-id="proposal:${x.id}">Simulate</button><button class="btn sm m" data-act="hold" data-id="proposal:${x.id}">Hold</button><button class="btn sm" data-ask-text="Improve experiment (proposal) #${x.id}: read proposal_detail(${x.id}) and revise_proposal with concrete changes or comment_proposal.">Improve</button></div>` : ''}</div>`).join('') : `<div class="empty" style="padding:10px 4px">empty</div>`}</div>`).join('')}</div>`;
  } catch (e) { body.innerHTML = err(e.message); }
};

views.sops = async () => {
  const body = $('#body'); body.innerHTML = loading();
  try {
    const d = await api('/api/sops'); S.sops = (d.sops || []).filter(s => s.active !== 0); if (!S.sop || !S.sops.find(s => s.id === S.sop)) S.sop = (S.sops[0] || {}).id;
    const sop = S.sops.find(s => s.id === S.sop) || {};
    const gateOf = (st) => (String(st.purpose || '').startsWith('(internal)') ? 'AUTO' : 'APPROVAL');
    body.innerHTML = `<div class="cols"><div class="c1"><div class="panel"><div class="ph"><span class="t">Library</span><span class="meta">${S.sops.length} sops</span></div><div class="soplist" style="max-height:70vh;overflow:auto">${S.sops.map(s => `<div class="it ${s.id === S.sop ? 'on' : ''}" data-sop="${esc(s.id)}"><div style="display:flex;justify-content:space-between;gap:8px"><span class="nm">${esc(s.name)}</span><span class="vv">v${s.version}</span></div><div class="fm">${esc(s.campaign_type)} · ${s.steps_count} steps · ${esc((s.audience || {}).segment_family || '')}</div></div>`).join('')}</div></div></div>
      <div class="c3"><div class="panel"><div class="ph"><span class="t">Sequence · ${esc(sop.name || '')}</span><span class="meta"><button class="btn sm c" id="sop-dry">Dry run</button> <button class="btn sm primary" id="sop-run">▷ Run → proposals</button> <button class="btn sm" data-ask-text="Run SOP '${esc(sop.id)}' on its default cohort: sop_detail, segment_study, run_sop dry_run, write two compliant variants per step, run_sop for real.">Ask brain to run it</button></span></div>
        <div class="pb"><div class="muted" style="font-size:12px;margin-bottom:8px">${esc(sop.objective || '')}${sop.user ? ` · <span class="mute">who:</span> ${esc(sop.user)}` : ''}</div>
        ${(sop.steps || []).map((st, i) => `<div class="step"><div class="n">S${i + 1}</div><div class="b"><div class="t">${esc(st.purpose)}</div><div class="d">${esc(st.copy_brief)}${st.condition ? ` · if ${esc(st.condition)}` : ''}</div><div class="m"><span style="color:${C.dim}">${esc(st.channel)}</span><span class="mute">D${st.day} · ${esc(st.send_time_ist || '')}</span><span style="color:${gateOf(st) === 'AUTO' ? C.gr : C.am}">${gateOf(st)}</span></div></div></div>`).join('')}
        <div class="rules"><div class="r"><div class="k">FREQUENCY CAP</div><div class="v">${esc(((sop.frequency || {}).max_messages_per_user_per_week ?? '—') + ' per week · ' + ((sop.frequency || {}).cadence || ''))}</div></div><div class="r"><div class="k">HOLDOUT</div><div class="v">${esc(sop.holdout_pct)}% · window ${esc(sop.measurement_window_days)}d</div></div><div class="r"><div class="k">KILL RULES</div><div class="v">${esc((sop.kill_criteria || []).join(' · '))}</div></div><div class="r"><div class="k">PRIMARY KPI</div><div class="v">${esc(sop.primary_kpi)} → ${esc(sop.target)} · guardrail ${esc(sop.guardrail_metric)}</div></div></div>
        ${(sop.ideas || []).length ? `<div class="mute" style="font-size:11px;margin-top:10px">ideas to test · ${sop.ideas.map(esc).join(' · ')}</div>` : ''}<div id="sop-out" style="margin-top:10px;font-size:12px"></div></div></div></div></div>`;
  } catch (e) { body.innerHTML = err(e.message); }
};
async function runSop(dry) {
  const out = $('#sop-out'); if (!S.sop) return;
  if (!dry && !confirm('Queue one approval-gated proposal per step? Nothing sends before approval.')) return;
  out.innerHTML = '<span class="mute">running pre-flight…</span>';
  try {
    const r = await api(`/api/sops/${encodeURIComponent(S.sop)}/run`, {method: 'POST', body: {dry_run: dry}});
    const pf = r.preflight || {};
    out.innerHTML = `<div class="${r.ok ? 'up' : 'down'}">${r.ok ? (r.run_id ? `sop/run ${esc(S.sop)} → ${r.proposal_ids.length} approval-gated proposals queued (run #${r.run_id})` : `dry run ok · nothing queued`) : esc(r.note || r.error || 'pre-flight blocked')}</div>${(pf.checks || []).map(c => `<div style="font-size:11px"><span class="${c.ok ? 'up' : 'down'}">${c.ok ? '✓' : '✗'}</span> ${esc(c.check)} <span class="mute">${esc(c.detail || '')}</span></div>`).join('')}`;
    pushTrace(r.run_id ? `sop/run ${S.sop} → ${r.proposal_ids.length} proposals` : `sop/dry-run ${S.sop} · ${r.ok ? 'ok' : 'blocked'}`, r.ok ? 'ok' : 'warn');
    if (r.run_id) refreshState();
  } catch (e) { out.innerHTML = err(e.message); }
}

views.anom = async () => {
  const body = $('#body'); body.innerHTML = loading();
  try {
    const d = await api('/api/brain/anomalies'); const rows = d.anomalies || [];
    const spark = (vals, color) => { const v = (vals || []).filter(x => x != null).map(Number); if (!v.length) return '<div class="spark"></div>'; const mx = Math.max(...v), mn = Math.min(...v); return `<div class="spark">${v.map((x, i) => `<i style="height:${Math.round(20 + (mx === mn ? 50 : (x - mn) / (mx - mn) * 80))}%;${i === v.length - 1 ? `background:${color}` : ''}"></i>`).join('')}</div>`; };
    body.innerHTML = `<div class="panel"><div class="ph"><span class="t">Deep diagnosis · ranked</span><span class="meta">vs each campaign’s own baseline · day-of-week adjusted</span></div><div class="pb">${rows.length ? rows.map(a => `<div class="an"><div><span class="chip sev" style="color:${esc(a.color)};border-color:${esc(a.color)}">${esc(a.severity)}</span> <span class="nm">${esc(a.campaign)}</span> <span class="mt">${esc(a.metric)} · baseline ${fmt(a.baseline)}${a.n_history ? ` · n=${a.n_history}` : ''}</span><div class="ca"><span class="lab10">cause ·</span> ${esc(a.cause)}</div><div class="op"><span class="lab10">option ·</span> ${esc(a.option)}</div></div>
      <div class="rt"><div class="dl" style="color:${esc(a.color)}">${esc(a.delta || '—')}</div>${spark(a.trend, a.color)}<div class="cap">14D TREND</div><button class="btn sm" style="margin-top:4px" data-ask-text="Diagnose ${esc(a.campaign)} (${esc(a.metric)} ${esc(a.delta)}): campaign_diagnosis, then propose the do-first option as a proposal if it is an action.">Ask the brain</button></div></div>`).join('') : empty('no anomalies above baseline', 'next snapshot at ' + ((((S.state || {}).bus || []).find(b => b.k === 'SCHEDULER') || {}).v || '09:00'))}</div></div>`;
  } catch (e) { body.innerHTML = err(e.message); }
};

views.market = async () => {
  const body = $('#body'); body.innerHTML = loading();
  try {
    const d = await api('/api/brain/market');
    body.innerHTML = `${d.tier0 ? `<div class="err" style="border-color:${C.mg};margin-bottom:12px">tier-0 · ${esc(d.tier0.kind.replace(/_/g, ' '))} · ${esc(d.tier0.detail)} · promotions frozen; global announcement sop with product lenses is the response <button class="btn sm m" data-ask-text="Tier-0 event: ${esc(d.tier0.detail)}. announcement_lenses → core fact + product lenses → run_sop('sop_global_announcement_lenses') dry-run then real.">hand to the brain</button></div>` : ''}
      <div class="tiles">${(d.tiles || []).map(t => `<div class="mtile"><div class="sy"><span>${esc(t.symbol)}</span><span class="${t.change == null ? 'mute' : cls(t.change)}">${t.change == null ? '' : pct(t.change)}</span></div><div class="px">${typeof t.price === 'number' ? fmt(t.price, t.price < 1 ? 4 : 2) : esc(t.price ?? '—')}</div><div class="nt">${esc(t.note || '')}</div></div>`).join('') || empty('market feed not loaded', 'the daily cycle fetches it')}</div>
      <div class="panel"><div class="ph"><span class="t">Compiled campaign hooks</span><span class="meta">regime ${esc(d.regime || '—')} · ${(d.hooks || []).length} allowed</span></div><div class="pb"><div class="grid-tbl hooks"><div class="h">angle</div><div class="h">hook</div><div class="h">cohort</div>${(d.hooks || []).map(h => `<div class="row" style="display:contents"><div class="r" style="color:${esc(h.c)};font-size:10px;letter-spacing:.12em">${esc(h.regime)}</div><div class="r">${esc(h.hook)} <a href="#" data-ask-text="Act on hook ${esc(h.id)}: ${esc(h.hook)}. Use SOP ${esc(h.sop || 'sop_asset_spotlight')} via run_sop dry-run then real, two compliant variants, respect regime policy and limits." class="ghost" style="font-size:10px">ask the brain</a></div><div class="r mute">${esc(h.cohort)}</div></div>`).join('') || `<div class="r" style="grid-column:1/-1">${empty('no allowed hooks', 'angle policy blocks everything in this regime')}</div>`}</div></div></div>
      ${(d.web3 || []).length ? `<div class="panel"><div class="ph"><span class="t">Web3 trending · unverified tokens</span><span class="meta">data only</span></div><div class="pb" style="font-size:12px">${d.web3.map(w => `<span class="chip">${esc(w[1])} <span class="mute">${esc(w[0])}</span></span> `).join('')}</div></div>` : ''}
      <div class="ghost" style="font-size:10px">${esc(d.narrative || '')}</div>`;
  } catch (e) { body.innerHTML = err(e.message); }
};

// ── navigation / keyboard / palette ───────────────────────────────────────────
function show(id) { if (!views[id]) id = 'brain'; S.screen = id; location.hash = id; renderChrome(); views[id]().catch(e => { $('#body').innerHTML = err(e.message); }); }
document.addEventListener('click', async e => {
  const go = e.target.closest('[data-go]'); if (go) return show(go.dataset.go);
  const act = e.target.closest('[data-act]'); if (act) { const card = act.closest('.dir'); return actDirective(act.dataset.id, act.dataset.act, card ? +card.dataset.i : 0); }
  const ask = e.target.closest('[data-ask]'); if (ask) { const d = S.directives.find(x => x.id === ask.dataset.ask); return openPalette(d ? `Directive: ${d.title}. ${d.rationale} What exactly should we do, and can you queue it?` : ''); }
  const askT = e.target.closest('[data-ask-text]'); if (askT) { e.preventDefault(); return openPalette(askT.dataset.askText); }
  const f = e.target.closest('[data-f]'); if (f) { S.filter = f.dataset.f; return views.ideas(); }
  const sop = e.target.closest('[data-sop]'); if (sop) { S.sop = sop.dataset.sop; return views.sops(); }
  const pr = e.target.closest('[data-promote]'); if (pr) { try { const r = await api(`/api/brain/ideas/${encodeURIComponent(pr.dataset.promote)}/promote`, {method: 'POST', body: {}}); pushTrace(r.trace, 'info'); openPalette(r.agent_prompt || ''); } catch (err2) { toast(err2.message); } return; }
  if (e.target.closest('#sop-dry')) return runSop(true); if (e.target.closest('#sop-run')) return runSop(false);
  if (e.target.closest('#scan-toggle')) { e.preventDefault(); S.scan = !S.scan; document.body.classList.toggle('noscan', !S.scan); try { localStorage.setItem('moe.scan', S.scan ? 'on' : 'off'); } catch (_) {} renderChrome(); return; }
  if (e.target.closest('#cmd')) return openPalette('');
  if (e.target.closest('#auto')) { const on = !((S.state || {}).autopilot); try { await api('/api/engine/setting', {method: 'POST', body: {key: 'autopilot_enabled', value: on}}); pushTrace(on ? 'autopilot armed · approval still required for sends' : 'autopilot disarmed', on ? 'ok' : 'warn'); await refreshState(); } catch (err2) { toast(err2.message); } return; }
  if (e.target.closest('#cycle')) { const b = $('#cycle'); b.disabled = true; pushTrace('cycle/start mode=guarded', 'info'); try { const r = await api('/api/brain/cycle/run', {method: 'POST', body: {}}); pushTrace(`cycle/done · ${(r.steps || []).join(' → ') || r.status || 'ok'}`, 'ok'); await refreshState(); show(S.screen); } catch (err2) { toast(err2.message); pushTrace('cycle/failed · ' + err2.message, 'alert'); } finally { b.disabled = false; } return; }
  if (e.target.closest('#palette') && !e.target.closest('.pal')) closePalette();
});
function openPalette(text) { const p = $('#palette'); p.hidden = false; const i = $('#pal-in'); i.value = text || ''; i.focus(); const m = ((S.state || {}).bus || []).find(b => b.k === 'BRAIN'); $('#pal-model').textContent = m ? m.v : ''; }
function closePalette() { $('#palette').hidden = true; }
async function sendPalette() {
  const i = $('#pal-in'); const q = i.value.trim(); if (!q || S.busy) return; S.busy = true; const out = $('#pal-out');
  out.innerHTML += `<div class="who">YOU</div><div>${esc(q)}</div><div class="who">BRAIN</div><div id="pal-wait" class="mute">thinking · tools run locally · writes queue for approval <span class="cursor"></span></div>`; out.scrollTop = out.scrollHeight; i.value = '';
  pushTrace(`brain/ask "${q.slice(0, 60)}"`, 'info');
  try { const r = await api('/api/brain/ask', {method: 'POST', body: {message: q}}); $('#pal-wait').outerHTML = md(r.reply || '') + ((r.tool_used || []).length ? `<div class="ghost" style="font-size:10px">tools · ${r.tool_used.map(esc).join(' · ')}${r.model ? ' · ' + esc(r.model) : ''}</div>` : ''); pushTrace(`brain/answer · ${(r.tool_used || []).length} tools`, 'ok'); refreshState(); }
  catch (e) { $('#pal-wait').outerHTML = err(e.message); pushTrace('brain/ask failed · ' + e.message, 'alert'); }
  finally { S.busy = false; out.scrollTop = out.scrollHeight; }
}
$('#pal-in').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sendPalette(); } });
document.addEventListener('keydown', e => {
  const typing = ['INPUT', 'TEXTAREA'].includes((e.target || {}).tagName);
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); return $('#palette').hidden ? openPalette('') : closePalette(); }
  if (e.key === 'Escape') return closePalette();
  if (typing) return;
  if (/^[1-7]$/.test(e.key)) return show(NAV[+e.key - 1].id);
  if (e.key === 'a' && S.screen === 'brain' && S.directives[S.focus] && S.directives[S.focus].actions.includes('approve')) return actDirective(S.directives[S.focus].id, 'approve', S.focus);
  if ((e.key === 'j' || e.key === 'ArrowDown') && S.screen === 'brain') { S.focus = Math.min(S.directives.length - 1, S.focus + 1); renderDirectives(); }
  if ((e.key === 'k' || e.key === 'ArrowUp') && S.screen === 'brain') { S.focus = Math.max(0, S.focus - 1); renderDirectives(); }
});
window.addEventListener('hashchange', () => { const t = location.hash.slice(1); if (views[t] && t !== S.screen) show(t); });

(async () => {
  await refreshState();
  await pollTrace();
  show(views[location.hash.slice(1)] ? location.hash.slice(1) : 'brain');
  setInterval(pollTrace, 4200);
  setInterval(refreshState, 60000);
})();
})();
