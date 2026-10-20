'use strict';
const $ = (id) => document.getElementById(id);
const state = { files: [], busy: false, online: false, job: null, preview: null, previewRequest: 0 };
const MAX_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 20;
const finalStatuses = new Set(['success', 'partial_success', 'failure']);
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
  $('uploadHint').textContent = state.busy ? 'Preparing records for the broker in the background.' : state.files.length ? 'Your selected files are ready to prepare for the broker.' : 'Choose CSV files to prepare for the broker.';
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
  const pending = job.files.length - succeeded - failed;
  const received = job.files.filter(file => file.broker_status === 'received').length;
  const deliveryFailures = job.files.filter(file => file.broker_status === 'failed').length;
  $('resultSummary').textContent = pending ? `${succeeded} prepared · ${failed} failed · ${pending} preparing` : `${succeeded} prepared · ${received} received by broker${deliveryFailures ? ` · ${deliveryFailures} delivery failed` : ''}${failed ? ` · ${failed} processing failed` : ''}.`;
  const stats = $('resultStats'); stats.replaceChildren();
  for (const [value, label] of [[received, 'Files received by broker'], [failed + deliveryFailures, 'Files needing attention'], [job.files.reduce((sum, f) => sum + (f.patients || 0), 0), 'Patient records prepared']]) {
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
    const status = file.status === 'success' ? ({ received: 'Received by broker', failed: 'Delivery failed', sending: 'Sending to broker' }[file.broker_status] || 'Ready for broker') : ({ failure: 'Failed', queued: 'Queued', processing: 'Processing' }[file.status] || file.status);
    head.append(title, el('span', `status-badge ${file.broker_status === 'failed' ? 'failure' : file.status}`, status)); card.append(head);
    if (file.status === 'success') {
      const metrics = el('div', 'metrics');
      for (const [value, label] of [[file.input_rows, 'Uploaded rows'], [file.patients, 'Unique patients'], [file.tokens_generated, 'Protected tokens prepared']]) {
        const metric = el('div', 'metric'); metric.append(el('strong', '', String(value)), el('span', '', label)); metrics.append(metric);
      }
      card.append(metrics);
      const invalid = Object.entries(file.invalid_values || {}).filter(([, count]) => count > 0).map(([field, count]) => `${field}: ${count}`);
      if (invalid.length) card.append(el('p', 'result-warning', `Invalid values replaced with blanks — ${invalid.join(' · ')}. Processing succeeded for the remaining data.`));
      if (file.patients_with_no_tokens) card.append(el('p', 'result-warning', `${file.patients_with_no_tokens} patient rows contain no tokens because their identifier combinations are incomplete.`));
      const actions = el('div', 'result-actions');
      if (file.broker_status === 'received') card.append(el('p', 'result-details', `Broker acknowledged receipt at ${new Date(file.broker_receipt.received_at).toLocaleString()}.`));
      if (file.broker_status === 'failed') {
        card.append(el('p', 'result-warning', file.broker_error));
        const retry = el('button', 'button primary', 'Retry sending'); retry.type = 'button';
        retry.disabled = !finalStatuses.has(job.status);
        retry.addEventListener('click', async () => {
          retry.disabled = true; retry.textContent = 'Sending…';
          try {
            const response = await fetch(`/api/jobs/${job.job_id}/files/${file.file_id}/send`, { method: 'POST', signal: AbortSignal.timeout(120000) });
            if (!response.ok) throw new Error('Could not retry broker delivery.');
            renderJob(await response.json());
          } catch (error) { setError(error.message); retry.disabled = false; retry.textContent = 'Retry sending'; }
        });
        actions.append(retry);
      }
      const preview = el('button', 'button primary', 'Inspect prepared records'); preview.type = 'button'; preview.addEventListener('click', () => openPreview(file));
      actions.append(preview, link(file.downloads.tokens, 'Download CSV ↓'), link(file.downloads.token_report, 'Token report ↓'), link(file.downloads.cleaning_report, 'Cleaning report ↓')); card.append(actions);
    } else if (file.status === 'failure') card.append(el('p', 'error-text', file.error || 'This file could not be processed.'));
    else card.append(el('p', 'result-details', file.status === 'processing' ? 'Preparing protected records for the broker…' : 'Waiting for local processing…'));
    list.append(card);
  }
}
async function pollJob(id) {
  let consecutiveErrors = 0;
  while (true) {
    try {
      const job = await getJson(`/api/jobs/${id}`); consecutiveErrors = 0; setError(''); renderJob(job);
      if (finalStatuses.has(job.status)) return job;
      $('progressLabel').textContent = job.files.some(file => file.broker_status === 'sending') ? 'Sending tokens individually to the broker…' : 'Preparing records locally…'; $('progressValue').textContent = `${job.files.filter(f => f.status === 'success' || f.status === 'failure').length} / ${job.files.length} files prepared`;
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
    const delivered = completed.files.filter(file => file.broker_status === 'received').length;
    const deliveryFailed = completed.files.some(file => file.broker_status === 'failed');
    $('progressLabel').textContent = deliveryFailed ? 'Preparation finished. Some broker deliveries failed; retry below.' : delivered ? `${delivered} file(s) received by the broker.` : 'Preparation finished. No files were sent to a broker.'; $('progressValue').textContent = '';
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
