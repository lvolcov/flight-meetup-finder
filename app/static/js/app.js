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

/* Human-friendly duration estimate: "under a minute", "about 4 min",
   "about 1 hr 20 min". Used for every search-time estimate in the app. */
function fmtETA(seconds) {
  if (seconds < 60) return 'under a minute';
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `about ${mins} min`;
  const hrs = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `about ${hrs} hr ${rem} min` : `about ${hrs} hr`;
}

/* Dates are entered as dd/mm/yyyy (British) and sent to the API as ISO. */
function ukToISO(value) {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value.trim());
  if (!m) return null;
  const [, dd, mm, yyyy] = m;
  if (+mm < 1 || +mm > 12 || +dd < 1 || +dd > 31) return null;
  return `${yyyy}-${mm}-${dd}`;
}
function isoToUK(iso) {
  const [yyyy, mm, dd] = iso.split('-');
  return `${dd}/${mm}/${yyyy}`;
}

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
  const dates = {};
  for (const field of ['outbound_start', 'outbound_end', 'return_start', 'return_end']) {
    const iso = ukToISO(form[field].value);
    if (!iso) throw new Error(`Enter ${field.replace('_', ' ')} as dd/mm/yyyy`);
    dates[field] = iso;
  }
  const req = {
    mode,
    outbound_start: dates.outbound_start,
    outbound_end: dates.outbound_end,
    outbound_weekdays: weekdays('outbound'),
    return_start: dates.return_start,
    return_end: dates.return_end,
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
  const uk = (days) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return isoToUK(d.toISOString().slice(0, 10));
  };
  form.outbound_start.value = uk(30);
  form.outbound_end.value = uk(37);
  form.return_start.value = uk(33);
  form.return_end.value = uk(44);
}

async function loadDestinationChecklist() {
  const box = $('#dest-list');
  if (!box) return;
  try {
    const rows = await getJSON('/api/destinations?enabled_only=true');
    box.innerHTML = rows.map((r) =>
      `<label class="chk" data-schengen="${r.schengen ? 1 : 0}">` +
      `<input type="checkbox" value="${r.iata}" checked>` +
      `${r.iata} · ${r.name}` +
      (r.schengen ? '' : ' <span class="badge mini-badge">passport</span>') +
      `</label>`).join('');
    applySchengenOnly($('#schengen-only').checked);
  } catch (e) {
    box.innerHTML = '<p class="hint">Could not load destinations.</p>';
  }
}

/* Bulk de/select destinations outside the Schengen area (passport control
   when flying from Lisbon). State persists across visits. */
function applySchengenOnly(on) {
  $$('#dest-list label[data-schengen="0"] input').forEach((boxEl) => {
    boxEl.checked = !on;
  });
}
function initSchengenToggle() {
  const toggle = $('#schengen-only');
  if (!toggle) return;
  try { toggle.checked = localStorage.getItem('fmf-schengen-only') === '1'; }
  catch (e) { /* localStorage unavailable */ }
  toggle.addEventListener('change', () => {
    try {
      localStorage.setItem('fmf-schengen-only', toggle.checked ? '1' : '0');
    } catch (e) { /* noop */ }
    applySchengenOnly(toggle.checked);
    scheduleEstimate();
  });
}

/* Each date field is a dd/mm/yyyy text box plus a calendar button that opens
   the browser's native picker (a hidden type=date input). The picker writes
   its choice back in dd/mm/yyyy, so the display format never depends on the
   browser locale. */
function initDateFields() {
  $$('.date-field').forEach((wrap) => {
    const text = $('input[type="text"]', wrap);
    const pick = $('input.date-pick', wrap);
    $('.date-btn', wrap).addEventListener('click', () => {
      const iso = ukToISO(text.value);
      if (iso) pick.value = iso;
      try {
        pick.showPicker();
      } catch (e) {
        pick.focus();
        pick.click();
      }
    });
    pick.addEventListener('change', () => {
      if (!pick.value) return;
      text.value = isoToUK(pick.value);
      text.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });
}

/* Rerun an old search: first check the dates haven't passed and tell the
   user how many queries it will run and roughly how long it will take. */
async function rerunWithCheck(jobId) {
  let info;
  try {
    info = await getJSON(`/api/jobs/${jobId}/rerun-check`);
  } catch (e) {
    alert('Could not check this search: ' + e.message);
    return;
  }
  if (info.dates_in_past) {
    alert(`The dates of this search have already passed ` +
      `(last outbound day was ${isoToUK(info.outbound_end)}). ` +
      `Start a new search with fresh dates.`);
    return;
  }
  const cached = info.estimated_queries - info.uncached_queries;
  let msg = `Run this search again?\n\n` +
    `${info.estimated_queries} queries, ${fmtETA(info.estimated_seconds)}.`;
  if (cached > 0) msg += `\n(${cached} are already cached, so it's quicker.)`;
  if (!confirm(msg)) return;
  try {
    const { job_id } = await sendJSON(`/api/jobs/${jobId}/rerun`, 'POST');
    window.location.href = `/search/${job_id}`;
  } catch (e) {
    alert('Could not re-run: ' + e.message);
  }
}

/* Estimate of time remaining for a live job from its own progress rate. */
function jobRemainingSeconds(job) {
  if (!job.created_at || !job.queries_done || !job.queries_total) return null;
  const elapsed = (Date.now() - new Date(job.created_at).getTime()) / 1000;
  if (elapsed <= 0) return null;
  const rate = job.queries_done / elapsed;  // queries per second
  if (rate <= 0) return null;
  return Math.round((job.queries_total - job.queries_done) / rate);
}

/* Recent searches (server-side jobs) — visible from any device, and they
   keep running even if the page that launched them is closed. */
const JOB_STATUS_ICON = {
  pending: '…', running: '▶', done: '✓', failed: '✕', cancelled: '⊘',
};
async function loadRecentJobs() {
  const box = $('#recent-jobs');
  if (!box) return;
  let jobs = [];
  try { jobs = await getJSON('/api/jobs'); } catch (e) { return; }
  $('#recent-jobs-section').hidden = jobs.length === 0;
  box.innerHTML = jobs.map((j) => {
    const pct = j.queries_total
      ? Math.round((j.queries_done / j.queries_total) * 100) : 0;
    const live = j.status === 'running' || j.status === 'pending';
    let detail = live
      ? `${j.queries_done}/${j.queries_total} queries`
      : fmtDateTime(j.created_at);
    if (j.status === 'running') {
      const left = jobRemainingSeconds(j);
      if (left != null) detail += ` · ${fmtETA(left)} left`;
    }
    return `<div class="job-row" data-status="${j.status}">` +
      `<a class="job-link" href="/search/${j.id}" title="Open this search's results">` +
      `<span class="job-icon">${JOB_STATUS_ICON[j.status] || '?'}</span>` +
      `<span class="job-meta"><strong>${j.mode}</strong> · ${j.status} · ${detail}</span>` +
      `<span class="progress mini"><span class="progress-fill" style="width:${pct}%"></span></span>` +
      `</a>` +
      `<span class="job-actions">` +
      `<button type="button" class="icon-btn small" data-rerun="${j.id}" ` +
      `title="Search again with the same filters" aria-label="Run again">↻</button>` +
      `<button type="button" class="icon-btn small" data-delete="${j.id}" ` +
      `title="Delete this search and its results" aria-label="Delete">🗑</button>` +
      `</span></div>`;
  }).join('');

  $$('[data-rerun]', box).forEach((b) => b.addEventListener('click', () => {
    rerunWithCheck(b.dataset.rerun);
  }));
  $$('[data-delete]', box).forEach((b) => b.addEventListener('click', async () => {
    if (!confirm('Delete this search and its results?')) return;
    try {
      await sendJSON(`/api/jobs/${b.dataset.delete}`, 'DELETE');
      loadRecentJobs();
    } catch (e) {
      alert('Could not delete: ' + e.message);
    }
  }));

  if (jobs.some((j) => j.status === 'running' || j.status === 'pending')) {
    setTimeout(loadRecentJobs, 2000);
  }
}

let estimateTimer = null;
async function refreshEstimate() {
  const out = $('#estimate');
  if (!out) return;
  try {
    const est = await sendJSON('/api/estimate', 'POST', gatherRequest());
    const n = est.estimated_queries;
    const cached = n - est.uncached_queries;
    let text = `≈ ${n} scrape ${n === 1 ? 'query' : 'queries'}` +
      ` · ${fmtETA(est.estimated_seconds)}`;
    if (cached > 0) text += ` (${cached} already cached)`;
    out.textContent = text;
    $('#estimate-warn').hidden = est.uncached_queries <= 200;
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
  initDateFields();
  initSchengenToggle();
  loadDestinationChecklist().then(refreshEstimate);
  loadRecentJobs();

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
function legPrice(leg) {
  if (leg.price_gbp == null) return '';
  let price = `£${leg.price_gbp.toFixed(0)}`;
  if (leg.price_currency === 'EUR') {
    price += ` <span class="leg-orig">(€${leg.price_amount.toFixed(0)})</span>`;
  }
  return `<span class="leg-price">${price}</span>`;
}

function legRow(label, leg) {
  return `<div class="leg"><span>${label}: <strong>${leg.airline}</strong></span>` +
    `<span>${fmtDateTime(leg.depart_dt)} → ${fmtDateTime(leg.arrive_dt)}</span>` +
    `<span>${fmtDuration(leg.duration_minutes)} · ${stopsLabel(leg.stops)}</span>` +
    legPrice(leg) +
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
  $('#rerun-job').addEventListener('click', () => rerunWithCheck(jobId));
  $('#save-job').addEventListener('click', async () => {
    const name = prompt('Name this search:');
    if (!name) return;
    try {
      await sendJSON(`/api/jobs/${jobId}/save`, 'POST', { name });
      alert(`Saved — find it under “Saved”.`);
    } catch (e) {
      alert('Could not save: ' + e.message);
    }
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
    let eta = '';
    if (job.status === 'running') {
      const left = jobRemainingSeconds(job);
      if (left != null) eta = ` · ${fmtETA(left)} left`;
    }
    $('#status-text').textContent =
      `${job.status} — ${job.queries_done}/${job.queries_total} queries${failed}${eta}`;

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

  const byId = {};
  rows.forEach((s) => { byId[s.id] = s; });
  $$('[data-run]', box).forEach((b) => b.addEventListener('click', async () => {
    const saved = byId[b.dataset.run];
    const filters = saved ? saved.filters_json : null;
    if (filters && filters.outbound_end) {
      const today = new Date().toISOString().slice(0, 10);
      if (filters.outbound_end < today) {
        alert(`The dates of this search have already passed (last outbound ` +
          `day was ${isoToUK(filters.outbound_end)}). Edit it on the search ` +
          `page with fresh dates.`);
        return;
      }
      try {
        const est = await sendJSON('/api/estimate', 'POST', filters);
        const msg = `Run “${saved.name}”?\n\n${est.estimated_queries} ` +
          `queries, ${fmtETA(est.estimated_seconds)}.`;
        if (!confirm(msg)) return;
      } catch (e) { /* estimate is best-effort; still allow the run */ }
    }
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
    `${r.enabled ? 'checked' : ''}>${r.iata} · ${r.name}` +
    (r.schengen ? '' : ' <span class="badge mini-badge">passport</span>') +
    `</label>`).join('');
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

/* =================================================================== */
/* Found flights (F-36)                                                */
/* =================================================================== */
function fmtAgo(iso) {
  if (!iso) return '';
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} hour${hrs > 1 ? 's' : ''} ago`;
  const days = Math.floor(hrs / 24);
  return `${days} day${days > 1 ? 's' : ''} ago`;
}

function foundStatusHTML(f) {
  if (!f.check_status) return '<span class="found-status muted">Not checked yet</span>';
  const cls = { available: 'ok', gone: 'bad', error: 'warn' }[f.check_status] || 'muted';
  const when = f.checked_at ? ` · checked ${fmtAgo(f.checked_at)}` : '';
  return `<span class="found-status ${cls}">${f.check_note || f.check_status}${when}</span>`;
}

function renderFound(f) {
  const card = renderResult(f.payload);
  card.classList.add('found-card');
  card.dataset.foundId = f.id;
  card.insertAdjacentHTML('beforeend',
    `<div class="found-meta">` +
    `<span class="hint">Found ${fmtAgo(f.first_seen_at)}</span>` +
    `<span class="found-actions">` +
    `<button type="button" class="btn primary small" data-check="${f.id}">` +
    `Check if still available</button>` +
    `<button type="button" class="btn ghost small" data-remove="${f.id}">Remove</button>` +
    `</span></div>` +
    `<div class="found-status-row" data-status="${f.id}">${foundStatusHTML(f)}</div>`);
  return card;
}

const FoundView = {
  data: [],
  render() {
    const sort = $('#found-sort').value;
    const dest = $('#found-filter-dest').value.trim().toUpperCase();
    const hideGone = $('#found-hide-gone').checked;
    let rows = this.data.slice();
    if (dest) rows = rows.filter((f) => f.destination.toUpperCase().includes(dest));
    if (hideGone) rows = rows.filter((f) => f.check_status !== 'gone');
    rows.sort((a, b) => (sort === 'price'
      ? a.combined_gbp - b.combined_gbp
      : new Date(b.last_seen_at) - new Date(a.last_seen_at)));
    const box = $('#found-list');
    box.innerHTML = '';
    rows.forEach((f) => box.appendChild(renderFound(f)));
    $('#found-empty').hidden = this.data.length > 0;
    $('#found-count').textContent = this.data.length
      ? `${rows.length} shown / ${this.data.length} found` : '';
  },
};

async function checkFound(id, statusRow, btn) {
  btn.disabled = true;
  const label = btn.textContent;
  btn.textContent = 'Checking…';
  if (statusRow)
    statusRow.innerHTML = '<span class="found-status muted">Checking Google Flights…</span>';
  try {
    const updated = await sendJSON(`/api/found-flights/${id}/check`, 'POST');
    const idx = FoundView.data.findIndex((f) => String(f.id) === String(id));
    if (idx >= 0) FoundView.data[idx] = updated;
    if (statusRow) statusRow.innerHTML = foundStatusHTML(updated);
  } catch (e) {
    if (statusRow)
      statusRow.innerHTML = `<span class="found-status warn">Could not check (${e.message})</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function onFoundClick(ev) {
  const checkBtn = ev.target.closest('[data-check]');
  if (checkBtn) {
    await checkFound(checkBtn.dataset.check,
      $(`[data-status="${checkBtn.dataset.check}"]`), checkBtn);
    return;
  }
  const removeBtn = ev.target.closest('[data-remove]');
  if (removeBtn) {
    if (!confirm('Remove this flight from your Found list?')) return;
    const id = removeBtn.dataset.remove;
    try {
      await sendJSON(`/api/found-flights/${id}`, 'DELETE');
      FoundView.data = FoundView.data.filter((f) => String(f.id) !== String(id));
      FoundView.render();
    } catch (e) { alert('Could not remove: ' + e.message); }
  }
}

async function initFound() {
  ['#found-sort', '#found-filter-dest', '#found-hide-gone'].forEach((s) =>
    $(s).addEventListener('input', () => FoundView.render()));
  $('#found-list').addEventListener('click', onFoundClick);
  try {
    FoundView.data = await getJSON('/api/found-flights');
  } catch (e) { FoundView.data = []; }
  FoundView.render();
}

/* ---- Boot --------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
  initTheme();
  if (window.FMF_PAGE === 'index') initIndex();
  else if (window.FMF_PAGE === 'results') initResults();
  else if (window.FMF_PAGE === 'saved') initSaved();
  else if (window.FMF_PAGE === 'found') initFound();
});
