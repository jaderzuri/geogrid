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
async function migration(path) {
  await exec('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-f', path], { env: process.env });
}
const requestId = i => `30000000-0000-4000-8000-${String(i).padStart(12, '0')}`;
async function addRequest(i) {
  const id = requestId(i);
  await query(`INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
    VALUES ('event','p03-event',1000) ON CONFLICT DO NOTHING;
    INSERT INTO geogrid_requests(request_id,idempotency_key,payload_fingerprint,campaign_id,
      contact_name,company_name,email,whatsapp,city,state,keyword,consent_at)
    VALUES ('${id}','p03-${i}','p03-fp-${i}','p03-event','Synthetic','Synthetic',
      'synthetic@example.test','+15550000000','Test','TS','test',now())`);
  return id;
}
async function claim(worker = 'p03-worker') {
  const result = await query(`SELECT request_id,claim_token FROM geogrid_claim_processor_request('${worker}')`);
  if (!result) return null;
  const [id, token] = result.split('|');
  return { id, token };
}
const errorCall = (id, token, node, category, retryAfter = 'NULL') =>
  `SELECT geogrid_record_processor_error('${id}','${token}','${node}','${category}',NULL,'p03-exec',${retryAfter})`;

await migration('/app/migration-006.sql');
await migration('/app/migration-007.sql');
await migration('/app/migration-008.sql');
await query(`UPDATE geogrid_budget SET daily_cap_usd=1000,default_event_cap_usd=1000,
  estimated_task_cost_usd=0.0001,max_pending_tasks=10000,new_diagnostics_per_minute=1000
  WHERE scope_type='system' AND scope_key='default';
  UPDATE geogrid_retry_policy SET max_attempts=3,base_delay_seconds=1,
  max_delay_seconds=20,jitter_percent=0 WHERE policy_key='processor'`);

const retryId = await addRequest(1);
let owned = await claim();
assert.equal(owned.id, retryId);
await query(errorCall(retryId, owned.token, 'Resolve Exact Place Details', 'RATE_LIMIT', 4));
let state = (await query(`SELECT status,next_attempt_at>now() AS scheduled,budget_reserved_usd
  FROM geogrid_requests WHERE request_id='${retryId}'`)).split('|');
assert.equal(state[0], 'queued');
assert.equal(state[1], 't');
assert.equal(Number(state[2]), 0);
assert.equal(await claim('too-early-worker'), null, 'A scheduled retry cannot be claimed early');
const delay = Number(await query(`SELECT details->>'scheduled_delay_seconds'
  FROM geogrid_execution_logs WHERE request_id='${retryId}' AND event_type='processor_error'`));
assert(delay >= 4, `Retry-After should be respected; got ${delay}s`);
await query(`UPDATE geogrid_requests SET next_attempt_at=now() WHERE request_id='${retryId}'`);
owned = await claim();
assert.equal(owned.id, retryId);
await query(errorCall(retryId, owned.token, 'Create Idempotent Smart Grid Scan', 'TIMEOUT'));
state = (await query(`SELECT status,budget_reserved_usd FROM geogrid_requests WHERE request_id='${retryId}'`)).split('|');
assert.equal(state[0], 'queued');
assert.equal(Number(state[1]), 0);
await query(`UPDATE geogrid_requests SET attempt_count=2,next_attempt_at=now() WHERE request_id='${retryId}'`);
owned = await claim();
assert.equal(owned.id, retryId, 'Final configured attempt remains available');
await query(errorCall(retryId, owned.token, 'Register Business in Smart Grid', 'CONNECTION_ERROR'));
assert.equal(await query(`SELECT status FROM geogrid_requests WHERE request_id='${retryId}'`), 'manual_review');
assert.equal(await query(`SELECT count(*) FROM geogrid_execution_logs
  WHERE request_id='${retryId}' AND details->>'retry_limit_reached'='true'`), '1');
console.log('PASS bounded retries, Retry-After, delayed claiming, released reservations and terminal limit');

const permanentId = await addRequest(2);
owned = await claim();
assert.equal(owned.id, permanentId);
await query(errorCall(permanentId, owned.token, 'Register Business in Smart Grid', 'AUTHENTICATION_ERROR'));
assert.equal(await query(`SELECT status FROM geogrid_requests WHERE request_id='${permanentId}'`), 'manual_review');
assert.equal(await query(`SELECT next_attempt_at IS NULL FROM geogrid_requests
  WHERE request_id='${permanentId}'`), 't');
console.log('PASS permanent failures are not automatically retried');

const expiryId = await addRequest(3);
owned = await claim();
assert.equal(owned.id, expiryId);
await query(`UPDATE geogrid_requests SET claim_expires_at=now()-interval '1 second'
  WHERE request_id='${expiryId}'`);
await query('SELECT geogrid_release_expired_claim_reservations()');
state = (await query(`SELECT status,budget_reservation_state,budget_reserved_usd,
  next_attempt_at>now() FROM geogrid_requests WHERE request_id='${expiryId}'`)).split('|');
assert.deepEqual(state, ['queued', 'released', '0.000000', 't']);
console.log('PASS expired pre-submission claim releases reservation and schedules recovery');

const paidId = await addRequest(4);
owned = await claim();
assert.equal(owned.id, paidId);
const points = JSON.stringify(Array.from({ length: 9 }, (_, i) =>
  ({ row: Math.floor(i / 3), col: i % 3, lat: -23.55, lng: -46.63 })));
await query(`SELECT geogrid_prepare_tasks('${paidId}','${owned.token}','p03-scan','p03-business',
  -23.55,-46.63,'${points}'::jsonb)`);
await query(`SELECT geogrid_authorize_task_post('${paidId}','${owned.token}','p03-scan')`);
const response = {
  status_code: 20000, status_message: 'Ok.', cost: 0.0009,
  tasks: Array.from({ length: 9 }, (_, i) => ({
    id: `p03-task-${i}`, status_code: 20100, cost: 0.0001,
    data: { tag: `${paidId}:${Math.floor(i / 3)}:${i % 3}` }
  }))
};
const raw = JSON.stringify(response).replaceAll("'", "''");
await query(`SELECT geogrid_journal_paid_response('${paidId}','${owned.token}',
  'p03-journal-exec','${raw}'::jsonb)`);
await query(`SELECT geogrid_journal_paid_response('${paidId}','${owned.token}',
  'p03-journal-replay','${raw}'::jsonb)`);
assert.equal(await query(`SELECT recovery_attempts FROM geogrid_paid_response_journal
  WHERE request_id='${paidId}'`), '0');
const recoveryClaims = await Promise.all(Array.from({ length: 8 }, (_, i) =>
  query(`SELECT row_to_json(r) FROM geogrid_claim_paid_response_recovery('p03-recovery-${i}') r`)
));
assert.equal(recoveryClaims.filter(Boolean).length, 1, 'Only one concurrent worker may claim a journal row');
const recovery = JSON.parse(recoveryClaims.find(Boolean));
assert.equal(recovery.request_id, paidId);
assert.equal(recovery.point_map.length, 9);
const mapped = recovery.point_map.map(point => {
  const task = response.tasks.find(item => item.data.tag === point.tag);
  return { row: point.row, col: point.col, task_id: task.id, status_code: task.status_code,
    status_message: 'Task Created.', cost: task.cost };
});
const mappedJson = JSON.stringify(mapped).replaceAll("'", "''");
await query(`SELECT geogrid_save_post_response('${paidId}','${owned.token}',
  '${mappedJson}'::jsonb,0.0009)`);
assert.equal(await query(`SELECT geogrid_save_post_response('${paidId}','${owned.token}',
  '${mappedJson}'::jsonb,0.0009)`), 'submitted');
assert.equal(await query(`SELECT geogrid_mark_paid_response_recovered('${paidId}',
  '${recovery.recovery_token}')`), 't');
assert.equal(await query(`SELECT count(*) FROM geogrid_tasks WHERE request_id='${paidId}'
  AND task_id IS NOT NULL`), '9');
assert.equal(await query(`SELECT task_post_authorization_count FROM geogrid_requests
  WHERE request_id='${paidId}'`), '1');
assert.equal(await query(`SELECT recovered_at IS NOT NULL FROM geogrid_paid_response_journal
  WHERE request_id='${paidId}'`), 't');
assert.equal(await query(`SELECT count(*) FROM geogrid_claim_paid_response_recovery('replay')`), '0');
console.log('PASS durable response recovery, late id association, repeated persistence and one authorization');

const unknownId = await addRequest(5);
owned = await claim();
assert.equal(owned.id, unknownId);
await query(`SELECT geogrid_prepare_tasks('${unknownId}','${owned.token}','p03-unknown-scan',
  'p03-business',-23.55,-46.63,'${points}'::jsonb)`);
await query(`SELECT geogrid_authorize_task_post('${unknownId}','${owned.token}','p03-unknown-scan')`);
const unknownResponse = {
  status_code: 20000, status_message: 'Ok.',
  tasks: Array.from({ length: 9 }, (_, i) => ({
    id: `p03-unknown-task-${i}`, status_code: 20100,
    data: { tag: `${unknownId}:${Math.floor(i / 3)}:${i % 3}` }
  }))
};
const unknownRaw = JSON.stringify(unknownResponse).replaceAll("'", "''");
await query(`SELECT geogrid_journal_paid_response('${unknownId}','${owned.token}',
  'p03-unknown-exec','${unknownRaw}'::jsonb)`);
const unknownRecovery = JSON.parse(await query(`SELECT row_to_json(r) FROM
  geogrid_claim_paid_response_recovery('p03-unknown-recovery') r`));
const unknownMapped = unknownRecovery.point_map.map(point => {
  const task = unknownResponse.tasks.find(item => item.data.tag === point.tag);
  return { row: point.row, col: point.col, task_id: task.id, status_code: task.status_code,
    status_message: 'Task Created.', cost: null };
});
const unknownMappedJson = JSON.stringify(unknownMapped).replaceAll("'", "''");
await query(`SELECT geogrid_save_post_response('${unknownId}','${owned.token}',
  '${unknownMappedJson}'::jsonb,NULL)`);
await query(`SELECT geogrid_mark_paid_response_recovered('${unknownId}',
  '${unknownRecovery.recovery_token}')`);
assert.equal(await query(`SELECT budget_reservation_state FROM geogrid_requests
  WHERE request_id='${unknownId}'`), 'held_unknown_cost');
assert.equal(await query(`SELECT count(*) FROM geogrid_tasks WHERE request_id='${unknownId}'
  AND task_id IS NOT NULL`), '9');
await query(`SELECT geogrid_reconcile_post_cost('${unknownId}',0.0009,'verified-cost-0005')`);
assert.equal(await query(`SELECT budget_reservation_state FROM geogrid_requests
  WHERE request_id='${unknownId}'`), 'consumed');
assert.equal(await query(`SELECT count(*) FROM geogrid_tasks WHERE request_id='${unknownId}'
  AND task_id IS NOT NULL`), '9');
console.log('PASS unknown cost retains IDs and reservation until later reconciliation');

console.log('P03_POSTGRES_TESTS_PASSED');
