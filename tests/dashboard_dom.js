const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.textContent = ''; this.value = ''; this.handlers = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, callback) { this.handlers[name] = callback; }
}
const elements = Object.fromEntries(['records', 'status', 'hospital', 'previous', 'next', 'page'].map(id => [id, new Element()]));
const requests = [];
const context = vm.createContext({ document: { getElementById: id => elements[id], createElement: () => new Element() },
  fetch: url => { requests.push(url); return new Promise(() => {}); }, AbortSignal, URLSearchParams, setInterval: () => {} });
vm.runInContext(fs.readFileSync('broker/frontend/app.js', 'utf8'), context);
const input = fs.readFileSync(0, 'utf8').trim();
context.received = input ? JSON.parse(input) : { records: [
  { hospitalID: 'hospital1', patientID: '000123', token: 'a'.repeat(64), token_index: 1, filename: '<script>.csv', received_at: '2026-10-24T00:00:00Z' },
  { hospitalID: 'hospital2', patientID: '2', token: '', token_index: 2, filename: 'hospital2.csv', received_at: '2026-10-24T00:00:00Z' }
], total: 2, offset: 0 };
vm.runInContext('renderRecords(received)', context);
const text = node => [node.textContent, ...node.children.map(text)].join(' ');
const rendered = text(elements.records);
for (const record of context.received.records) {
  assert.ok(rendered.includes(`Patient ${record.patientID}`));
  assert.ok(rendered.includes(record.hospitalID.replace(/^hospital(\d+)$/, 'Hospital $1')));
  assert.ok(rendered.includes(record.token || 'Blank combination'));
}
const pendingRequests = requests.length;
vm.runInContext('refresh()', context);
assert.equal(requests.length, pendingRequests, 'Polling must not overlap a pending request');
vm.runInContext('loading = false', context);
elements.hospital.value = 'hospital2';
elements.hospital.handlers.change();
assert.match(requests.at(-1), /hospital_id=hospital2/);
assert.match(requests.at(-1), /offset=0/);
vm.runInContext('loading = false', context);
elements.next.handlers.click();
assert.match(requests.at(-1), /offset=50/);
vm.runInContext('loading = false', context);
elements.previous.handlers.click();
assert.match(requests.at(-1), /offset=0/);
console.log('Dashboard rendering, hospital filtering and pagination passed');
