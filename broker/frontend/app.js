'use strict';
let offset = 0;
let loading = false;
const limit = 50;
const elements = Object.fromEntries(['records', 'status', 'hospital', 'previous', 'next', 'page'].map(id => [id, document.getElementById(id)]));
function renderRecords(data) {
  elements.records.replaceChildren();
  for (const record of data.records) {
    const item = document.createElement('li');
    const heading = document.createElement('strong');
    heading.textContent = `${record.hospitalID.replace(/^hospital(\d+)$/, 'Hospital $1')} \u00b7 Patient ${record.patientID}`;
    const token = document.createElement('code');
    token.textContent = record.token || 'Blank combination';
    const details = document.createElement('small');
    details.textContent = `Combination ${record.token_index} \u00b7 ${record.filename} \u00b7 ${new Date(record.received_at).toLocaleString()}`;
    item.append(heading, token, details);
    elements.records.append(item);
  }
  elements.status.textContent = data.total ? `${data.total.toLocaleString()} received records` : 'No records received for this selection.';
  elements.page.textContent = data.total ? `${data.offset + 1}\u2013${data.offset + data.records.length} of ${data.total}` : '0 records';
  elements.previous.disabled = data.offset === 0;
  elements.next.disabled = data.offset + data.records.length >= data.total;
}
async function refresh() {
  if (loading) return;
  loading = true;
  const requestedHospital = elements.hospital.value;
  const requestedOffset = offset;
  try {
    const query = new URLSearchParams({ offset, limit });
    if (elements.hospital.value) query.set('hospital_id', elements.hospital.value);
    const response = await fetch(`/api/records?${query}`, { cache: 'no-store', signal: AbortSignal.timeout(60000) });
    if (!response.ok) throw new Error('Request failed');
    const data = await response.json();
    if (requestedHospital === elements.hospital.value && requestedOffset === offset) renderRecords(data);
  } catch {
    elements.status.textContent = 'Could not refresh records. Check your connection and operator login.';
  } finally {
    loading = false;
  }
}
elements.hospital.addEventListener('change', () => { offset = 0; elements.records.replaceChildren(); refresh(); });
elements.previous.addEventListener('click', () => { offset = Math.max(0, offset - limit); refresh(); });
elements.next.addEventListener('click', () => { offset += limit; refresh(); });
refresh();
setInterval(refresh, 3000);
