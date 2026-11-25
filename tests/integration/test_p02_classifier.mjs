import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const workflow = JSON.parse(readFileSync(
  new URL('../../workflows/n8n/02-processador-standard.json', import.meta.url)
));
const claim = workflow.nodes.find(node => node.name === 'Atomically Claim One Request');
assert.notEqual(claim.onError, 'continueErrorOutput', 'Claim database errors must stop execution');
const classifier = workflow.nodes.find(node => node.name === 'Classify Processor Failure');
const classify = new Function('$json', '$prevNode', '$execution', '$', classifier.parameters.jsCode);
const original = { request_id: 'synthetic-request', claim_token: 'synthetic-claim' };
const previous = () => ({ item: { json: original } });

function run(node, json) {
  return classify(json, { name: node }, { id: 'execution-1' }, previous).json;
}

const cases = [
  ['Has Verifiable Place ID', {}, 'BUSINESS_AMBIGUOUS'],
  ['Extract Exact Google Place ID', { error: { message: 'P02:INVALID_PLACE_ID' } }, 'INVALID_PLACE_ID'],
  ['Resolve Exact Place Details', { error: { code: 'ETIMEDOUT', message: 'secret contact@example.test' } }, 'TIMEOUT'],
  ['Validate Resolved Business', { error: { message: 'P02:BUSINESS_IDENTITY_MISMATCH' } }, 'BUSINESS_IDENTITY_MISMATCH'],
  ['Validate Resolved Business', { error: 'P02:BUSINESS_IDENTITY_MISMATCH' }, 'BUSINESS_IDENTITY_MISMATCH'],
  ['Validate Resolved Business', { error: 'P02:BUSINESS_LOCATION_MISMATCH' }, 'BUSINESS_LOCATION_MISMATCH'],
  ['Register Business in Smart Grid', { error: { httpCode: '401' } }, 'AUTHENTICATION_ERROR'],
  ['Create Idempotent Smart Grid Scan', { error: { httpCode: '429' } }, 'RATE_LIMIT'],
  ['Build Nine Standard Tasks', { error: {} }, 'API_CONTRACT_ERROR'],
  ['Persist Grid Before Paid Submission', { error: {} }, 'PERSISTENCE_ERROR'],
  ['Authorize Paid task_post Exactly Once', { error: {} }, 'PERSISTENCE_ERROR'],
  ['Paid DataForSEO task_post - No Automatic Retry', { error: { message: 'ECONNRESET' } }, 'CONNECTION_ERROR'],
  ['Associate and Classify task_post Response', { error: {} }, 'API_CONTRACT_ERROR'],
  ['Persist DataForSEO IDs and Cost', { error: {} }, 'PERSISTENCE_ERROR'],
  ['Log Persisted Submission Outcomes', { error: {} }, 'PERSISTENCE_ERROR'],
  ['Build Failed Submission Point Callbacks', { error: {} }, 'PERMANENT_ERROR'],
  ['Report Failed Submission Points', { error: { httpCode: '500' } }, 'CONNECTION_ERROR'],
  ['Record Failed Point Callback Delivery', { error: {} }, 'PERSISTENCE_ERROR'],
];

for (const [node, data, expected] of cases) {
  const result = run(node, data);
  assert.equal(result.category, expected, node);
  assert.equal(result.node, node);
  assert.equal(result.execution_id, 'execution-1');
  if (data.error?.code === 'ETIMEDOUT') assert.equal(result.code, 'ETIMEDOUT');
  assert(!JSON.stringify(result).includes('secret'));
  assert(!JSON.stringify(result).includes('contact@example.test'));
}
for (const [code, category] of [[404, 'BUSINESS_NOT_FOUND'], [400, 'INVALID_PLACE_ID'],
  [401, 'AUTHENTICATION_ERROR'], [429, 'RATE_LIMIT'], [503, 'CONNECTION_ERROR']]) {
  const result = run('Validate Resolved Business', {
    error: { message: `P02:${category}:${code}` }
  });
  assert.equal(result.category, category);
  assert.equal(result.code, String(code));
  assert.equal(result.node, 'Resolve Exact Place Details');
}
const associateNode = workflow.nodes.find(node => node.name === 'Associate and Classify task_post Response');
const associate = new Function('$json', '$', associateNode.parameters.jsCode);
const build = {
  request_id: original.request_id, claim_token: original.claim_token,
  points: Array.from({ length: 9 }, (_, i) => ({ row: Math.floor(i / 3), col: i % 3 })),
  tasks: Array.from({ length: 9 }, (_, i) => ({ tag: `task-${i}` }))
};
const rejected = associate(
  { status_code: 40100, status_message: 'Synthetic rejection', tasks: [] },
  () => ({ item: { json: build } })
).json;
assert(rejected.task_results.every(task =>
  task.status_code === 40100 && task.error_category === 'authentication_error' && task.cost === null));
assert.equal(rejected.total_cost_usd, null);
console.log(`P02 classifier: ${cases.length + 6} injected failures passed`);
