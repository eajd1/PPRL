'use strict';
const $ = (id) => document.getElementById(id);
const state = { files: [], busy: false, online: false, job: null, preview: null, previewRequest: 0, review: null, reviewRequest: 0 };
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 20;
// Awaiting review is settled for polling, but can resume after confirmation.
const finalStatuses = new Set(['success', 'partial_success', 'failure', 'awaiting_review']);
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function formatBytes(size) { return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`; }
function setError(message) { $('selectionError').textContent = message; $('selectionError').hidden = !message; }
function updateControls() {
  $('processButton').disabled = state.busy || !state.files.length || !state.online;
  $('browseButton').disabled = state.busy;
  $('clearButton').disabled = state.busy;
  $('dropzone').classList.toggle('locked', state.busy);
  $('uploadHint').textContent = state.busy ? 'Preparing your files. You can review results below.' : state.files.length ? 'Your selected files are ready to process.' : 'Choose files to start preparing tokens.';
}
function renderSelection() {
  const list = $('selectedFiles'); list.replaceChildren();
  $('selectionHeading').hidden = !state.files.length;
  $('selectionCount').textContent = `${state.files.length} ${state.files.length === 1 ? 'file' : 'files'} selected`;
  state.files.forEach((file, index) => {
    const item = el('li'); item.append(el('span', 'csv-badge', 'CSV'));
    const copy = el('div', 'file-copy'); copy.append(el('div', 'file-name', file.name), el('div', 'file-meta', formatBytes(file.size))); item.append(copy);
    const remove = el('button', 'icon-button', '×'); remove.type = 'button'; remove.disabled = state.busy;
    remove.setAttribute('aria-label', `Remove ${file.name}`);
    remove.addEventListener('click', () => { state.files.splice(index, 1); renderSelection(); });
    item.append(remove); list.append(item);
  }); updateControls();
}
function addFiles(files) {
  if (state.busy) return;
  const errors = [];
  for (const file of files) {
    if (!/^[A-Za-z0-9][A-Za-z0-9._ -]{0,119}\.csv$/i.test(file.name)) { errors.push(`${file.name}: choose a CSV with a simple filename.`); continue; }
    if (!file.size) { errors.push(`${file.name}: the file is empty.`); continue; }
    if (file.size > MAX_BYTES) { errors.push(`${file.name}: exceeds 10 MB.`); continue; }
    if (state.files.some(f => f.name === file.name && f.size === file.size && f.lastModified === file.lastModified)) continue;
    if (state.files.length >= MAX_FILES) { errors.push(`You can select up to ${MAX_FILES} files per batch.`); break; }
    state.files.push(file);
  }
  setError(errors.join(' ')); renderSelection();
}
async function getJson(url) {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(15000) });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const error = new Error(
      typeof body.detail === 'string'
        ? body.detail
        : `Request failed (${response.status}).`
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function upload(form) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open('POST', '/api/jobs'); xhr.responseType = 'json'; xhr.timeout = 180000;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const progress = Math.round(event.loaded / event.total * 100);
        $('uploadProgress').value = progress; $('progressValue').textContent = `${progress}%`;
        if (progress === 100) $('progressLabel').textContent = 'Upload complete. Starting processing…';
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response?.job_id) resolve(xhr.response);
      else reject(new Error(typeof xhr.response?.detail === 'string' ? xhr.response.detail : `Upload failed (${xhr.status}).`));
    };
    xhr.onerror = () => reject(new Error('Could not reach the backend. Check that the application is running.'));
    xhr.ontimeout = () => reject(new Error('The upload timed out. Try fewer or smaller files.'));
    xhr.send(form);
  });
}
function link(url, text, className = 'button secondary') {
  const anchor = el('a', className, text); anchor.href = url; anchor.setAttribute('download', ''); return anchor;
}
function renderJob(job) {
  state.job = job; $('resultsSection').hidden = false;
  const succeeded = job.files.filter(f => f.status === 'success').length;
  const failed = job.files.filter(f => f.status === 'failure').length;
  const waiting = job.files.filter(f => f.status === 'awaiting_review').length;
  const pending = job.files.filter(f => f.status === 'queued' || f.status === 'processing').length;
  $('resultSummary').textContent = `${succeeded} complete · ${waiting} awaiting local review · ${failed} failed${pending ? ` · ${pending} preparing` : ''}.`;
  const stats = $('resultStats'); stats.replaceChildren();
  for (const [value, label] of [[succeeded, 'Files completed'], [waiting, 'Files awaiting review'], [failed, 'Files failed'], [job.files.filter(f => f.status === 'success').reduce((sum, f) => sum + (f.patients || 0), 0), 'Patient rows tokenised']]) {
    const stat = el('div', 'stat'); stat.append(el('strong', '', String(value)), el('span', '', label)); stats.append(stat);
  }
  $('downloadAll').hidden = !succeeded || !finalStatuses.has(job.status);
  $('downloadAll').href = `/api/jobs/${job.job_id}/download`; $('downloadAll').setAttribute('download', '');
  const list = $('resultsList'); list.replaceChildren();
  for (const file of job.files) {
    const card = el('article', 'result-card');
    const head = el('div', 'result-head'); head.append(el('span', 'csv-badge', 'CSV'));
    const title = el('div', 'file-copy'); title.append(el('h3', '', file.filename));
    if (file.hospital_id !== undefined) title.append(el('div', 'file-meta', `Hospital ${file.hospital_id}`));
    head.append(title, el('span', `status-badge ${file.status}`, { success: 'Success', failure: 'Failed', queued: 'Queued', processing: 'Processing', awaiting_review: 'Review required' }[file.status] || file.status)); card.append(head);
    if (file.status === 'success') {
      const metrics = el('div', 'metrics');
      for (const [value, label] of [[file.input_rows, 'Uploaded rows'], [file.patients, 'Unique patients'], [file.tokens_generated, 'Tokens generated']]) {
        const metric = el('div', 'metric'); metric.append(el('strong', '', String(value)), el('span', '', label)); metrics.append(metric);
      }
      card.append(metrics);
      const invalid = Object.entries(file.invalid_values || {}).filter(([, count]) => count > 0).map(([field, count]) => `${field}: ${count}`);
      if (invalid.length) card.append(el('p', 'result-warning', `Invalid values replaced with blanks — ${invalid.join(' · ')}. Processing succeeded for the remaining data.`));
      if (file.patients_with_no_tokens) card.append(el('p', 'result-warning', `${file.patients_with_no_tokens} patient rows contain no tokens because their identifier combinations are incomplete.`));
      if (file.resolved_conflict_fields) card.append(el('p', 'result-details', `${file.resolved_conflict_fields} conflicting fields confirmed locally before tokenisation.`));
      const actions = el('div', 'result-actions');
      const preview = el('button', 'button primary', 'View tokens'); preview.type = 'button'; preview.addEventListener('click', () => openPreview(file));
      actions.append(preview, link(file.downloads.tokens, 'Download CSV ↓'), link(file.downloads.token_report, 'Token report ↓'), link(file.downloads.cleaning_report, 'Cleaning report ↓')); card.append(actions);
      addBrokerControls(card, file, job.job_id);
    } else if (file.status === 'awaiting_review') {
      card.append(el('p', 'result-warning', `${file.conflict_patients} patient records have ${file.conflict_fields} conflicting fields. Review the details before generating tokens. No tokens from this file are available for broker submission yet.`));
      if (file.review_error) card.append(el('p', 'error-text', file.review_error));
      const actions = el('div', 'result-actions');
      const review = el('button', 'button primary', 'Review patient details'); review.type = 'button';
      review.disabled = state.busy && !finalStatuses.has(job.status);
      review.addEventListener('click', () => openConsolidationReview(file, job.job_id));
      actions.append(review); card.append(actions);
    } else if (file.status === 'failure') card.append(el('p', 'error-text', file.error || 'This file could not be processed.'));
    else card.append(el('p', 'result-details', file.status === 'processing' ? 'Cleaning, consolidating and generating tokens…' : 'Waiting for local processing…'));
    list.append(card);
  }
}
async function pollJob(id) {
  let consecutiveErrors = 0;
  while (true) {
    try {
      const job = await getJson(`/api/jobs/${id}`); consecutiveErrors = 0; setError(''); renderJob(job);
      if (finalStatuses.has(job.status)) return job;
      $('progressLabel').textContent = 'Preparing token files…'; $('progressValue').textContent = `${job.files.filter(f => f.status === 'success' || f.status === 'failure').length} / ${job.files.length} files`;
    } catch (error) {
      consecutiveErrors++;
      if (consecutiveErrors >= 5) throw new Error(`${error.message} Your job is saved. Reload this page to check its results.`);
      setError('Connection interrupted. Retrying your saved job…');
    }
    await new Promise(resolve => setTimeout(resolve, 900));
  }
}
function saveJobId(id) { try { sessionStorage.setItem('pprlJobId', id); } catch {} }
function readJobId() { try { return sessionStorage.getItem('pprlJobId'); } catch { return null; } }
async function processFiles() {
  if (state.busy || !state.files.length) return;
  state.busy = true; setError(''); renderSelection(); $('progressBlock').hidden = false;
  $('uploadProgress').value = 0; $('progressLabel').textContent = 'Uploading files…'; $('progressValue').textContent = '0%';
  try {
    const form = new FormData(); state.files.forEach(file => form.append('files', file));
    const job = await upload(form); saveJobId(job.job_id); renderJob(job);
    $('resultsSection').scrollIntoView({ behavior: 'smooth', block: 'start' });
    const completed = await pollJob(job.job_id);
    $('progressLabel').textContent = completed.status === 'awaiting_review' ? 'Preparation paused. Review the conflicting patient details below.' : 'Processing finished.'; $('progressValue').textContent = '';
    state.files = [];
  } catch (error) { setError(error.message); $('progressLabel').textContent = 'Processing could not be completed.'; }
  finally { state.busy = false; renderSelection(); }
}
async function openPreview(file) {
  state.preview = { file, offset: 0, total: file.patients }; $('previewTitle').textContent = file.filename;
  $('previewMeta').textContent = `Hospital ${file.hospital_id} · ${file.patients} patients · ${file.token_version} · ${file.key_id}`;
  $('previewDownload').href = file.downloads.tokens; $('previewDownload').setAttribute('download', '');
  $('previewDialog').showModal(); await loadPreview();
}
async function loadPreview() {
  const request = ++state.previewRequest;
  const current = state.preview; if (!current) return;
  $('previewError').hidden = true; $('previewTable').replaceChildren(el('p', 'preview-note', 'Loading token records…'));
  $('previousPage').disabled = true; $('nextPage').disabled = true;
  try {
    const data = await getJson(`${current.file.preview_url}?offset=${current.offset}&limit=20`);
    if (request !== state.previewRequest || !state.preview) return;
    const table = el('table'); const thead = el('thead'); const header = el('tr');
    data.columns.forEach(column => { const th = el('th', '', column); th.scope = 'col'; header.append(th); }); thead.append(header); table.append(thead);
    const body = el('tbody');
    data.rows.forEach(row => { const tr = el('tr'); data.columns.forEach((column, index) => { const value = row[column] || ''; const td = el('td', index >= 5 ? 'token-cell' : '', value || '—'); if (!value) td.classList.add('empty'); tr.append(td); }); body.append(tr); });
    table.append(body); $('previewTable').replaceChildren(table);
    $('previewPage').textContent = `Rows ${data.total_rows ? data.offset + 1 : 0}–${data.offset + data.rows.length} of ${data.total_rows} · Scroll horizontally to see all token columns`;
    $('previousPage').disabled = current.offset === 0; $('nextPage').disabled = current.offset + 20 >= data.total_rows;
  } catch (error) {
    if (request !== state.previewRequest) return;
    $('previewTable').replaceChildren(); $('previewError').textContent = error.message; $('previewError').hidden = false;
  }
}
$('browseButton').addEventListener('click', () => $('fileInput').click());
$('fileInput').addEventListener('change', (event) => { addFiles(event.target.files); event.target.value = ''; });
$('clearButton').addEventListener('click', () => { state.files = []; setError(''); renderSelection(); });
$('processButton').addEventListener('click', processFiles);
['dragenter', 'dragover'].forEach(name => $('dropzone').addEventListener(name, event => { event.preventDefault(); if (!state.busy) $('dropzone').classList.add('drag-over'); }));
['dragleave', 'drop'].forEach(name => $('dropzone').addEventListener(name, event => { event.preventDefault(); $('dropzone').classList.remove('drag-over'); }));
$('dropzone').addEventListener('drop', event => addFiles(event.dataTransfer.files));
// Prevent a dropped file outside the dropzone from navigating away.
window.addEventListener('dragover', event => event.preventDefault()); window.addEventListener('drop', event => event.preventDefault());
$('closePreview').addEventListener('click', () => $('previewDialog').close());
$('previewDialog').addEventListener('close', () => { state.preview = null; state.previewRequest++; });
$('previewDialog').addEventListener('click', event => { if (event.target === $('previewDialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
$('previousPage').addEventListener('click', () => { if (state.preview) { state.preview.offset = Math.max(0, state.preview.offset - 20); loadPreview(); } });
$('nextPage').addEventListener('click', () => { if (state.preview) { state.preview.offset += 20; loadPreview(); } });
async function checkHealth() {
  try { await getJson('/api/health'); state.online = true; $('serviceState').textContent = 'Backend connected'; $('serviceState').className = 'service-state online'; }
  catch { state.online = false; $('serviceState').textContent = 'Backend unavailable'; $('serviceState').className = 'service-state offline'; }
  updateControls();
}
async function initialise() {
  renderSelection(); await checkHealth(); setInterval(checkHealth, 15000);
  const id = readJobId();
  if (id && /^[0-9a-f]{32}$/.test(id)) {
    try { const job = await getJson(`/api/jobs/${id}`); renderJob(job); if (!finalStatuses.has(job.status)) { state.busy = true; updateControls(); await pollJob(id); } }
    catch (error) {
      if (error.status === 404) {
        // Clear the reference when the previous job no longer exists.
        try {
          sessionStorage.removeItem('pprlJobId');
        } catch {}

        state.job = null;
        $('resultsSection').hidden = true;
        setError('');
    } else {
      // Keep the reference if the connection temporarily fails.
      setError(`Previous job: ${error.message}`);
    }
  }
    finally { state.busy = false; updateControls(); }
  }
}
initialise();


function reviewKey(patientId, field) { return `${patientId}\u0000${field}`; }
function updateReviewProgress() {
  const review = state.review;
  if (!review) return;
  $('reviewProgress').textContent = `${review.decisions.size} of ${review.totalFields} fields confirmed · Patients ${review.total ? review.offset + 1 : 0}–${Math.min(review.offset + review.pageSize, review.total)} of ${review.total}`;
  const busy = review.loading || review.saving || review.bulkBlanking;
  $('reviewConfirm').disabled = busy || review.failed || !review.revision || review.decisions.size !== review.totalFields;
  $('reviewBlankAll').disabled = busy || review.failed || !review.revision;
  $('reviewBlankAll').textContent = review.bulkBlanking ? 'Selecting blanks…' : 'Leave all review fields blank';
  $('reviewPatients').querySelectorAll('select').forEach(select => { select.disabled = busy; });
  $('reviewPrevious').disabled = busy || review.offset === 0;
  $('reviewNext').disabled = busy || review.offset + review.pageSize >= review.total;
}

async function openConsolidationReview(file, jobId) {
  state.review = { file, jobId, offset: 0, pageSize: 10, total: file.conflict_patients,
    totalFields: file.conflict_fields, revision: null, decisions: new Map(), loadedSaved: false,
    loading: false, saving: false, bulkBlanking: false };
  $('reviewTitle').textContent = 'Review patient details';
  $('reviewMeta').textContent = `${file.filename} · Hospital ${file.hospital_id}`;
  $('reviewConfirm').textContent = 'Confirm and generate tokens';
  $('reviewBulkStatus').textContent = '';
  $('reviewDialog').showModal();
  await loadConsolidationPage();
}

async function loadConsolidationPage() {
  const review = state.review;
  if (!review) return;
  const request = ++state.reviewRequest;
  review.loading = true; review.failed = false; $('reviewError').hidden = true;
  $('reviewPatients').replaceChildren(el('p', 'result-details', 'Loading local consolidation conflicts…'));
  updateReviewProgress();
  try {
    const data = await getJson(`/api/jobs/${review.jobId}/files/${review.file.file_id}/consolidation-conflicts?offset=${review.offset}&limit=${review.pageSize}`);
    if (request !== state.reviewRequest || state.review !== review) return;
    if (review.revision && review.revision !== data.revision) throw new Error('Review data changed. Close and reopen this dialog.');
    review.revision = data.revision; review.total = data.total;
    if (!review.loadedSaved) {
      for (const [patientId, fields] of Object.entries(data.saved_decisions || {})) {
        for (const [field, value] of Object.entries(fields)) {
          review.decisions.set(reviewKey(patientId, field), { local_patient_id: patientId, field, value });
        }
      }
      review.loadedSaved = true;
    }
    const container = $('reviewPatients'); container.replaceChildren();
    for (const patient of data.patients) {
      const card = el('section', 'review-patient');
      card.append(el('h3', '', `Hospital ${patient.hospital_id} · Local patient ${patient.local_patient_id}`));
      for (const field of patient.fields) {
        const row = el('div', 'review-field');
        const label = el('label');
        label.append(el('span', 'review-field-name', field.field.replaceAll('_', ' ')));
        label.append(el('span', 'result-details', field.resolution === 'tie' ? 'Tied values — no automatic selection' : 'Most frequent value shown as a suggestion'));
        const select = el('select');
        select.setAttribute('aria-label', `Patient ${patient.local_patient_id} ${field.field.replaceAll('_', ' ')}`);
        const placeholder = el('option', '', 'Choose a value…'); placeholder.value = ''; placeholder.disabled = true;
        select.append(placeholder);
        field.values.forEach((item, index) => {
          const option = el('option', '', `${item.value} — ${item.count} occurrence${item.count === 1 ? '' : 's'}${item.value === field.suggested_value ? ' (suggested)' : ''}`);
          option.value = String(index); select.append(option);
        });
        const blank = el('option', '', 'Leave blank — omit combinations requiring this field'); blank.value = 'blank'; select.append(blank);
        const key = reviewKey(patient.local_patient_id, field.field);
        const chosen = review.decisions.get(key);
        select.value = chosen ? (chosen.value === '' ? 'blank' : String(field.values.findIndex(item => item.value === chosen.value))) : '';
        select.addEventListener('change', () => {
          const value = select.value === 'blank' ? '' : field.values[Number(select.value)].value;
          review.decisions.set(key, { local_patient_id: patient.local_patient_id, field: field.field, value });
          $('reviewBulkStatus').textContent = '';
          updateReviewProgress();
        });
        label.append(select); row.append(label); card.append(row);
      }
      container.append(card);
    }
  } catch (error) {
    if (request !== state.reviewRequest) return;
    review.failed = true;
    $('reviewPatients').replaceChildren(); $('reviewError').textContent = error.message; $('reviewError').hidden = false;
  } finally {
    if (request === state.reviewRequest && state.review === review) { review.loading = false; updateReviewProgress(); }
  }
}

async function leaveAllReviewFieldsBlank() {
  const review = state.review;
  if (!review || review.loading || review.failed || review.saving || review.bulkBlanking || !review.revision) return;
  const request = state.reviewRequest;
  review.bulkBlanking = true;
  $('reviewError').hidden = true; $('reviewBulkStatus').textContent = '';
  updateReviewProgress();
  try {
    // Collect every page before replacing choices, so a failed request cannot
    // leave only part of the review blanked.
    const decisions = new Map();
    for (let offset = 0; offset < review.total;) {
      const data = await getJson(`/api/jobs/${review.jobId}/files/${review.file.file_id}/consolidation-conflicts?offset=${offset}&limit=100`);
      if (state.review !== review || state.reviewRequest !== request) return;
      if (data.revision !== review.revision || data.total !== review.total) throw new Error('Review data changed. Close and reopen this dialog.');
      if (!data.patients.length) throw new Error('Could not load all review fields. Try again.');
      for (const patient of data.patients) {
        for (const field of patient.fields) {
          decisions.set(reviewKey(patient.local_patient_id, field.field),
            { local_patient_id: patient.local_patient_id, field: field.field, value: '' });
        }
      }
      offset += data.patients.length;
    }
    if (decisions.size !== review.totalFields) throw new Error('Review fields changed. Close and reopen this dialog.');
    review.decisions = decisions;
    $('reviewPatients').querySelectorAll('select').forEach(select => { select.value = 'blank'; });
    $('reviewBulkStatus').textContent = `All ${decisions.size} review fields are set to Leave blank. You can change individual choices before confirming.`;
  } catch (error) {
    if (state.review !== review || state.reviewRequest !== request) return;
    $('reviewError').textContent = `${error.message} Existing choices have been kept.`;
    $('reviewError').hidden = false;
  } finally {
    if (state.review === review) { review.bulkBlanking = false; updateReviewProgress(); }
  }
}

$('reviewBlankAll').addEventListener('click', leaveAllReviewFieldsBlank);
$('closeReview').addEventListener('click', () => { if (!state.review?.saving) $('reviewDialog').close(); });
$('reviewDialog').addEventListener('cancel', event => { if (state.review?.saving) event.preventDefault(); });
$('reviewDialog').addEventListener('close', () => { state.review = null; state.reviewRequest++; });
$('reviewPrevious').addEventListener('click', () => { if (state.review) { state.review.offset = Math.max(0, state.review.offset - state.review.pageSize); loadConsolidationPage(); } });
$('reviewNext').addEventListener('click', () => { if (state.review) { state.review.offset += state.review.pageSize; loadConsolidationPage(); } });
$('reviewConfirm').addEventListener('click', async () => {
  const review = state.review;
  if (!review || review.loading || review.failed || review.saving || review.bulkBlanking || !review.revision || review.decisions.size !== review.totalFields) return;
  review.saving = true; updateReviewProgress(); $('reviewError').hidden = true;
  $('reviewConfirm').textContent = 'Saving decisions…';
  try {
    const response = await fetch(`/api/jobs/${review.jobId}/files/${review.file.file_id}/consolidation-resolutions`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: review.revision, decisions: [...review.decisions.values()] }),
      signal: AbortSignal.timeout(30000)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save the review decisions.');
    review.saving = false; $('reviewDialog').close(); renderJob(data);
    state.busy = true; updateControls(); $('progressBlock').hidden = false;
    $('progressLabel').textContent = 'Generating tokens from the confirmed patient details…';
    const completed = await pollJob(review.jobId);
    $('progressLabel').textContent = completed.status === 'awaiting_review' ? 'Further local review is required. Check the file below.' : 'Token generation finished.';
  } catch (error) {
    if (state.review === review) {
      review.saving = false; $('reviewError').textContent = error.message; $('reviewError').hidden = false;
      $('reviewConfirm').textContent = 'Confirm and generate tokens'; updateReviewProgress();
    } else setError(error.message);
  } finally {
    state.busy = false; updateControls();
  }
});


function addBrokerControls(card, file, jobId) {
  const base = `/api/jobs/${jobId}/files/${file.file_id}`;
  const section = el('div', 'broker-section');
  const controls = el('div', 'result-actions');
  const status = el('p', 'result-details', `Broker: ${file.broker_delivery?.status || 'not sent'}`);
  const error = el('p', 'error-text');
  const send = el('button', 'button primary', file.broker_delivery?.status === 'failed' ? 'Retry broker submission' : 'Send to broker');
  const refresh = el('button', 'button secondary', 'View / refresh broker results');
  send.type = refresh.type = 'button';
  send.disabled = file.broker_delivery?.status === 'processed';
  const output = el('div', 'table-scroll');
  const paging = el('div', 'result-actions');
  const previous = el('button', 'button secondary', 'Previous');
  const next = el('button', 'button secondary', 'Next');
  previous.type = next.type = 'button';
  paging.append(previous, next); paging.hidden = true;
  let offset = 0, timer;
  function displayDelivery(delivery) {
    status.textContent = `Broker: ${delivery.status.replaceAll('_', ' ')}`;
    error.textContent = delivery.error || '';
    send.textContent = delivery.status === 'failed' || delivery.status === 'sending' ? 'Retry broker submission' : 'Send to broker';
    send.disabled = delivery.status === 'processed';
  }
  async function load() {
    if (!section.isConnected) { clearTimeout(timer); return; }
    refresh.disabled = true; error.textContent = '';
    try {
      const data = await getJson(`${base}/broker-results?offset=${offset}&limit=20`);
      displayDelivery(data.delivery); output.replaceChildren();
      if (data.items.length) {
        const table = el('table'), head = el('tr');
        ['Local patient ID', 'Global patient ID', 'Status', 'Latest arrival'].forEach(t => head.append(el('th', '', t)));
        const thead = el('thead'); thead.append(head); table.append(thead);
        const body = el('tbody');
        data.items.forEach(item => { const row = el('tr');
          [item.local_patient_id, item.global_patient_id || 'Pending review', item.status.replaceAll('_', ' '), new Date(item.latest_received_at).toLocaleString()].forEach(t => row.append(el('td', '', t)));
          body.append(row);
        });
        table.append(body); output.append(table);
      } else output.append(el('p', 'result-details', data.delivery.status === 'processed' ? 'No results.' : 'Results appear after matching completes.'));
      paging.hidden = data.total <= 20; previous.disabled = offset === 0; next.disabled = offset + 20 >= data.total;
      clearTimeout(timer);
      if (data.delivery.status === 'accepted') timer = setTimeout(load, 2000);
    } catch (e) { error.textContent = e.message; }
    finally { refresh.disabled = false; }
  }
  send.addEventListener('click', async () => {
    clearTimeout(timer);
    send.disabled = true; error.textContent = ''; status.textContent = 'Broker: sending...';
    try {
      const response = await fetch(`${base}/broker-submission`, { method: 'POST', signal: AbortSignal.timeout(40000) });
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Broker submission failed.');
      displayDelivery(data);
      if (data.status !== 'failed') await load();
    } catch (e) { displayDelivery({ status: 'failed', error: e.message }); }
  });
  refresh.addEventListener('click', load);
  previous.addEventListener('click', () => { offset = Math.max(0, offset - 20); load(); });
  next.addEventListener('click', () => { offset += 20; load(); });
  controls.append(send, refresh); section.append(status, error, controls, output, paging); card.append(section);
  if (file.broker_delivery?.status === 'accepted') timer = setTimeout(load, 1000);
}
