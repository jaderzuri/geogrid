import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';

function query(sql) {
  return execFileSync('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-c', sql], {
    encoding: 'utf8', env: process.env
  }).trim();
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(fn, description) {
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    const result = fn();
    if (result) return result;
    await sleep(250);
  }
  throw new Error(`Timed out waiting for ${description}`);
}
function insertRequest(fault) {
  const id = randomUUID();
  const url = fault === 'invalid' ? 'not a URL' :
    `https://www.google.com/maps/search/?query_place_id=ChIJZuriHomolog&fault=${fault}&end=1`;
  query(`INSERT INTO geogrid_requests(request_id,idempotency_key,payload_fingerprint,
    campaign_id,contact_name,company_name,email,whatsapp,city,state,keyword,
    google_maps_url,consent_at)
    VALUES ('${id}','${id}','${id}','homolog-event','Synthetic','ZURI Homologação',
    'synthetic@example.test','+15550000000','São Paulo','SP','test','${url}',now())`);
  return id;
}

const dbPath = ['/n8n-state/.n8n/database.sqlite', '/n8n-state/database.sqlite']
  .find(path => existsSync(path));
assert(dbPath, 'Isolated n8n execution database not found');
const db = new DatabaseSync(dbPath, { readOnly: true });
db.exec('PRAGMA busy_timeout = 5000');
const failedExecutions = () =>
  db.prepare(`SELECT count(*) AS count FROM execution_entity e JOIN workflow_entity w ON w.id=e.workflowId
    WHERE e.status='error' AND w.name='ZURI Smart Grid | 02 - Processador Standard Normal'`).get().count;

query(`INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
  VALUES ('event','homolog-event',100000);
  CREATE FUNCTION p02_test_claim_failure() RETURNS trigger LANGUAGE plpgsql AS $$
  BEGIN
    IF NEW.google_maps_url LIKE '%fault=claim%' AND NEW.status = 'claimed' THEN
      RAISE EXCEPTION 'synthetic claim failure';
    END IF;
    RETURN NEW;
  END $$;
  CREATE TRIGGER p02_test_claim_failure BEFORE UPDATE ON geogrid_requests
    FOR EACH ROW EXECUTE FUNCTION p02_test_claim_failure()`);
const before = failedExecutions();
const claimId = insertRequest('claim');
await waitFor(() => failedExecutions() > before, 'failed n8n claim execution');
assert.equal(query(`SELECT status || '|' || budget_reserved_usd FROM geogrid_requests
  WHERE request_id='${claimId}'`), 'queued|0.000000');
query(`UPDATE geogrid_requests SET status='manual_review' WHERE request_id='${claimId}'`);
console.log('PASS claim error stops the real n8n execution without reserving funds');

const cases = [
  ['invalid', 'Extract Exact Google Place ID', 'INVALID_PLACE_ID', 'before_authorization'],
  ['places', 'Resolve Exact Place Details', 'CONNECTION_ERROR', 'before_authorization'],
  ['timeout', 'Resolve Exact Place Details', 'TIMEOUT', 'before_authorization'],
  ['rate', 'Resolve Exact Place Details', 'RATE_LIMIT', 'before_authorization'],
  ['auth', 'Resolve Exact Place Details', 'AUTHENTICATION_ERROR', 'before_authorization'],
  ['register', 'Register Business in Smart Grid', 'CONNECTION_ERROR', 'before_authorization'],
  ['scan', 'Create Idempotent Smart Grid Scan', 'CONNECTION_ERROR', 'before_authorization'],
  ['grid', 'Build Nine Standard Tasks', 'API_CONTRACT_ERROR', 'before_authorization'],
  ['grid-persist', 'Persist Grid Before Paid Submission', 'PERSISTENCE_ERROR', 'before_authorization'],
  ['authorize', 'Authorize Paid task_post Exactly Once', 'PERSISTENCE_ERROR', 'before_authorization'],
  ['post', 'Paid DataForSEO task_post - No Automatic Retry', 'SUBMISSION_UNCERTAIN', 'after_authorization'],
  ['associate', 'Associate and Classify task_post Response', 'SUBMISSION_UNCERTAIN', 'after_authorization'],
  ['ids-persist', 'Persist DataForSEO IDs and Cost', 'SUBMISSION_UNCERTAIN', 'after_authorization'],
  ['outcomes', 'Log Persisted Submission Outcomes', 'PERSISTENCE_ERROR', 'after_response_persisted'],
  ['callback-build', 'Build Failed Submission Point Callbacks', 'PERMANENT_ERROR', 'after_response_persisted'],
  ['callback-report', 'Report Failed Submission Points', 'CONNECTION_ERROR', 'after_response_persisted'],
  ['callback-persist', 'Record Failed Point Callback Delivery', 'PERSISTENCE_ERROR', 'after_response_persisted'],
];
for (const [fault, node, category, phase] of cases) {
  const id = insertRequest(fault);
  const details = await waitFor(() => {
    const result = query(`SELECT details FROM geogrid_execution_logs WHERE request_id='${id}'
      AND event_type='processor_error' AND details->>'node'='${node}' ORDER BY created_at LIMIT 1`);
    return result && JSON.parse(result);
  }, fault);
  assert.equal(details.node, node, fault);
  assert.equal(details.category, category, fault);
  assert.equal(details.paid_phase, phase, fault);
  assert(details.execution_id && details.stage && details.attempt > 0);
  if (fault === 'rate') {
    assert.equal(details.retry_after_seconds, 6);
    assert(details.scheduled_delay_seconds >= 6);
  }
  assert(!JSON.stringify(details).includes('synthetic@example.test'));
  const state = query(`SELECT status,budget_reservation_state,budget_reserved_usd,
    task_post_authorization_count FROM geogrid_requests WHERE request_id='${id}'`).split('|');
  if (phase === 'after_authorization') {
    assert.equal(state[3], '1', fault);
    if (['associate', 'ids-persist'].includes(fault)) {
      assert(['submitted', 'processing', 'done', 'partial'].includes(state[0]), fault);
      assert.equal(state[1], 'consumed', fault);
      assert.equal(Number(state[2]), 0, fault);
      assert.equal(query(`SELECT count(*) FROM geogrid_tasks WHERE request_id='${id}'
        AND task_id IS NOT NULL`), '9', fault);
      assert.equal(query(`SELECT recovered_at IS NOT NULL FROM geogrid_paid_response_journal
        WHERE request_id='${id}'`), 't', fault);
    } else {
      assert.deepEqual(state.slice(0, 2), ['submission_uncertain', 'held_uncertain'], fault);
      assert(Number(state[2]) > 0, fault);
    }
  } else if (phase === 'after_response_persisted') {
    assert.equal(state[3], '1', fault);
    if (fault.startsWith('callback-')) {
      assert.equal(state[1], 'held_unknown_cost', fault);
      assert(Number(state[2]) > 0, fault);
    }
  } else {
    assert.equal(Number(state[2]), 0, fault);
    assert.equal(state[3], '0', fault);
    if (state[0] === 'queued') {
      query(`UPDATE geogrid_requests SET status='manual_review' WHERE request_id='${id}'
        AND status='queued'`);
    }
  }
  console.log(`PASS ${fault}: ${node} ${category}`);
}
const metrics = await (await fetch('http://mock-services:8081/__mock__/metrics')).json();
assert.equal(metrics.counts.task_post, 6, 'Only expected requests reach the simulator');
await sleep(3000);
const after = await (await fetch('http://mock-services:8081/__mock__/metrics')).json();
assert.equal(after.counts.task_post, 6, 'Uncertain submissions must not be posted again');
db.close();
console.log(`P02_N8N_FAULTS_PASSED: claim and ${cases.length} induced failures`);
