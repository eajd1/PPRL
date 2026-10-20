// Execute the real dashboard renderer against metadata fetched over TLS.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const elements = { submissions: new Element(), status: new Element() };
const context = vm.createContext({ document: { getElementById: id => elements[id], createElement: () => new Element() }, fetch: () => new Promise(() => {}), AbortSignal, setTimeout });
vm.runInContext(fs.readFileSync('broker/frontend/app.js', 'utf8'), context);
context.received = JSON.parse(fs.readFileSync(0, 'utf8'));
vm.runInContext('renderSubmissions(received)', context);
const text = node => [node.textContent, ...node.children.map(text)].join(' ');
const rendered = text(elements.submissions);
for (const submission of context.received) {
  assert.ok(rendered.includes(`Received ${submission.filename}`));
  assert.ok(rendered.includes(submission.hospital_id.replace(/^hospital(\d+)$/, 'Hospital $1')));
}
assert.ok(!rendered.includes('tokens'));
console.log('Dashboard rendered real received metadata successfully');
