import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const exec = promisify(execFile);
async function query(sql) {
  const { stdout } = await exec('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-c', sql], {
    env: process.env
  });
  return stdout.trim();
}
async function rejects(sql) {
  await assert.rejects(query(sql));
}
async function migration(path) {
  await exec('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-f', path], { env: process.env });
}
const requestId = index => `10000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
async function addRequest(index, campaign = 'p02-event') {
  const id = requestId(index);
  await query(`INSERT INTO geogrid_requests(request_id,idempotency_key,payload_fingerprint,
    campaign_id,contact_name,company_name,email,whatsapp,city,state,keyword,consent_at)
    VALUES ('${id}','p02-${index}','p02-fp-${index}','${campaign}','Synthetic',
    'Synthetic','p02@example.test','+15550000000','Test','TS','test',now())`);
  return id;
}
async function claim() {
  const value = await query("SELECT request_id,claim_token FROM geogrid_claim_processor_request('p02-execution')");
  const [id, token] = value.split('|');
  assert(id && token, `Expected a claim, got ${value}`);
  return [id, token];
}
function call(id, token, node, category, code = 'NULL') {
  return `SELECT geogrid_record_processor_error('${id}','${token}','${node}','${category}',${code},'p02-execution')`;
}

assert.equal(await query('SELECT count(*) FROM geogrid_requests'), '0', 'Use a new empty isolated database');
await migration('/app/migration.sql');
await migration('/app/migration-p02.sql');
await query(`UPDATE geogrid_budget SET daily_cap_usd=100, default_event_cap_usd=100,
  estimated_task_cost_usd=0.0001, max_pending_tasks=10000,
  new_diagnostics_per_minute=1000 WHERE scope_type='system' AND scope_key='default';
  INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
  VALUES ('event','p02-event',100),('event','p02-blocked',0)`);

const cases = [
  ['Has Verifiable Place ID', 'BUSINESS_AMBIGUOUS', null, 'manual_review'],
  ['Extract Exact Google Place ID', 'INVALID_PLACE_ID', null, 'manual_review'],
  ['Resolve Exact Place Details', 'BUSINESS_NOT_FOUND', '404', 'manual_review'],
  ['Validate Resolved Business', 'BUSINESS_IDENTITY_MISMATCH', null, 'manual_review'],
  ['Validate Resolved Business', 'BUSINESS_LOCATION_MISMATCH', null, 'manual_review'],
  ['Register Business in Smart Grid', 'AUTHENTICATION_ERROR', '401', 'manual_review'],
  ['Create Idempotent Smart Grid Scan', 'TIMEOUT', null, 'queued'],
  ['Build Nine Standard Tasks', 'API_CONTRACT_ERROR', null, 'manual_review'],
  ['Persist Grid Before Paid Submission', 'PERSISTENCE_ERROR', null, 'queued'],
  ['Authorize Paid task_post Exactly Once', 'RATE_LIMIT', '429', 'queued'],
];
for (const [index, [node, category, code, status]] of cases.entries()) {
  const id = await addRequest(index + 1);
  const [claimed, token] = await claim();
  assert.equal(claimed, id);
  await rejects(call(id, 'ffffffff-0000-4000-8000-000000000099', node, category));
  assert.equal(await query(call(id, token, node, category, code ? `'${code}'` : 'NULL')), 't');
  const result = await query(`SELECT status,budget_reserved_usd FROM geogrid_requests WHERE request_id='${id}'`);
  assert.equal(result, `${status}|0.000000`);
  const details = JSON.parse(await query(`SELECT details FROM geogrid_execution_logs
    WHERE request_id='${id}' AND event_type='processor_error'`));
  assert.equal(details.node, node);
  assert.equal(details.category, category);
  assert.equal(details.execution_id, 'p02-execution');
  assert.equal(details.paid_phase, 'before_authorization');
  assert.equal(details.manual_intervention, status === 'manual_review');
  assert.equal(details.attempt, 1);
  assert(details.stage && details.message && typeof details.recoverable === 'boolean');
  if (code) assert.equal(details.code, code);
  assert(!JSON.stringify(details).includes('p02@example.test'));
  if (status === 'queued') {
    await query(`UPDATE geogrid_requests SET status='manual_review' WHERE request_id='${id}'`);
  }
}
console.log(`PASS pre-submission classification: ${cases.length} induced failures`);

const paidId = await addRequest(40);
const [paid, token] = await claim();
assert.equal(paid, paidId);
const points = JSON.stringify(Array.from({ length: 9 }, (_, i) =>
  ({ row: Math.floor(i / 3), col: i % 3, lat: -23.55, lng: -46.63 })));
await query(`SELECT geogrid_prepare_tasks('${paid}','${token}','scan-${paid}','business',
  -23.55,-46.63,'${points}'::jsonb)`);
await query(`SELECT geogrid_authorize_task_post('${paid}','${token}','scan-${paid}')`);
await query(call(paid, token, 'Paid DataForSEO task_post - No Automatic Retry', 'TIMEOUT'));
assert.equal(await query(`SELECT status,budget_reservation_state,budget_reserved_usd
  FROM geogrid_requests WHERE request_id='${paid}'`),
  'submission_uncertain|held_uncertain|0.000900');
const uncertain = JSON.parse(await query(`SELECT details FROM geogrid_execution_logs
  WHERE request_id='${paid}' AND event_type='processor_error'`));
assert.equal(uncertain.category, 'SUBMISSION_UNCERTAIN');
assert.equal(uncertain.cause_category, 'TIMEOUT');
assert.equal(uncertain.paid_phase, 'after_authorization');
assert.equal(uncertain.manual_intervention, true);
assert.equal(uncertain.recoverable, false, 'An uncertain paid POST cannot be retried');
for (const node of ['Associate and Classify task_post Response', 'Persist DataForSEO IDs and Cost']) {
  await query(call(paid, token, node, 'PERSISTENCE_ERROR'));
}
assert.equal(await query(`SELECT count(*) FROM geogrid_execution_logs
  WHERE request_id='${paid}' AND event_type='processor_error'`), '3');
await rejects(`SELECT geogrid_authorize_task_post('${paid}','${token}','scan-${paid}')`);
console.log('PASS paid failure: uncertain, reservation held, repost prohibited');

const resultId = await addRequest(41);
const [resultClaim, resultToken] = await claim();
assert.equal(resultClaim, resultId);
await query(`SELECT geogrid_prepare_tasks('${resultId}','${resultToken}','scan-${resultId}',
  'business',-23.55,-46.63,'${points}'::jsonb)`);
await query(`SELECT geogrid_authorize_task_post('${resultId}','${resultToken}','scan-${resultId}')`);
const results = JSON.stringify(Array.from({ length: 9 }, (_, i) => ({
  row: Math.floor(i / 3), col: i % 3,
  task_id: i < 2 ? null : `p02-task-${i}`,
  status_code: i === 0 ? 40100 : i === 1 ? 50000 : 20100,
  status_message: 'Synthetic response', cost: null
})));
await query(`SELECT geogrid_save_post_response('${resultId}','${resultToken}','${results}'::jsonb,NULL)`);
assert.equal(await query(`SELECT geogrid_log_post_outcomes('${resultId}','${resultToken}','p02-execution')`), '2');
assert.equal(await query(`SELECT geogrid_log_post_outcomes('${resultId}','${resultToken}','p02-execution')`), '0');
const categories = await query(`SELECT string_agg(details->>'category',',' ORDER BY details->>'category')
  FROM geogrid_execution_logs WHERE request_id='${resultId}' AND event_type='processor_error'`);
assert.equal(categories, 'AUTHENTICATION_ERROR,SUBMISSION_UNCERTAIN');
assert.equal(await query(`SELECT budget_reservation_state FROM geogrid_requests WHERE request_id='${resultId}'`), 'held_uncertain');
for (const node of ['Log Persisted Submission Outcomes', 'Build Failed Submission Point Callbacks',
  'Report Failed Submission Points', 'Record Failed Point Callback Delivery']) {
  await query(call(resultId, resultToken, node, 'PERSISTENCE_ERROR'));
}
assert.equal(await query(`SELECT count(*) FROM geogrid_execution_logs
  WHERE request_id='${resultId}' AND event_type='processor_error'
    AND details->>'paid_phase'='after_response_persisted'`), '4');
assert.equal(await query(`SELECT budget_reservation_state FROM geogrid_requests WHERE request_id='${resultId}'`), 'held_uncertain');
console.log('PASS provider outcomes: rejected and uncertain points logged idempotently');

const blockedId = await addRequest(50, 'p02-blocked');
assert.equal(await query("SELECT count(*) FROM geogrid_claim_processor_request('p02-budget-execution')"), '0');
assert.equal(await query(`SELECT status FROM geogrid_requests WHERE request_id='${blockedId}'`), 'budget_blocked');
const budget = JSON.parse(await query(`SELECT details FROM geogrid_execution_logs
  WHERE request_id='${blockedId}' AND event_type='processor_error'`));
assert.equal(budget.category, 'BUDGET_LIMIT');
assert.equal(budget.execution_id, 'p02-budget-execution');
assert.equal(budget.paid_phase, 'before_authorization');
console.log('PASS budget denial: metadata logged without a paid submission');
console.log('P02_POSTGRES_TESTS_PASSED');
