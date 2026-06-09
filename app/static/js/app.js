/* Flight Meetup Finder — vanilla front-end controller.
   Handles theme, the search form (+ live estimate), the polling results view
   with client-side re-sort/re-filter, and the saved-searches page. No bundler,
   no framework. Created 2026-06-09. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function sendJSON(url, method, body) {
  const r = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.status === 204 ? null : r.json();
}

/* ---- Theme (F-25) -------------------------------------------------- */
function initTheme() {
  const btn = $('#theme-toggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const next =
      document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('fmf-theme', next); } catch (e) { /* noop */ }
  });
}

/* ---- Formatting helpers ------------------------------------------- */
const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function fmtDateTime(iso) {
  const d = new Date(iso);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  return `${DOW[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}, ${hh}:${mm}`;
}
function fmtDuration(mins) {
  return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
}
function fmtDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return `${d.getDate()} ${MON[d.getMonth()]}`;
}
function stopsLabel(n) { return n === 0 ? 'direct' : `${n} stop${n > 1 ? 's' : ''}`; }

/* =================================================================== */
/* Index page                                                          */
/* =================================================================== */
function legRule(scope, leg) {
  const sel = $(`select[data-leg="${leg}"]`, scope);
  return { preset: sel ? sel.value : 'any' };
}
function travellerFilters(letter) {
  const scope = $(`[data-traveller="${letter}"]`);
  const dur = $('[data-field="max_duration_hours"]', scope).value;
  const tf = {
    outbound: legRule(scope, 'outbound'),
    return: legRule(scope, 'return'),
    max_stops: $('[data-field="max_stops"]', scope).value,
  };
  if (dur) tf.max_duration_hours = parseFloat(dur);
  return tf;
}
function checkedValues(root) {
  return $$('input[type="checkbox"]:checked', root).map((c) => c.value);
}
function weekdays(which) {
  return checkedValues($(`[data-weekdays="${which}"]`)).map(Number);
}

function gatherRequest() {
  const form = $('#search-form');
  const mode = form.mode.value;
  const req = {
    mode,
    outbound_start: form.outbound_start.value,
    outbound_end: form.outbound_end.value,
    outbound_weekdays: weekdays('outbound'),
    return_start: form.return_start.value,
    return_end: form.return_end.value,
    return_weekdays: weekdays('return'),
    min_nights: parseInt(form.min_nights.value, 10),
    max_nights: parseInt(form.max_nights.value, 10),
    traveller_a: travellerFilters('a'),
  };
  if (mode === 'meetup') {
    const dests = checkedValues($('#dest-list'));
    req.destinations = dests.length ? dests : null;
    const bo = checkedValues($('#b-origins'));
    req.b_origins = bo.length ? bo : ['LIS'];
    req.traveller_b = travellerFilters('b');
    if (form.max_arrival_gap_hours.value)
      req.max_arrival_gap_hours = parseFloat(form.max_arrival_gap_hours.value);
    if (form.max_departure_gap_hours.value)
      req.max_departure_gap_hours = parseFloat(form.max_departure_gap_hours.value);
    if (form.max_combined_gbp.value)
      req.max_combined_gbp = parseFloat(form.max_combined_gbp.value);
  } else {
    const targets = checkedValues($('#visit-targets'));
    req.destinations = targets.length ? targets : ['LIS'];
    req.hidden_city = form.hidden_city.checked;
    if (form.max_price_gbp.value)
      req.max_price_gbp = parseFloat(form.max_price_gbp.value);
  }
  return req;
}

function applyMode(mode) {
  $('#mode').value = mode;
  $$('.tab').forEach((t) =>
    t.classList.toggle('is-active', t.dataset.tab === mode));
  $$('[data-mode-only]').forEach((el) => {
    el.hidden = el.dataset.modeOnly !== mode;
  });
}

function setDefaultDates() {
  const form = $('#search-form');
  if (form.outbound_start.value) return;
  const iso = (days) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return d.toISOString().slice(0, 10);
  };
  form.outbound_start.value = iso(30);
  form.outbound_end.value = iso(37);
  form.return_start.value = iso(33);
  form.return_end.value = iso(44);
}

async function loadDestinationChecklist() {
  const box = $('#dest-list');
  if (!box) return;
  try {
    const rows = await getJSON('/api/destinations?enabled_only=true');
    box.innerHTML = rows.map((r) =>
      `<label class="chk"><input type="checkbox" value="${r.iata}" checked>` +
      `${r.iata} · ${r.name}</label>`).join('');
  } catch (e) {
    box.innerHTML = '<p class="hint">Could not load destinations.</p>';
  }
}

let estimateTimer = null;
async function refreshEstimate() {
  const out = $('#estimate');
  if (!out) return;
  try {
    const { estimated_queries: n } = await sendJSON(
      '/api/estimate', 'POST', gatherRequest());
    out.textContent = `≈ ${n} scrape ${n === 1 ? 'query' : 'queries'}`;
    $('#estimate-warn').hidden = n <= 200;
  } catch (e) {
    out.textContent = 'Fill in the dates to estimate.';
  }
}
function scheduleEstimate() {
  clearTimeout(estimateTimer);
  estimateTimer = setTimeout(refreshEstimate, 350);
}

function initIndex() {
  applyMode('meetup');
  setDefaultDates();
  loadDestinationChecklist().then(refreshEstimate);

  $$('.tab').forEach((tab) =>
    tab.addEventListener('click', () => {
      applyMode(tab.dataset.tab);
      scheduleEstimate();
    }));
  $('#search-form').addEventListener('input', scheduleEstimate);
  $('#search-form').addEventListener('change', scheduleEstimate);

  $('#search-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('#launch');
    btn.disabled = true;
    try {
      const { job_id } = await sendJSON('/api/search', 'POST', gatherRequest());
      window.location.href = `/search/${job_id}`;
    } catch (e) {
      btn.disabled = false;
      alert('Could not start the search: ' + e.message);
    }
  });

  $('#save-search').addEventListener('click', async () => {
    const name = prompt('Name this search:');
    if (!name) return;
    try {
      await sendJSON('/api/saved-searches', 'POST',
        { name, request: gatherRequest() });
      alert('Saved.');
    } catch (e) {
      alert('Could not save: ' + e.message);
    }
  });
}

/* =================================================================== */
/* Results page (F-14, F-21, F-22)                                     */
/* =================================================================== */
function legRow(label, leg) {
  return `<div class="leg"><span>${label}: <strong>${leg.airline}</strong></span>` +
    `<span>${fmtDateTime(leg.depart_dt)} → ${fmtDateTime(leg.arrive_dt)}</span>` +
    `<span>${fmtDuration(leg.duration_minutes)} · ${stopsLabel(leg.stops)}</span>` +
    `<a href="${leg.deep_link}" target="_blank" rel="noopener">Google&nbsp;Flights ↗</a></div>`;
}

function travellerBlock(t) {
  return `<div class="itin"><h4>${t.name} · ${t.origin}</h4>` +
    legRow('Out', t.outbound) + legRow('Back', t.return) + '</div>';
}

function maxLegStops(p) {
  const legs = [];
  [p.traveller_a, p.traveller_b].forEach((t) => {
    if (t) { legs.push(t.outbound.stops, t.return.stops); }
  });
  return Math.max(...legs);
}
function totalDuration(p) {
  let total = 0;
  [p.traveller_a, p.traveller_b].forEach((t) => {
    if (t) { total += t.outbound.duration_minutes + t.return.duration_minutes; }
  });
  return total;
}

function renderResult(p) {
  const el = document.createElement('article');
  el.className = 'result';
  el.dataset.price = p.combined_gbp;
  el.dataset.duration = totalDuration(p);
  el.dataset.gap = p.arrival_gap_minutes != null ? p.arrival_gap_minutes : 0;
  el.dataset.maxstops = maxLegStops(p);
  const withB = p.b_origin ? ` · ${p.traveller_b.name} from ${p.b_origin}` : '';
  let badges = `<span class="badge">${fmtDate(p.outbound_date)} → ${fmtDate(p.return_date)}</span>`;
  if (p.arrival_gap_minutes != null)
    badges += `<span class="badge">arrival gap ${fmtDuration(p.arrival_gap_minutes)}</span>`;
  if (p.departure_gap_minutes != null)
    badges += `<span class="badge">departure gap ${fmtDuration(p.departure_gap_minutes)}</span>`;
  el.innerHTML =
    `<div class="result-head"><h3 class="result-dest">${p.destination}${withB}</h3>` +
    `<div class="result-price">£${p.combined_gbp.toFixed(0)}</div></div>` +
    `<div class="badges">${badges}</div>` +
    travellerBlock(p.traveller_a) +
    (p.traveller_b ? travellerBlock(p.traveller_b) : '');
  return el;
}

function renderHiddenCity(items) {
  const section = $('#hidden-city');
  if (!items.length) { section.hidden = true; return; }
  section.hidden = false;
  $('#hidden-city-list').innerHTML = items.map((p) =>
    `<article class="result"><div class="result-head">` +
    `<h3 class="result-dest">${p.destination} · ${p.destination_name || ''}</h3>` +
    `<a class="btn ghost small" href="${p.deep_link}" target="_blank" ` +
    `rel="noopener">Check on ${fmtDate(p.outbound_date)} ↗</a></div></article>`).join('');
}

const ResultsView = {
  data: [],
  render() {
    const sortBy = $('#sort-by').value;
    const maxPrice = parseFloat($('#filter-price').value);
    const maxStops = $('#filter-stops').value;
    let rows = this.data.slice();
    if (!Number.isNaN(maxPrice)) rows = rows.filter((p) => p.combined_gbp <= maxPrice);
    if (maxStops !== 'any')
      rows = rows.filter((p) => maxLegStops(p) <= parseInt(maxStops, 10));
    rows.sort((a, b) => {
      if (sortBy === 'total_duration') return totalDuration(a) - totalDuration(b);
      if (sortBy === 'arrival_gap_minutes')
        return (a.arrival_gap_minutes || 0) - (b.arrival_gap_minutes || 0);
      return a.combined_gbp - b.combined_gbp;
    });
    const box = $('#results');
    box.innerHTML = '';
    if (!rows.length) {
      box.innerHTML = '<p class="empty">No matching itineraries yet.</p>';
    } else {
      rows.forEach((p) => box.appendChild(renderResult(p)));
    }
    $('#result-count').textContent =
      `${rows.length} shown / ${this.data.length} found`;
  },
};

function initResults() {
  const root = $('#results-root');
  const jobId = root.dataset.jobId;
  ['#sort-by', '#filter-price', '#filter-stops'].forEach((s) =>
    $(s).addEventListener('input', () => ResultsView.render()));

  $('#cancel-job').addEventListener('click', async () => {
    try { await sendJSON(`/api/jobs/${jobId}/cancel`, 'POST'); } catch (e) { /* noop */ }
  });

  let active = true;
  async function poll() {
    if (!active) return;
    let job;
    try { job = await getJSON(`/api/jobs/${jobId}`); }
    catch (e) { setTimeout(poll, 1500); return; }

    const pct = job.queries_total
      ? Math.round((job.queries_done / job.queries_total) * 100) : 0;
    $('#progress-fill').style.width = `${pct}%`;
    const failed = job.queries_failed
      ? ` · ${job.queries_failed} failed` : '';
    $('#status-text').textContent =
      `${job.status} — ${job.queries_done}/${job.queries_total} queries${failed}`;

    ResultsView.data = job.results;
    ResultsView.render();
    renderHiddenCity(job.hidden_city);

    if (['done', 'failed', 'cancelled'].includes(job.status)) {
      active = false;
      $('#cancel-job').hidden = true;
      $('#progress-fill').style.width = '100%';
      return;
    }
    setTimeout(poll, 1000);
  }
  poll();
}

/* =================================================================== */
/* Saved page (F-23) + destination management                          */
/* =================================================================== */
async function loadSaved() {
  const box = $('#saved-list');
  const rows = await getJSON('/api/saved-searches');
  $('#saved-empty').hidden = rows.length > 0;
  box.innerHTML = rows.map((s) => {
    const last = s.last_run_at ? `Last run ${fmtDateTime(s.last_run_at)}` : 'Never run';
    return `<article class="result"><div class="result-head">` +
      `<h3 class="result-dest">${s.name}</h3>` +
      `<span class="badge">${s.mode}</span></div>` +
      `<p class="hint">${last}</p>` +
      `<div class="actions"><button class="btn primary small" data-run="${s.id}">Run</button>` +
      `<button class="btn ghost small" data-del="${s.id}">Delete</button></div></article>`;
  }).join('');

  $$('[data-run]', box).forEach((b) => b.addEventListener('click', async () => {
    const { job_id } = await sendJSON(`/api/saved-searches/${b.dataset.run}/run`, 'POST');
    window.location.href = `/search/${job_id}`;
  }));
  $$('[data-del]', box).forEach((b) => b.addEventListener('click', async () => {
    await sendJSON(`/api/saved-searches/${b.dataset.del}`, 'DELETE');
    loadSaved();
  }));
}

async function loadDestManager() {
  const box = $('#dest-manage');
  const rows = await getJSON('/api/destinations');
  box.innerHTML = rows.map((r) =>
    `<label class="chk"><input type="checkbox" data-iata="${r.iata}" ` +
    `${r.enabled ? 'checked' : ''}>${r.iata} · ${r.name}</label>`).join('');
  $$('input[data-iata]', box).forEach((c) =>
    c.addEventListener('change', () =>
      sendJSON(`/api/destinations/${c.dataset.iata}`, 'PATCH', { enabled: c.checked })));
}

function initSaved() {
  loadSaved();
  loadDestManager();
  $('#add-dest').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const code = $('#add-dest-code').value.trim().toUpperCase();
    if (!code) return;
    try {
      await sendJSON('/api/destinations', 'POST', { iata: code });
      $('#add-dest-code').value = '';
      loadDestManager();
    } catch (e) {
      alert('Could not add: ' + e.message);
    }
  });
}

/* ---- Boot --------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  if (window.FMF_PAGE === 'index') initIndex();
  else if (window.FMF_PAGE === 'results') initResults();
  else if (window.FMF_PAGE === 'saved') initSaved();
});
