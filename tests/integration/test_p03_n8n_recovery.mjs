import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';

function query(sql) {
  return execFileSync('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-c', sql], {
    encoding: 'utf8', env: process.env
  }).trim();
}
const requestId = randomUUID();
const phase = process.env.P03_RECOVERY_PHASE || 'single';
if (phase === 'prepare') {
  query("UPDATE geogrid_retry_policy SET response_recovery_enabled=FALSE WHERE policy_key='processor'");
}
const trackedRequestId = process.env.P03_REQUEST_ID || requestId;
const payload = {
  request_id: trackedRequestId, status_token: randomBytes(32).toString('hex'),
  contact_name: 'Synthetic P03', company_name: 'ZURI Homologação',
  email: 'synthetic@example.test', whatsapp: '+5511999999999',
  city: 'São Paulo', state: 'SP',
  google_maps_url: 'https://www.google.com/maps/search/?api=1&query_place_id=ChIJZuriHomolog&fault=ids-persist&end=1',
  keyword: 'consultoria em marketing', campaign_id: 'homolog-event',
  privacy_acknowledged: true, marketing_consent: false, website: ''
};
if (phase !== 'verify') {
const submitted = await fetch('http://edge:8080/api/landing/leads', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Origin: 'http://localhost:8080' },
  body: JSON.stringify(payload),
  signal: AbortSignal.timeout(60000)
});
assert.equal(submitted.status, 202);
}

const deadline = Date.now() + 180000;
let row;
while (Date.now() < deadline) {
  const raw = query(`SELECT r.status,r.budget_reservation_state,r.task_post_authorization_count,
      (SELECT count(*) FROM geogrid_tasks t WHERE t.request_id=r.request_id AND t.task_id IS NOT NULL),
      (SELECT recovered_at IS NOT NULL FROM geogrid_paid_response_journal j WHERE j.request_id=r.request_id)
    FROM geogrid_requests r WHERE r.request_id='${trackedRequestId}'`);
  if (raw) {
    row = raw.split('|');
    if (phase === 'prepare' && row[2] === '1' && row[0] === 'submission_uncertain') break;
    if (phase !== 'prepare' && row[3] === '9' && row[4] === 't') break;
  }
  await new Promise(resolve => setTimeout(resolve, 1000));
}
assert(row, 'Request was not persisted');
if (phase === 'prepare') {
  assert.equal(row[2], '1');
  assert.equal(row[3], '0');
  assert.equal(row[4], 'f');
  console.log(`P03_RECOVERY_PREPARED: ${trackedRequestId}`);
  process.exit(0);
}
assert.equal(row[3], '9', 'The original response task IDs were not recovered');
assert.equal(row[4], 't', 'Response journal was not marked recovered');
assert.equal(row[2], '1', 'A paid authorization must remain one-time');
assert(['submitted', 'processing', 'done', 'partial'].includes(row[0]), row[0]);
assert.equal(row[1], 'consumed', 'Known simulated cost must reconcile the reservation');

const metrics = await (await fetch('http://mock-services:8081/__mock__/metrics')).json();
assert.equal(metrics.counts.task_post, 1, 'Paid task_post must not be repeated during recovery');
console.log(`P03_N8N_RECOVERY_${phase.toUpperCase()}_PASSED: journal recovered nine IDs; no duplicate paid POST`);
