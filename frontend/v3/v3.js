'use strict';
const TOKEN = (document.querySelector('meta[name="local-token"]') || {}).content || '';
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const S = {screen: 'today', focusKey: null, resolved: new Set(), selectedSop: null, toast: '', clock: '', data: {}, params: {}};
const SCREENS = [['today', 'Today'], ['rivals', 'Rivals'], ['ideas', 'Ideas'], ['running', 'Running'], ['plays', 'Playbooks']];

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
  S.screen = SCREENS.some(([k]) => k === scr) ? scr : 'today';
  if (S.params.decision) S.focusKey = S.params.decision;
  if (S.params.sop) S.selectedSop = S.params.sop;
  render();
}
function nav(open) {
  $('#nav').innerHTML = SCREENS.map(([k, l]) => `<a href="#${k}" class="${S.screen === k ? 'on' : ''}">${l}${k === 'today' && open ? `<span class="count">${open}</span>` : ''}</a>`).join('');
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
      <div class="acts"><button class="primary" data-resolve="approve" data-id="${esc(focus.id)}">${esc(focus.primary.label)}</button>${verbLink(focus.evidence, 'evidence')}<button class="defer" data-resolve="defer" data-id="${esc(focus.id)}">${esc(focus.defer.label)}</button></div>
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
  const fn = {today: renderToday, rivals: renderRivals, ideas: renderIdeas, running: renderRunning, plays: renderPlays}[S.screen] || renderToday;
  nav((S.data.today || {}).open);
  try { if (S.screen !== 'today' && !S.data.today) api('/api/v3/today').then((d) => { S.data.today = d; nav(d.open); }).catch(() => {}); await fn(); }
  catch (e) { $('#main').innerHTML = `<section class="sec"><p class="lead">The brain is not answering.</p><p class="body">${esc(e.message)} · the engine may be restarting; this page retries when you switch screens.</p></section>`; }
}
document.addEventListener('click', async (e) => {
  const rs = e.target.closest('[data-resolve]'); if (rs) { e.preventDefault(); return resolve(rs.dataset.id, rs.dataset.resolve, rs); }
  const a = e.target.closest('[data-ask]'); if (a) { e.preventDefault(); return ask(a.dataset.ask, true); }
  const pr = e.target.closest('[data-promote]'); if (pr) {
    e.preventDefault(); pr.disabled = true;
    try { const r = await api(`/api/v3/ideas/${encodeURIComponent(pr.dataset.promote)}/promote`, {method: 'POST', body: {}}); toast(r.toast || 'Handed to the brain'); if (r.ask) ask(r.ask, true); S.data.ideas = null; renderIdeas(); }
    catch (err) { toast(`Not done · ${err.message}`); pr.disabled = false; }
    return;
  }
});
window.addEventListener('hashchange', route);
const askIn = $('#ask-in');
askIn.addEventListener('input', () => { $('#ask-ghost').hidden = !!askIn.value; });
askIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ask(askIn.value); } });
document.addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); askIn.focus(); } if (e.key === 'Escape') { $('#ask-out').hidden = true; } });
tick(); setInterval(tick, 30000);
route();
