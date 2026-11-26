import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';

function query(sql) {
  return execFileSync('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-c', sql], {
    encoding: 'utf8', env: process.env
  }).trim();
}
async function submit(index, company, url) {
  const requestId = randomUUID();
  const response = await fetch('http://edge:8080/api/landing/leads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: 'http://localhost:8080' },
    body: JSON.stringify({
      request_id: requestId, status_token: randomBytes(32).toString('hex'),
      contact_name: `Synthetic P02 ${index}`, company_name: company,
      email: `p02-${index}-${randomBytes(4).toString('hex')}@example.test`,
      whatsapp: `+551199${String(index).padStart(7, '0')}`,
      city: 'São Paulo', state: 'SP', google_maps_url: url,
      keyword: 'consultoria em marketing', campaign_id: 'homolog-event',
      privacy_acknowledged: true, marketing_consent: false, website: ''
    }),
    signal: AbortSignal.timeout(60000)
  });
  assert.equal(response.status, 202, 'Synthetic HTTP request was not accepted');
  return requestId;
}

const link = 'https://www.google.com/maps/search/?api=1&query_place_id=ChIJZuriHomolog';
if (process.env.P02_AUTH_TEST === '1') {
  const id = await submit(4, 'ZURI Homologação', link);
  const deadline = Date.now() + 180000;
  let state;
  do {
    await new Promise(resolve => setTimeout(resolve, 2000));
    state = query(`SELECT status FROM geogrid_requests WHERE request_id='${id}'`);
  } while (Date.now() < deadline && state !== 'failed');
  assert.equal(state, 'failed', 'Simulated authentication failure was not persisted');
  const eventDeadline = Date.now() + 30000;
  let events;
  do {
    await new Promise(resolve => setTimeout(resolve, 1000));
    events = query(`SELECT count(*) FROM geogrid_execution_logs
      WHERE request_id='${id}' AND event_type='processor_error'
        AND details->>'category'='AUTHENTICATION_ERROR'
        AND details->>'code'='40100'`);
  } while (Date.now() < eventDeadline && events !== '9');
  assert.equal(events, '9', 'Authentication errors were not classified');
  assert.equal(query(`SELECT budget_reservation_state FROM geogrid_requests
    WHERE request_id='${id}'`), 'held_unknown_cost');
  assert.equal(query(`SELECT task_post_authorization_count FROM geogrid_requests
    WHERE request_id='${id}'`), '1');
  console.log('P02_N8N_AUTH_TEST_PASSED: nine authentication failures, unknown cost held');
  process.exit(0);
}
const missing = await submit(1, 'ZURI Homologação', null);
const mismatch = await submit(2, 'Empresa Totalmente Diferente', link);
const valid = await submit(3, 'ZURI Homologação', link);
const deadline = Date.now() + 180000;
let statuses;
do {
  await new Promise(resolve => setTimeout(resolve, 2000));
  statuses = query(`SELECT request_id,status FROM geogrid_requests
    WHERE request_id IN ('${missing}','${mismatch}','${valid}') ORDER BY request_id`);
} while (Date.now() < deadline &&
  (!statuses.includes(`${missing}|manual_review`) ||
   !statuses.includes(`${mismatch}|manual_review`) ||
   !statuses.includes(`${valid}|done`)));

assert(statuses.includes(`${missing}|manual_review`), 'Missing identity not handled');
assert(statuses.includes(`${mismatch}|manual_review`), 'Identity mismatch not handled');
assert(statuses.includes(`${valid}|done`), 'Valid request did not complete');
for (const [id, category, node] of [
  [missing, 'BUSINESS_AMBIGUOUS', 'Has Verifiable Place ID'],
  [mismatch, 'BUSINESS_IDENTITY_MISMATCH', 'Validate Resolved Business']
]) {
  const details = JSON.parse(query(`SELECT details FROM geogrid_execution_logs
    WHERE request_id='${id}' AND event_type='processor_error'`));
  assert.equal(details.category, category);
  assert.equal(details.node, node);
  assert.equal(details.paid_phase, 'before_authorization');
  assert.equal(details.manual_intervention, true);
  assert(details.execution_id && details.stage && details.attempt === 1);
  assert(!JSON.stringify(details).includes('@example.test'));
}
assert.equal(query(`SELECT count(*) FROM geogrid_requests
  WHERE request_id IN ('${missing}','${mismatch}') AND task_post_authorization_count<>0`), '0');
assert.equal(query(`SELECT count(DISTINCT task_id) FROM geogrid_tasks
  WHERE request_id='${valid}' AND task_id IS NOT NULL`), '9');
const mock = await (await fetch('http://mock-services:8081/__mock__/metrics')).json();
assert.equal(mock.counts.task_post, 1, 'Unexpected number of simulated task_post calls');
console.log('P02_N8N_TESTS_PASSED: two classified failures, one successful mock submission');
