'use strict';
const TOKEN = (document.querySelector('meta[name="local-token"]') || {}).content || '';
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const S = {screen: 'today', focusKey: null, resolved: new Set(), selectedSop: null, toast: '', clock: '', data: {}, params: {}};
const SCREENS = [['today', 'Today'], ['asks', 'Asks'], ['rivals', 'Rivals'], ['ideas', 'Ideas'], ['running', 'Running'], ['plays', 'Playbooks'], ['alerts', 'Alerts'], ['engine', 'Engine'], ['bench', 'Workbench']];
const HIDDEN = ['tool'];            // routable, shown under Workbench in the nav

async function api(path, opts = {}) {
  const r = await fetch(path, {method: opts.method || 'GET', headers: {'Content-Type': 'application/json', 'X-Local-Token': TOKEN}, body: opts.body ? JSON.stringify(opts.body) : undefined});
  let data = null; try { data = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error((data && (data.detail || data.error)) || `${r.status}`);
  return data;
}
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3400);
}
function tick() { const d = new Date(); const hm = [d.getHours(), d.getMinutes()].map((x) => String(x).padStart(2, '0')).join(':'); S.clock = hm; const s = $('#synced'); if (s) s.textContent = `synced ${S.syncedAt || hm} · next ${S.next || '09:00'}`; }
function route() {
  const h = (location.hash || '#today').slice(1); const [scr, q] = h.split('?'); S.params = Object.fromEntries(new URLSearchParams(q || ''));
  S.screen = SCREENS.some(([k]) => k === scr) || HIDDEN.includes(scr) ? scr : 'today';
  if (S.params.decision) S.focusKey = S.params.decision;
  if (S.params.sop) S.selectedSop = S.params.sop;
  render();
}
function nav(open) {
  const asksOpen = S.data.asks ? S.data.asks.open : (S.data.today || {}).asks_open;
  $('#nav').innerHTML = SCREENS.map(([k, l]) => `<a href="#${k}" class="${S.screen === k || (k === 'bench' && S.screen === 'tool') ? 'on' : ''}">${l}${k === 'today' && open ? `<span class="count">${open}</span>` : ''}${k === 'asks' && asksOpen ? `<span class="count">${asksOpen}</span>` : ''}</a>`).join('');
}
function verbLink(m, cls = 'verb accent') {
  if (!m) return '';
  if (m.go) return `<a class="${cls}" href="${esc(m.go)}">${esc(m.label)}</a>`;
  return `<a class="${cls}" href="#" data-ask="${esc(m.ask || '')}">${esc(m.label)}</a>`;
}

// ── Today ────────────────────────────────────────────────────────────────────
async function renderToday() {
  const d = S.data.today || (S.data.today = await api('/api/v3/today'));
  S.syncedAt = d.synced; S.next = d.next; tick();
  const decs = d.decisions.filter((x) => !S.resolved.has(x.id));
  nav(decs.length);
  let focus = decs.find((x) => x.id === S.focusKey) || decs[0];
  const rest = decs.filter((x) => focus && x.id !== focus.id);
  const main = $('#main');
  const focusHtml = focus ? `<section class="sec focus" data-id="${esc(focus.id)}">
      <div class="tagline"><span class="tag ${esc(focus.tone)}">${esc(focus.tag)}</span><span class="counter">${decs.indexOf(focus) + 1} of ${decs.length}</span></div>
      <h2>${esc(focus.title)}</h2>
      <p class="fbody">${esc(focus.body)}</p>
      <div class="facts">${focus.facts.map((f) => `<div class="fact"><span class="k">${esc(f.k)}</span><span class="v ${esc(f.tone)}">${esc(f.v)}</span></div>`).join('')}</div>
      <div class="plan"><div class="k">IF YOU APPROVE</div><ul>${focus.plan.map((p) => `<li>${esc(p)}</li>`).join('')}</ul></div>
      ${focus.blocked ? `<p class="blocked"><span class="k">BEFORE THIS CAN RUN</span>${esc(focus.blocked)}</p>` : ''}
      <div class="acts">${focus.primary.go ? `<a class="primary" href="${esc(focus.primary.go)}">${esc(focus.primary.label)}</a>` : `<button class="primary" data-resolve="approve" data-id="${esc(focus.id)}">${esc(focus.primary.label)}</button>`}${verbLink(focus.evidence, 'evidence')}${focus.test ? `<a class="evidence" href="#" data-test-proposal="${focus.test.proposal_id}">${esc(focus.test.label)}</a>` : ''}<button class="defer" data-resolve="defer" data-id="${esc(focus.id)}">${esc(focus.defer.label)}</button></div>
    </section>` : `<section class="sec clear"><p class="line">${esc(d.all_clear.line)}</p><p class="body">${esc(d.all_clear.context)}</p><p><a class="bordered" href="${esc(d.all_clear.button.go)}">${esc(d.all_clear.button.label)}</a></p></section>`;
  const thenHtml = rest.length ? `<section class="sec then"><div class="lab">Then</div>${rest.map((x) => `<div class="row" data-focus="${esc(x.id)}"><span class="tag ${esc(x.tone)}">${esc(x.tag)}</span><span class="title">${esc(x.title)}</span><span class="val">${esc((x.facts[0] || {}).v || '')}</span><a class="verb accent" href="#today?decision=${encodeURIComponent(x.id)}">${x.kind === 'anomaly' ? 'FIX IT →' : x.kind === 'proposal' ? 'REVIEW →' : 'DECIDE →'}</a></div>`).join('')}</section>` : '';
  const movedHtml = `<section class="sec moved"><div class="lab">What moved · and what to do</div>${d.moved.length ? d.moved.map((m) => `<div class="row"><span class="delta ${esc(m.tone)}">${esc(m.delta)}</span><span class="what body">${esc(m.what)}</span><span class="do">${esc(m.do)}</span>${verbLink(m)}</div>`).join('') : '<div class="row"><span class="body">Nothing moved outside its own range today.</span></div>'}</section>`;
  const handledHtml = `<section class="sec handled"><div class="lab">Handled without you · no action needed</div>${d.handled.length ? `<ul>${d.handled.map((h) => `<li>${esc(h)}</li>`).join('')}</ul>` : '<p class="small">Nothing yet today.</p>'}</section>`;
  main.innerHTML = `<section class="sec"><div class="dateline">${esc(d.dateline)}</div><p class="headline">${esc(decs.length ? d.headline : d.all_clear.line + ' ' + d.all_clear.context)}</p>${focusHtml}</section>${thenHtml}${movedHtml}${handledHtml}`;
}
async function resolve(id, action, btn) {
  if (btn) btn.disabled = true;
  const prev = S.focusKey; S.resolved.add(id); S.focusKey = null; renderToday().catch(() => {});      // optimistic
  try {
    const r = await api(`/api/v3/decisions/${encodeURIComponent(id)}/${action}`, {method: 'POST', body: {}});
    toast(r.toast || 'Done');
    if (r.ask) ask(r.ask, true);
    if (!r.ok && action === 'approve') { S.resolved.delete(id); S.focusKey = prev; }
    S.data.today = null; renderToday().catch(() => {});
  } catch (e) { S.resolved.delete(id); S.focusKey = prev; S.data.today = null; renderToday().catch(() => {}); toast(`Not done · ${e.message}`); }
}

// ── Rivals ───────────────────────────────────────────────────────────────────
async function renderRivals() {
  const d = S.data.rivals || (S.data.rivals = await api('/api/v3/rivals')); nav((S.data.today || {}).open);
  $('#main').innerHTML = `<section class="sec"><div class="lab">Rivals</div><p class="lead">${esc(d.lead)}</p></section>
    <section class="sec">${d.rivals.map((r) => `<div class="row rival" id="rival-${esc(r.id)}"><div class="grow"><h3>${esc(r.name)} <span class="tag ${esc(r.tone)}">${esc(r.threat)}</span></h3><p class="body">${esc(r.did)}</p><p class="answer"><b>Our answer</b> · ${esc(r.answer)}</p>${verbLink(r.link)}</div></div>`).join('') || '<div class="row"><span class="body">No rival data yet; the market picture is still warming up.</span></div>'}</section>`;
  if (S.params.focus) { const el = $(`#rival-${CSS.escape(S.params.focus)}`); if (el) el.scrollIntoView({block: 'start'}); }
}

// ── Ideas ────────────────────────────────────────────────────────────────────
async function renderIdeas() {
  const d = S.data.ideas || (S.data.ideas = await api('/api/v3/ideas')); nav((S.data.today || {}).open);
  $('#main').innerHTML = `<section class="sec"><div class="lab">Ideas</div><p class="lead">${esc(d.lead)}</p></section>
    <section class="sec">${d.ideas.map((i) => `<div class="row idea"><div class="grow"><h3>${esc(i.title)}</h3><p class="body">${esc(i.hypothesis)}</p><div class="line">${esc(i.line)}</div><button class="bordered" data-promote="${esc(i.raw_id ?? i.id)}">${esc(i.button.label)}</button></div></div>`).join('') || '<div class="row"><span class="body">The queue is empty; the brain refills it on its next cycle.</span></div>'}</section>`;
}

// ── Running ──────────────────────────────────────────────────────────────────
async function renderRunning() {
  const d = S.data.running || (S.data.running = await api('/api/v3/running')); nav((S.data.today || {}).open);
  $('#main').innerHTML = `<section class="sec"><div class="lab">Running</div><p class="lead">${esc(d.lead)}</p></section>
    <section class="sec">${d.rows.map((r) => `<div class="row run" id="run-${r.id}"><span class="title">${esc(r.title)}</span><span class="when">${esc(r.when)}</span><span class="metric">${esc(r.metric)}</span><span class="status ${esc(r.tone)}">${esc(r.status)}</span>${verbLink(r.move)}</div>`).join('') || '<div class="row"><span class="body">Nothing is running yet. Start with an idea.</span></div>'}</section>`;
  if (S.params.focus) { const el = $(`#run-${CSS.escape(S.params.focus)}`); if (el) el.scrollIntoView({block: 'start'}); }
}

// ── Playbooks ────────────────────────────────────────────────────────────────
async function renderPlays() {
  const key = `plays:${S.selectedSop || ''}`;
  const d = S.data[key] || (S.data[key] = await api('/api/v3/plays' + (S.selectedSop ? `?sop=${encodeURIComponent(S.selectedSop)}` : ''))); nav((S.data.today || {}).open);
  const p = d.detail;
  const link = (id) => `#plays?sop=${encodeURIComponent(id)}`;
  const picker = d.groups.map((g) => `<div class="lab pk-group">${esc(g)}</div>` + d.tabs.filter((t) => t.group === g).map((t) =>
    `<a href="${link(t.id)}" class="row pk ${t.id === d.selected ? 'on' : ''}" data-q="${esc((t.name + ' ' + g).toLowerCase())}"><span class="grow">${esc(t.short)}</span><span class="small">${t.steps} step${t.steps === 1 ? '' : 's'} · ${esc(t.version)}</span><span class="verb">${t.id === d.selected ? 'IN FRONT' : 'OPEN →'}</span></a>`).join('')).join('');
  $('#main').innerHTML = `${p ? `<section class="sec play">
      <div class="tagline"><span class="lab" style="margin:0">Playbook · ${esc(p.position)}</span>
        <span class="switch">${p.prev ? `<a href="${link(p.prev)}">← PREVIOUS</a>` : ''}${p.next ? `<a href="${link(p.next)}">NEXT →</a>` : ''}<a href="#picker" data-jump="picker">ALL PLAYBOOKS ↓</a></span></div>
      <h2>${esc(p.name)} <span class="small">${esc(p.version)}</span></h2><p class="metaline">${esc(p.meta)}</p><p class="body">${esc(p.objective)}</p>
      <div class="steps">${p.steps.map((s) => `<div class="row step"><span class="n">${s.n}</span><span class="st"><b>${esc(s.title)}</b><span class="small">${esc(s.detail)}</span></span><span class="gate ${s.gate === 'automatic' ? 'green' : 'amber'}">${esc(s.gate)}</span>${verbLink(s.action)}</div>`).join('')}</div>
      <div class="rules">${p.rules.map((r) => `<div class="row"><span class="k">${esc(r.k)}</span><span class="v">${esc(r.v)}</span></div>`).join('')}</div>
      <p class="runline"><a href="#" class="bordered" data-ask="${esc(p.run.ask)}">${esc(p.run.label)}</a></p></section>` : ''}
    <section class="sec picker" id="picker"><div class="lab">All playbooks</div><p class="lead">${esc(d.lead)}</p>
      <div class="pk-find"><input id="pk-find" type="text" placeholder="type to narrow · deposit, winback, funding…" aria-label="Find a playbook" autocomplete="off"><span class="small" id="pk-count">${d.tabs.length} of ${d.tabs.length}</span></div>
      <div id="pk-list">${picker}</div></section>`;
  const find = $('#pk-find');
  if (find) find.addEventListener('input', () => {
    const q = find.value.trim().toLowerCase(); let n = 0;
    $$('#pk-list .pk').forEach((a) => { const hit = !q || a.dataset.q.includes(q); a.hidden = !hit; n += hit ? 1 : 0; });
    $$('#pk-list .pk-group').forEach((g) => { let e = g.nextElementSibling, any = false; while (e && !e.classList.contains('pk-group')) { any = any || !e.hidden; e = e.nextElementSibling; } g.hidden = !any; });
    $('#pk-count').textContent = `${n} of ${d.tabs.length}`;
  });
  const jump = $('[data-jump="picker"]');
  if (jump) jump.addEventListener('click', (e) => { e.preventDefault(); $('#picker').scrollIntoView({block: 'start', behavior: 'smooth'}); setTimeout(() => find && find.focus(), 350); });
  if (S.params.sop) window.scrollTo(0, 0);
}


// ── Asks: every missing input as one question with its own field ─────────────
function fieldHtml(f) {
  const id = `f-${f.key.replace(/[^a-z0-9]/gi, '-')}`;
  const common = `id="${id}" name="${esc(f.key)}" aria-label="${esc(f.label)}" autocomplete="${f.type === 'secret' ? 'new-password' : 'off'}" spellcheck="false"`;
  let input;
  if (f.type === 'choice') input = `<select ${common}>${f.value ? '' : '<option value="">choose…</option>'}${f.options.map((o) => `<option value="${esc(o)}" ${String(f.value) === o ? 'selected' : ''}>${esc(o.replace(/_/g, ' '))}</option>`).join('')}</select>`;
  else if (f.type === 'list' || f.type === 'long') input = `<textarea ${common} rows="${f.type === 'list' ? 4 : 2}" placeholder="${esc(f.placeholder)}">${esc(f.value || '')}</textarea>`;
  else input = `<input ${common} type="${{secret: 'password', email: 'email', number: 'number'}[f.type] || 'text'}" value="${esc(f.value || '')}" placeholder="${esc(f.placeholder)}">`;
  return `<label class="fld" for="${id}">${f.q ? `<span class="q">${esc(f.q)}</span>` : ''}<span class="k">${esc(f.label)}${f.set === true ? ' <span class="green">· saved</span>' : f.set === false ? ' <span class="dim">· not set</span>' : ''}</span>${input}</label>`;
}
function formValues(form) { const v = {}; form.querySelectorAll('[name]').forEach((el) => { if (el.value !== '') v[el.name] = el.value; }); return v; }
async function renderAsks() {
  const d = S.data.asks || (S.data.asks = await api('/api/v3/asks')); nav((S.data.today || {}).open);
  const next = S.askNext ? `<p class="nextline"><a class="bordered" href="${esc(S.askNext.go)}">${esc(S.askNext.label)}</a></p>` : '';
  const cards = d.asks.map((a) => `<form class="askcard" data-ask-form="${esc(a.id)}" id="ask-${esc(a.id.replace(/[^a-z0-9]/gi, '-'))}">
      <div class="tagline"><span class="tag ${esc(a.tone)}">${esc(a.group)}</span><span class="counter">${a.n} of ${d.open}</span></div>
      <h3>${esc(a.title)}</h3><p class="body">${esc(a.why)}</p>
      <p class="where"><span class="k">WHERE TO FIND IT</span>${esc(a.where)}</p>
      ${a.command ? `<pre class="cmdline">${esc(a.command)}</pre>` : ''}
      ${a.fields.length ? `<div class="flds">${a.fields.map(fieldHtml).join('')}</div>` : ''}
      <div class="acts">${a.button ? `<button type="submit" class="primary">${esc(a.button)}</button>` : ''}${a.link ? verbLink(a.link, 'evidence') : ''}${a.evidence ? verbLink(a.evidence, 'evidence') : ''}<span class="unblocks">UNBLOCKS · ${esc(a.unblocks)}</span></div>
    </form>`).join('');
  const waiting = d.waiting.length ? `<section class="sec"><div class="lab">Waiting on other teams · not yours to answer</div>${d.waiting.map((w) => `<div class="row"><div class="grow"><span class="text">${esc(w.title)}</span><p class="small">${esc(w.why)}${w.unblocks ? ' · unblocks ' + esc(w.unblocks) : ''}</p></div><a class="verb accent" href="#" data-delivered="${esc(w.id)}">${esc(w.verb)}</a></div>`).join('')}</section>` : '';
  $('#main').innerHTML = `<section class="sec"><div class="lab">Asks</div><p class="lead">${esc(d.lead)}</p>${next}</section>
    ${d.asks.length ? `<section class="sec asks">${cards}</section>` : `<section class="sec clear"><p class="line">Nothing is missing.</p><p><a class="bordered" href="#today">GO TO TODAY'S DECISIONS</a></p></section>`}${waiting}`;
  if (S.params.focus) { const el = $(`#ask-${CSS.escape(S.params.focus.replace(/[^a-z0-9]/gi, '-'))}`); if (el) { el.scrollIntoView({block: 'start'}); const i = el.querySelector('input,select,textarea'); if (i) i.focus({preventScroll: true}); } }
}
async function answerAsk(id, values, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await api(`/api/v3/asks/${encodeURIComponent(id)}/answer`, {method: 'POST', body: {values}});
    toast(r.toast || (r.ok ? 'Saved' : 'Not saved'));
    if (r.asks) S.data.asks = r.asks;
    S.askNext = r.ok ? (r.next || null) : S.askNext;
    if (r.ok) { S.data.today = null; S.data.alerts = null; S.data.engine = null; S.params = {}; }
    if (r.ok || r.asks) await renderAsks(); else if (btn) btn.disabled = false;
  } catch (e) { toast(`Not saved · ${e.message}`); if (btn) btn.disabled = false; }
}

// ── Alerts: market alerts in rows and verbs ──────────────────────────────────
async function apiText(path) { const r = await fetch(path, {headers: {'X-Local-Token': TOKEN}}); if (!r.ok) throw new Error(String(r.status)); return r.text(); }
async function renderAlerts() {
  const d = S.data.alerts || (S.data.alerts = await api('/api/v3/alerts')); nav((S.data.today || {}).open);
  const rows = (label, html) => `<section class="sec"><div class="lab">${label}</div>${html}</section>`;
  $('#main').innerHTML = `<section class="sec"><div class="lab">Market alerts</div><p class="lead">${esc(d.lead)}</p>
      <p class="small" style="margin-top:14px">${esc(d.launch.note)} ${esc(d.autonomy)}</p>
      <p class="small ${d.telegram.on ? 'green' : ''}" style="margin-top:8px">${esc(d.telegram.line)}${d.telegram.configured ? '' : ' <a href="#asks?focus=setting:telegram" class="accent">SET IT UP →</a>'}</p>
      <div class="acts" style="margin-top:22px"><button class="primary" data-al="brief">${esc(d.launch.label)}</button><button class="bordered" data-al="dry">SEE WHAT WOULD GO OUT NOW</button><button class="bordered" data-al="test">SEND A TEST TO THE TEST USERS</button><button class="${d.kill ? 'bordered' : 'defer'}" data-al="kill" data-on="${d.kill ? '0' : '1'}">${d.kill ? 'LIFT THE STOP' : 'STOP ALL ALERTS'}</button></div>
      <div id="al-out"></div></section>
    ${d.blockers.length ? rows('Before it can really run', d.blockers.map((b) => `<div class="row"><span class="tag amber" style="flex:0 0 190px">${esc(b.check)}</span><div class="grow"><span class="body">${esc(b.detail)}</span>${b.fix ? `<p class="small" style="margin:6px 0 0"><span class="dim">TO FIX · </span>${esc(b.fix)}</p>` : ''}</div><a class="verb accent" href="${esc(b.go)}">${esc(b.verb)}</a></div>`).join('')) : ''}
    ${rows('The plan in one look', d.summary.map((r) => `<div class="row rules"><span class="k">${esc(r.k)}</span><span class="v">${esc(r.v)}</span></div>`).join(''))}
    ${rows(`What it will say · word for word${d.copy_blocking ? ` · ${d.copy_blocking} blocked by the copy rules` : ''}`, d.copy.map((c) => `<div class="row"><span class="tag dim" style="flex:0 0 150px">${esc(c.signal)}</span><div class="grow"><span class="text">${esc(c.title)}</span><p class="small">${esc(c.body)}</p></div><span class="verb ${c.ok ? 'green' : 'magenta'}">${c.ok ? 'PASSES THE RULES' : esc(c.lint).toUpperCase()}</span></div>`).join(''))}
    ${rows('When it fires', d.signals.map((g) => `<div class="row"><div class="grow"><span class="text">${esc(g.label)}</span><p class="small">${esc(g.when)}</p></div><span class="small" style="flex:0 0 230px">${esc(g.cap)} · goes out ${esc(g.lane)}</span></div>`).join(''))}
    ${rows('Who gets it · tick the cohorts to launch · one MoEngage campaign each', d.cohorts.map((c) => `<div class="row"><label class="pick"><input type="checkbox" data-cohort="${esc(c.id)}" ${c.locked ? 'disabled' : ''} ${c.id === 'internal' || c.active ? 'checked' : ''}></label><div class="grow"><span class="text">${esc(c.label)}</span><p class="small">${esc(c.segment)} · ${esc(c.control)} · ${esc(c.why)}</p></div><span class="verb ${esc(c.tone)}">${esc(c.state)}</span></div>`).join('') + `<p class="small" style="margin-top:14px">Wider rollout · ${esc(d.promotion.progress)} · ${esc(d.promotion.line)}</p>`)}
    ${rows('Sent today', d.fires.length ? d.fires.map((f) => `<div class="row"><span class="small" style="flex:0 0 60px">${esc(f.at)}</span><div class="grow"><span class="text">${esc(f.title)}</span><p class="small">${esc(f.body)}</p></div><span class="small">${esc(f.to)} · by ${esc(f.source)}</span></div>`).join('') : '<div class="row"><span class="body">Nothing has gone out today.</span></div>')}
    <section class="sec"><p>${verbLink(d.ops, 'bordered')}</p></section>`;
}
async function alertsAction(what, btn) {
  const out = $('#al-out'); btn.disabled = true;
  try {
    if (what === 'kill') { const on = btn.dataset.on === '1'; await api('/api/alerts2/kill', {method: 'POST', body: {on}}); toast(on ? 'Stopped · nothing goes out until you lift it' : 'Stop lifted'); S.data.alerts = null; return renderAlerts(); }
    if (what === 'dry') {
      out.innerHTML = '<p class="small" style="margin-top:20px">Reading the market…</p>';
      const r = await api('/api/alerts2/discovery/run', {method: 'POST', body: {mode: 'dry_run'}});
      const dec = (r.summary.decisions || []);
      out.innerHTML = `<div class="inline"><div class="lab">Right now · ${r.detected} market facts seen · ${r.would_send} would go out · ${r.suppressed} held back</div>${dec.map((x) => `<div class="row"><div class="grow"><span class="text">${esc(x.title)}</span><p class="small">${esc(x.body)}</p></div><span class="verb ${x.reason ? 'dim' : 'green'}">${x.reason ? 'HELD · ' + esc(String(x.reason).replace(/_/g, ' ')) : 'WOULD GO OUT'}</span></div>`).join('') || '<p class="small">The market is quiet: nothing crossed a threshold.</p>'}<p class="small">A practice run. Nothing was sent and nothing was recorded.</p></div>`;
    }
    const picked = () => { const c = $$('[data-cohort]:checked').map((x) => x.dataset.cohort); return c.length ? c : ['internal']; };
    if (what === 'test') {
      const d = S.data.alerts; const t = d.test_users;
      out.innerHTML = `<form class="inline askcard plain" id="al-test"><div class="lab">A test send · only the test users get it</div>
        <div class="flds"><label class="fld"><span class="k">WHICH ALERT</span><select name="copy">${d.copy.filter((c) => c.ok).map((c, i) => `<option value="${i}">${esc(c.signal)} · ${esc(c.title)}</option>`).join('')}</select></label>
        <label class="fld"><span class="k">SEND IT TO</span><select name="dest"><option value="moengage">MoEngage test users (push on their phones)</option><option value="telegram" ${d.telegram.on ? 'selected' : ''} ${d.telegram.configured ? '' : 'disabled'}>Telegram chat${d.telegram.configured ? '' : ' (not set up yet)'}</option></select></label>
        <label class="fld"><span class="k">TEST USERS · EMAIL OR CUSTOMER ID · ONE PER LINE · UP TO ${t.max}</span><textarea name="users" rows="3" placeholder="${t.count ? esc('saved: ' + t.masked.join(', ') + ' · leave empty to use them') : 'you@coindcx.com'}"></textarea></label></div>
        <div class="acts"><button type="submit" class="primary">SEND THE TEST NOW</button><span class="small">Goes through MoEngage's test API to these people only. The list is stored encrypted and shown masked.</span></div></form>`;
      $('#al-test').addEventListener('submit', async (ev) => {
        ev.preventDefault(); const f = ev.target; const b = f.querySelector('[type=submit]'); b.disabled = true;
        const c = d.copy.filter((x) => x.ok)[Number(f.copy.value) || 0];
        try {
          if (f.dest.value === 'telegram') { await api('/api/telegram/send', {method: 'POST', body: {title: c.title, body: c.body, signal: c.signal}}); toast('Test posted to the Telegram chat'); }
          else { const r = await api('/api/test-send', {method: 'POST', body: {title: c.title, body: c.body, name: `MA2 test · ${c.signal}`, users: f.users.value}}); toast(`Test sent to ${r.sent_to} · ${r.status}`); }
          S.data.alerts = null; S.data.asks = null; }
        catch (e) { toast(`Not sent · ${e.message}`); }
        b.disabled = false;
      });
    }
    if (what === 'brief') {
      out.innerHTML = '<p class="small" style="margin-top:20px">Writing the brief…</p>';
      S.pickedCohorts = picked();
      const md = await apiText('/api/alerts2/discovery/brief?format=md&cohorts=' + encodeURIComponent(S.pickedCohorts.join(',')));
      out.innerHTML = `<div class="inline"><div class="lab">The launch brief · read it before anything is queued</div><pre class="brief">${esc(md)}</pre>
        <label class="readit"><input type="checkbox" id="al-read"> I have read the copy, the audience, the caps and the stop rules.</label>
        <div class="acts"><button class="primary" data-al="launch" disabled id="al-launch">QUEUE THE LAUNCH FOR ${S.pickedCohorts.length} COHORT${S.pickedCohorts.length === 1 ? '' : 'S'}</button><span class="small">Queues the MoEngage draft and the permission to fire. Both still wait for your approval on Today.</span></div></div>`;
      $('#al-read').addEventListener('change', (e) => { $('#al-launch').disabled = !e.target.checked; });
    }
    if (what === 'launch') {
      const r = await api('/api/alerts2/discovery/launch', {method: 'POST', body: {reviewed: true, cohort_ids: S.pickedCohorts || ['internal']}});
      toast(r.needs_review ? 'Read the brief first' : 'Queued · two approvals are waiting on Today'); S.data.alerts = null; S.data.today = null; S.data.asks = null; location.hash = '#today';
    }
  } catch (e) { toast(`Not done · ${e.message}`); }
  finally { btn.disabled = false; }
}

// ── Engine: connection, model, rhythm, background jobs ───────────────────────
async function renderEngine() {
  const d = S.data.engine || (S.data.engine = await api('/api/v3/engine')); nav((S.data.today || {}).open);
  $('#main').innerHTML = `<section class="sec"><div class="lab">Engine</div><p class="lead">${esc(d.lead)}</p>
      <div class="facts" style="margin-top:26px">${d.state.map((f) => `<div class="fact"><span class="k">${esc(f.k)}</span><span class="v ${esc(f.tone)}">${esc(f.v)}</span><span class="small" style="display:block;margin-top:4px">${esc(f.note)}</span></div>`).join('')}</div>
      <div class="acts">${d.asks_open ? `<a class="primary" href="#asks">ANSWER THE ${d.asks_open} MISSING</a>` : ''}<button class="bordered" data-en="verify">CHECK THE MOENGAGE CONNECTION</button></div><div id="en-out"></div></section>
    ${d.groups.map((g, i) => `<section class="sec"><div class="lab">${esc(g.title)}</div><p class="small" style="margin:-6px 0 12px">${esc(g.note)}</p><form class="askcard plain" data-settings-form="${i}"><div class="flds two">${g.fields.map(fieldHtml).join('')}</div><div class="acts"><button type="submit" class="bordered">SAVE ${esc(g.title.toUpperCase())}</button></div></form></section>`).join('')}
    <section class="sec"><div class="lab">Test users · every draft is test-sent to them</div><p class="small" style="margin:-6px 0 12px">${d.test_users.count ? esc(`${d.test_users.count} saved: ${d.test_users.masked.join(', ')}`) : 'None saved yet.'} Stored encrypted; saving replaces the list.</p>
      <form class="askcard plain" id="en-testers"><div class="flds"><label class="fld"><span class="k">EMAIL OR CUSTOMER ID · ONE PER LINE · UP TO ${d.test_users.max}</span><textarea name="users" rows="3" placeholder="you@coindcx.com"></textarea></label></div><div class="acts"><button type="submit" class="bordered">SAVE THE TEST USERS</button></div></form></section>
    <section class="sec"><div class="lab">Background jobs</div>${d.jobs.map((j) => `<div class="row"><span class="text" style="flex:0 0 230px">${esc(j.name)}</span><span class="grow small ${j.tone === 'green' ? '' : esc(j.tone)}">${esc(j.status)}</span><a class="verb accent" href="#" data-job="${esc(j.job)}">${esc(j.verb)}</a></div>`).join('')}</section>
    <section class="sec"><div class="lab">Skills the brain really read · last 7 days</div><p class="small" style="margin:-6px 0 12px">${esc(d.skills_note || '')}</p>${(d.skills || []).map((k) => `<div class="row"><span class="text" style="flex:0 0 230px">${esc(k.name)}</span><span class="grow small">${esc(k.status)}</span>${verbLink(k)}</div>`).join('') || '<div class="row"><span class="body">No skill has been read yet. The next question or draft will show up here.</span></div>'}</section>
    <section class="sec"><div class="lab">What the brain could not do · ${d.challenges.open} open · ${d.challenges.building} being built · ${d.challenges.resolved} fixed</div><p class="small" style="margin:-6px 0 12px">Each one is a build prompt. On the build machine: <code>./cli.py challenges pull</code>, fix, ship, <code>./cli.py challenges resolve</code>. Here: <code>./cli.py challenges push</code> sends them as GitHub issues.</p>${d.challenges.rows.map((c) => `<div class="row"><span class="text" style="flex:0 0 230px">${esc(c.name)}</span><span class="grow small">${esc(c.status)}</span></div>`).join('') || '<div class="row"><span class="body">Nothing logged. The brain writes here when a task cannot be finished.</span></div>'}</section>
    <section class="sec"><div class="lab">More of the engine</div>${d.more.map((m) => `<div class="row"><span class="grow"></span>${verbLink(m)}</div>`).join('')}</section>`;
}

// ── Workbench: every operator module, opened inside this shell ───────────────
async function renderBench() {
  const d = S.data.bench || (S.data.bench = await api('/api/v3/workbench')); nav((S.data.today || {}).open);
  $('#main').innerHTML = `<section class="sec"><div class="lab">Workbench</div><p class="lead">${esc(d.lead)}</p></section>
    <section class="sec">${d.modules.map((m) => `<div class="row"><div class="grow"><span class="text">${esc(m.name)}</span><p class="small">${esc(m.what)}</p></div>${verbLink(m)}</div>`).join('')}</section>`;
}
async function renderTool() {
  const d = S.data.bench || (S.data.bench = await api('/api/v3/workbench')); nav((S.data.today || {}).open);
  const m = d.modules.find((x) => x.m === S.params.m) || d.modules[0];
  $('#main').innerHTML = `<div class="toolbar"><a href="#bench">← WORKBENCH</a><span class="text">${esc(m.name)}</span><span class="small">${d.modules.map((x) => `<a href="#tool?m=${x.m}" class="${x.m === m.m ? 'on' : ''}">${esc(x.name)}</a>`).join('')}</span></div>
    <iframe class="tool" title="${esc(m.name)}" src="/ops?embed=1#${esc(m.m)}"></iframe>`;
}

// ── Ask ──────────────────────────────────────────────────────────────────────
async function ask(q, silent) {
  q = (q || '').trim(); if (!q) return;
  const out = $('#ask-out'); out.hidden = false;
  out.insertAdjacentHTML('afterbegin', `<div class="reply"><div class="q">${esc(q.slice(0, 120))}</div><div class="body">Thinking…</div></div>`);
  const slot = out.firstElementChild.querySelector('.body');
  try { const r = await api('/api/brain/ask', {method: 'POST', body: {message: q}}); slot.textContent = r.reply || '(no reply)'; if (!silent) toast('The brain answered below'); }
  catch (e) { slot.textContent = `Could not answer · ${e.message}`; }
  if (!silent) { const i = $('#ask-in'); i.value = ''; $('#ask-ghost').hidden = false; }
}

// ── render / events ──────────────────────────────────────────────────────────
async function render() {
  const fn = {today: renderToday, asks: renderAsks, rivals: renderRivals, ideas: renderIdeas, running: renderRunning, plays: renderPlays, alerts: renderAlerts, engine: renderEngine, bench: renderBench, tool: renderTool}[S.screen] || renderToday;
  document.body.classList.toggle('wide', S.screen === 'tool');
  if (S.screen !== 'asks') S.askNext = null;
  nav((S.data.today || {}).open);
  try { if (S.screen !== 'today' && !S.data.today) api('/api/v3/today').then((d) => { S.data.today = d; nav(d.open); }).catch(() => {}); await fn(); }
  catch (e) { $('#main').innerHTML = `<section class="sec"><p class="lead">The brain is not answering.</p><p class="body">${esc(e.message)} · the engine may be restarting; this page retries when you switch screens.</p></section>`; }
}
document.addEventListener('click', async (e) => {
  const rs = e.target.closest('[data-resolve]'); if (rs) { e.preventDefault(); return resolve(rs.dataset.id, rs.dataset.resolve, rs); }
  const a = e.target.closest('[data-ask]'); if (a) { e.preventDefault(); return ask(a.dataset.ask, true); }
  const al = e.target.closest('[data-al]'); if (al) { e.preventDefault(); return alertsAction(al.dataset.al, al); }
  const tp = e.target.closest('[data-test-proposal]'); if (tp) {
    e.preventDefault(); tp.textContent = 'SENDING…';
    try { const r = await api('/api/test-send', {method: 'POST', body: {proposal_id: Number(tp.dataset.testProposal)}}); toast(`Test sent to ${r.sent_to} · ${r.status}`); }
    catch (err) { toast(/no test users/.test(err.message) ? 'No test users yet · add them on Asks' : `Not sent · ${err.message}`); if (/no test users/.test(err.message)) location.hash = '#asks?focus=setting:test_users'; }
    tp.textContent = 'SEND A TEST TO THE TEST USERS'; return;
  }
  const dl = e.target.closest('[data-delivered]'); if (dl) { e.preventDefault(); return answerAsk(dl.dataset.delivered, {}, null); }
  const jb = e.target.closest('[data-job]'); if (jb) {
    e.preventDefault(); jb.textContent = 'RUNNING…';
    try { const r = await api(`/api/refresh/run/${encodeURIComponent(jb.dataset.job)}`, {method: 'POST', body: {}}); toast(r.ok === false ? `It failed · ${r.error || ''}` : 'Done · refreshed just now'); } catch (err) { toast(`Not done · ${err.message}`); }
    S.data.engine = null; return renderEngine();
  }
  const en = e.target.closest('[data-en]'); if (en) {
    e.preventDefault(); en.disabled = true; const out = $('#en-out'); out.innerHTML = '<p class="small" style="margin-top:16px">Checking…</p>';
    try { const r = await api('/api/auth/test', {method: 'POST', body: {}}); const s = r.session || {}; const p = r.public_api || {};
      out.innerHTML = `<p class="small" style="margin-top:16px">${esc(s.mock_mode || s.mode === 'mock' ? 'Practice mode: there is no live connection to check. Answer the Asks and go live first.' : (p.ok ? 'MoEngage answered: the keys work.' : `MoEngage did not accept the keys · ${p.detail || s.detail || 'no detail'}`))}</p>`; }
    catch (err) { out.innerHTML = `<p class="small magenta" style="margin-top:16px">${esc(err.message)}</p>`; }
    en.disabled = false; return;
  }
  const pr = e.target.closest('[data-promote]'); if (pr) {
    e.preventDefault(); pr.disabled = true;
    try { const r = await api(`/api/v3/ideas/${encodeURIComponent(pr.dataset.promote)}/promote`, {method: 'POST', body: {}}); toast(r.toast || 'Handed to the brain'); if (r.ask) ask(r.ask, true); S.data.ideas = null; renderIdeas(); }
    catch (err) { toast(`Not done · ${err.message}`); pr.disabled = false; }
    return;
  }
});
document.addEventListener('submit', async (e) => {
  const f = e.target.closest('[data-ask-form]'); if (f) { e.preventDefault(); return answerAsk(f.dataset.askForm, formValues(f), f.querySelector('[type=submit]')); }
  const tu = e.target.closest('#en-testers'); if (tu) {
    e.preventDefault();
    try { const r = await api('/api/test-users', {method: 'POST', body: {users: tu.users.value}}); toast(`Saved · ${r.count} test user${r.count === 1 ? '' : 's'}`); S.data.engine = null; S.data.asks = null; await renderEngine(); }
    catch (err) { toast(`Not saved · ${err.message}`); }
    return;
  }
  const g = e.target.closest('[data-settings-form]'); if (g) {
    e.preventDefault(); const btn = g.querySelector('[type=submit]'); btn.disabled = true;
    try { const r = await api('/api/settings', {method: 'POST', body: {values: formValues(g)}}); toast(`Saved · ${(r.saved || []).length} setting${(r.saved || []).length === 1 ? '' : 's'}${(r.rejected || []).length ? ' · ' + r.rejected.length + ' refused' : ''}`); S.data.engine = null; S.data.asks = null; S.data.today = null; S.data.alerts = null; await renderEngine(); }
    catch (err) { toast(`Not saved · ${err.message}`); btn.disabled = false; }
  }
});
window.addEventListener('hashchange', route);
const askIn = $('#ask-in');
askIn.addEventListener('input', () => { $('#ask-ghost').hidden = !!askIn.value; });
askIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ask(askIn.value); } });
document.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); askIn.focus(); } if (e.key === 'Escape') { $('#ask-out').hidden = true; } });
tick(); setInterval(tick, 30000);
route();
