'use strict';
function renderSubmissions(submissions) {
  const list = document.getElementById('submissions');
  list.replaceChildren();
  for (const submission of submissions) {
    const item = document.createElement('li');
    const title = document.createElement('strong');
    title.textContent = `${submission.status === 'received' ? '\u2713 Received' : 'Receiving'} ${submission.filename}`;
    const hospital = document.createElement('p');
    hospital.textContent = `Hospital: ${submission.hospital_id.replace(/^hospital(\d+)$/, 'Hospital $1')}`;
    const received = document.createElement('small');
    received.textContent = `${new Date(submission.received_at).toLocaleString()} · ${submission.status}`;
    item.append(title, hospital, received);
    list.append(item);
  }
}
async function refresh() {
  try {
    const response = await fetch('/api/submissions', { cache: 'no-store', signal: AbortSignal.timeout(10000) });
    if (!response.ok) throw new Error('Request failed');
    const submissions = await response.json();
    renderSubmissions(submissions);
    document.getElementById('status').textContent = submissions.length ? `${submissions.length} received submissions (latest 500).` : 'Waiting for hospital submissions.';
  } catch {
    document.getElementById('status').textContent = 'Could not refresh submissions. Check your connection and operator login.';
  } finally {
    setTimeout(refresh, 3000);
  }
}
refresh();
