#!/usr/bin/env node

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);
const psqlArgs = ['-X', '-v', 'ON_ERROR_STOP=1', '-A', '-t', '-F', '|', '-c'];

async function query(sql) {
  const { stdout } = await execFileAsync('psql', [...psqlArgs, sql], {
    env: process.env,
    maxBuffer: 1024 * 1024
  });
  return stdout.trim();
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function requestId(index) {
  return `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
}

function requestValues(index) {
  const id = requestId(index);
  return `('${id}', 'p01:${id}', 'fp:${id}', 'p01-event',
    'Synthetic QA', 'P01 Test Company', 'qa-${index}@example.test',
    '+15550000000', 'Test City', 'TS', NULL, 'test keyword', now())`;
}

function makeGrid() {
  return JSON.stringify(Array.from({ length: 9 }, (_, point) => ({
    row: Math.floor(point / 3),
    col: point % 3,
    lat: -23.55 + Math.floor(point / 3) * 0.01,
    lng: -46.63 + (point % 3) * 0.01
  })));
}

async function expectRejected(promise, message) {
  let rejected = false;
  try {
    await promise;
  } catch {
    rejected = true;
  }
  assert(rejected, message);
}

async function main() {
  const ids = Array.from({ length: 20 }, (_, i) => requestId(200 + i));
  const expiredId = requestId(100);
  const legacyId = requestId(50);
  const legacyPaidId = requestId(51);
  assert(await query('SELECT count(*) FROM geogrid_requests') === '0',
    'P01 requires a new empty isolated database');
  await query(`
    INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd,reserved_amount_usd)
    VALUES ('event','p01-legacy',100,0.0009),
           ('daily','2025-01-01',100,0.0009),
           ('event','p01-legacy-paid',100,0.0009),
           ('daily','2025-01-03',100,0.0009);
    INSERT INTO geogrid_requests(
      request_id,idempotency_key,payload_fingerprint,campaign_id,contact_name,
      company_name,email,whatsapp,city,state,keyword,consent_at,status,
      claim_token,claim_expires_at,claimed_at,budget_reserved_usd
    ) VALUES (
      '${legacyId}','legacy:${legacyId}','legacy-fp','p01-legacy','Synthetic QA',
      'Legacy Company','legacy@example.test','+15550000000','Test City','TS',
      'test',now(),'claimed','ffffffff-0000-4000-8000-000000000002',
      now()-interval '1 minute',now()-interval '1 day',0.0009
    ), (
      '${legacyPaidId}','legacy:${legacyPaidId}','legacy-paid-fp','p01-legacy-paid',
      'Synthetic QA','Legacy Paid','paid@example.test','+15550000000','Test City',
      'TS','test',now(),'submitted',NULL,NULL,now()-interval '1 day',0.0009
    );
    INSERT INTO geogrid_tasks(request_id,point_index,row_index,col_index,
      latitude,longitude,tag,task_id,state)
    SELECT '${legacyPaidId}'::uuid, n, n/3, n%3, -23.55, -46.63,
      'legacy-paid-' || n, 'legacy-task-' || n, 'submitted'
    FROM generate_series(0,8) AS n
  `);
  await execFileAsync('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-f', '/app/migration.sql'], {
    env: process.env, maxBuffer: 1024 * 1024
  });
  const legacyState = await query(`SELECT status,budget_reservation_state,
    budget_reserved_scope_daily_key,budget_reserved_usd
    FROM geogrid_requests WHERE request_id='${legacyId}'`);
  assert(legacyState === 'manual_review|legacy_unreconciled||0.000900',
    `Legacy reservation must remain held without an inferred date: ${legacyState}`);
  await expectRejected(query(`SELECT geogrid_reconcile_legacy_reservation(
    '${legacyId}','2025-01-02','verified-case-001')`),
    'A scope without sufficient reserved funds must be rejected');
  assert(await query(`SELECT geogrid_reconcile_legacy_reservation(
    '${legacyId}','2025-01-01','verified-case-001')`) === 't',
    'Verified legacy reservation should be released once');
  await expectRejected(query(`SELECT geogrid_reconcile_legacy_reservation(
    '${legacyId}','2025-01-01','verified-case-001')`),
    'Legacy reservation cannot be released twice');
  assert(await query("SELECT reserved_amount_usd FROM geogrid_budget WHERE scope_type='daily' AND scope_key='2025-01-01'") === '0.000000',
    'Legacy release should affect only the evidenced daily scope');
  assert(await query(`SELECT budget_reservation_state,budget_reserved_scope_daily_key
    FROM geogrid_requests WHERE request_id='${legacyPaidId}'`) === 'legacy_unreconciled|',
    'Paid legacy reservation must remain held with no guessed scope');
  assert(await query(`SELECT geogrid_reconcile_legacy_reservation(
    '${legacyPaidId}','2025-01-03','verified-case-002')`) === 't',
    'Paid legacy scope should be bound without releasing its reservation');
  assert(await query(`SELECT budget_reservation_state,budget_reserved_usd
    FROM geogrid_requests WHERE request_id='${legacyPaidId}'`) === 'held_unknown_cost|0.000900',
    'Paid legacy reservation must remain held until cost reconciliation');
  assert(await query(`SELECT geogrid_reconcile_post_cost(
    '${legacyPaidId}',0.0003,'verified-cost-002')`) === 't',
    'Verified paid legacy cost should reconcile');
  console.log('PASS legacy: no inferred scope; verified reconciliation releases once');
  const authSql = `
    UPDATE geogrid_budget
    SET daily_cap_usd=100, default_event_cap_usd=100,
        estimated_task_cost_usd=0.0001, max_pending_tasks=10000,
        new_diagnostics_per_minute=1000, enabled=TRUE
    WHERE scope_type='system' AND scope_key='default';
    INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
    VALUES ('event','p01-event',100), ('event','p01-expired',100)
    ON CONFLICT(scope_type,scope_key) DO UPDATE
    SET cap_amount_usd=EXCLUDED.cap_amount_usd,
        spent_amount_usd=0, reserved_amount_usd=0;
    INSERT INTO geogrid_requests(
      request_id,idempotency_key,payload_fingerprint,campaign_id,
      contact_name,company_name,email,whatsapp,city,state,google_maps_url,
      keyword,consent_at
    ) VALUES ${[...Array.from({ length: 20 }, (_, i) => requestValues(200 + i)),
      `('${expiredId}','p01:${expiredId}','fp:${expiredId}','p01-expired',
        'Synthetic QA','Expired Claim Company','expired@example.test',
        '+15550000000','Test City','TS',NULL,'test keyword',now())`].join(',')};
    INSERT INTO geogrid_budget(scope_type,scope_key,cap_amount_usd)
    VALUES ('daily',to_char((now() AT TIME ZONE 'UTC')::date - 1,'YYYY-MM-DD'),100)
    ON CONFLICT(scope_type,scope_key) DO UPDATE
    SET cap_amount_usd=100,spent_amount_usd=0,reserved_amount_usd=0;
    UPDATE geogrid_requests SET status='manual_review' WHERE request_id='${expiredId}';
  `;
  await query(authSql);

  const claims = await Promise.all(ids.map(async () => {
    const output = await query(
      "SELECT request_id,claim_token FROM geogrid_claim_next_request('p01-worker')"
    );
    return output.split('|');
  }));
  assert(claims.length === 20, 'Expected 20 atomic claims');
  assert(new Set(claims.map(([id]) => id)).size === 20, 'A request was claimed more than once');
  console.log('PASS concurrent claims: 20 workers claimed 20 distinct requests');

  const [request, token] = claims[0];
  const grid = makeGrid();
  await expectRejected(query(`SELECT geogrid_prepare_tasks(
    '${request}'::uuid,'ffffffff-0000-4000-8000-000000000099'::uuid,'p01-scan-${request}',
    'p01-business',-23.55,-46.63,'${grid}'::jsonb)`),
    'A different worker cannot prepare another worker claim');
  await query(`SELECT geogrid_prepare_tasks(
    '${request}'::uuid,'${token}'::uuid,'p01-scan-${request}',
    'p01-business',-23.55,-46.63,'${grid}'::jsonb)`);
  await expectRejected(
    query(`SELECT geogrid_prepare_tasks(
      '${request}'::uuid,'${token}'::uuid,'p01-scan-${request}',
      'p01-business',-23.55,-46.63,'${grid}'::jsonb)`),
    'Repeated preparation must be rejected'
  );
  await query(`UPDATE geogrid_requests SET claim_expires_at=now()-interval '1 second'
    WHERE request_id='${request}'`);
  await expectRejected(query(`SELECT geogrid_authorize_task_post(
    '${request}'::uuid,'${token}'::uuid,'p01-scan-${request}')`),
    'An expired claim cannot authorize a paid submission');
  await query(`UPDATE geogrid_requests SET claim_expires_at=now()+interval '10 minutes'
    WHERE request_id='${request}'`);
  const authResults = await Promise.allSettled(Array.from({ length: 20 }, () =>
    query(`SELECT geogrid_authorize_task_post(
      '${request}'::uuid,'${token}'::uuid,'p01-scan-${request}')`)
  ));
  const authorized = authResults.filter(
    result => result.status === 'fulfilled' && result.value === 't'
  );
  assert(authorized.length === 1, 'Expected exactly one successful task_post authorization');
  assert(authResults.filter(result => result.status === 'rejected').length === 19,
    'Repeated/concurrent authorizations should be rejected');
  const authState = await query(`SELECT task_post_authorization_count,
      count(*) FILTER (WHERE t.state='submission_started')
    FROM geogrid_requests r JOIN geogrid_tasks t USING(request_id)
    WHERE r.request_id='${request}' GROUP BY task_post_authorization_count`);
  assert(authState === '1|9', 'Authorization must arm exactly nine tasks once');
  console.log('PASS one-time authorization: 1/20 concurrent authorizations succeeded');

  await query(`UPDATE geogrid_requests SET claim_expires_at=now()-interval '1 second'
    WHERE request_id='${request}'`);
  const recovery = await query('SELECT * FROM geogrid_recover_expired_work()');
  assert(recovery === '0|1', `Expected armed request to become uncertain, got ${recovery}`);
  const uncertain = await query(`SELECT r.status,r.budget_reservation_state,
      count(t.task_id),r.task_post_authorization_count
    FROM geogrid_requests r JOIN geogrid_tasks t USING(request_id)
    WHERE r.request_id='${request}'
    GROUP BY r.status,r.budget_reservation_state,r.task_post_authorization_count`);
  assert(uncertain === 'submission_uncertain|held_uncertain|0|1',
    'Restart recovery must hold reservation and prohibit repost without task IDs');
  await expectRejected(
    query(`SELECT geogrid_authorize_task_post(
      '${request}'::uuid,'${token}'::uuid,'p01-scan-${request}')`),
    'Authorization after uncertain submission must be rejected'
  );
  console.log('PASS uncertain recovery: no task IDs, reservation held, repost blocked');

  await query(`
    UPDATE geogrid_budget SET reserved_amount_usd=0.0009
    WHERE scope_type='daily'
      AND scope_key=to_char((now() AT TIME ZONE 'UTC')::date - 1,'YYYY-MM-DD');
    UPDATE geogrid_budget SET reserved_amount_usd=0.0009
    WHERE scope_type='event' AND scope_key='p01-expired';
    UPDATE geogrid_requests
    SET status='claimed',claim_owner='expired-worker',
        claim_token='ffffffff-0000-4000-8000-000000000001',
        claim_expires_at=now()-interval '1 second',claimed_at=now()-interval '1 day',
        budget_reserved_usd=0.0009,
        budget_reserved_scope_daily_key=to_char((now() AT TIME ZONE 'UTC')::date - 1,'YYYY-MM-DD'),
        budget_reservation_state='reserved'
    WHERE request_id='${expiredId}'`);
  const expirationRuns = await Promise.all(Array.from({ length: 8 }, () =>
    query('SELECT * FROM geogrid_recover_expired_work()')
  ));
  assert(expirationRuns.filter(output => output === '1|0').length === 1,
    `Expected exactly one expired-claim reservation release, got ${expirationRuns}`);
  const expiredState = await query(`SELECT status,budget_reserved_usd,
      budget_reservation_state,budget_reserved_scope_daily_key
    FROM geogrid_requests WHERE request_id='${expiredId}'`);
  const oldScope = await query(`SELECT reserved_amount_usd FROM geogrid_budget
    WHERE scope_type='daily'
      AND scope_key=to_char((now() AT TIME ZONE 'UTC')::date - 1,'YYYY-MM-DD')`);
  const expiredEvent = await query(`SELECT reserved_amount_usd FROM geogrid_budget
    WHERE scope_type='event' AND scope_key='p01-expired'`);
  assert(expiredState === 'queued|0.000000|released|',
    `Expired claim should be requeued without reservation, got ${expiredState}`);
  assert(oldScope === '0.000000' && expiredEvent === '0.000000',
    'Expired reservation must release from its original daily and event scopes');
  const newClaim = await query(
    "SELECT request_id,claim_token FROM geogrid_claim_next_request('p01-reclaimer')"
  );
  assert(newClaim.startsWith(`${expiredId}|`), 'Recovered request should be claimable again');
  const newReservation = await query(`SELECT budget_reserved_usd,
      budget_reserved_scope_daily_key,budget_reservation_state
    FROM geogrid_requests WHERE request_id='${expiredId}'`);
  assert(newReservation.endsWith('|reserved'), 'Reclaimed request should have one new reservation');
  const currentScope = await query(`SELECT reserved_amount_usd FROM geogrid_budget
    WHERE scope_type='daily'
      AND scope_key=to_char((now() AT TIME ZONE 'UTC')::date,'YYYY-MM-DD')`);
  const eventScope = await query(`SELECT reserved_amount_usd FROM geogrid_budget
    WHERE scope_type='event' AND scope_key='p01-expired'`);
  assert(currentScope === '0.018900' && eventScope === '0.000900',
    'Reclaim must reserve exactly once in the new daily and event scopes');
  console.log('PASS expired reservation: released from previous UTC day and reserved once after reclaim');

  const zeroCostRequest = claims[1][0];
  const zeroCostToken = claims[1][1];
  const zeroCostScan = `p01-scan-${zeroCostRequest}`;
  await query(`SELECT geogrid_prepare_tasks(
    '${zeroCostRequest}'::uuid,'${zeroCostToken}'::uuid,'${zeroCostScan}',
    'p01-business',-23.55,-46.63,'${grid}'::jsonb)`);
  await query(`SELECT geogrid_authorize_task_post(
    '${zeroCostRequest}'::uuid,'${zeroCostToken}'::uuid,'${zeroCostScan}')`);
  const knownZeroResults = JSON.stringify(Array.from({ length: 9 }, (_, point) => ({
    row: Math.floor(point / 3),
    col: point % 3,
    task_id: `mock-${zeroCostRequest}-${point}`,
    status_code: 20100,
    status_message: 'Task Created.',
    cost: 0
  })));
  const savedZero = await query(`SELECT geogrid_save_post_response(
    '${zeroCostRequest}'::uuid,'${zeroCostToken}'::uuid,
    '${knownZeroResults}'::jsonb,0)`);
  assert(savedZero === 'submitted', 'Known zero-cost response should persist');
  const zeroCostState = await query(`SELECT actual_cost_known,actual_cost_usd,
      budget_reserved_usd,budget_reservation_state
    FROM geogrid_requests WHERE request_id='${zeroCostRequest}'`);
  assert(zeroCostState === 't|0.000000|0.000000|consumed',
    `Known zero cost was not distinguished from unknown: ${zeroCostState}`);
  assert(await query(`SELECT geogrid_save_post_response(
      '${zeroCostRequest}'::uuid,'${zeroCostToken}'::uuid,
      '${knownZeroResults}'::jsonb,0)`) === 'submitted',
    'Persisting the same response again must be idempotent');
  const zeroCostSpent = await query(`SELECT spent_amount_usd FROM geogrid_budget
    WHERE scope_type='event' AND scope_key='p01-event'`);
  assert(zeroCostSpent === '0.000000', 'A known zero cost must not add spend');
  console.log('PASS cost reconciliation: known zero releases reservation once; response persistence is idempotent');

  const unknownCostRequest = claims[2][0];
  const unknownCostToken = claims[2][1];
  const unknownCostScan = `p01-scan-${unknownCostRequest}`;
  await query(`SELECT geogrid_prepare_tasks(
    '${unknownCostRequest}'::uuid,'${unknownCostToken}'::uuid,'${unknownCostScan}',
    'p01-business',-23.55,-46.63,'${grid}'::jsonb)`);
  await query(`SELECT geogrid_authorize_task_post(
    '${unknownCostRequest}'::uuid,'${unknownCostToken}'::uuid,'${unknownCostScan}')`);
  const unknownCostResults = JSON.stringify(Array.from({ length: 9 }, (_, point) => ({
    row: Math.floor(point / 3),
    col: point % 3,
    task_id: `mock-unknown-${unknownCostRequest}-${point}`,
    status_code: 20100,
    status_message: 'Task Created.',
    cost: null
  })));
  await query(`SELECT geogrid_save_post_response(
    '${unknownCostRequest}'::uuid,'${unknownCostToken}'::uuid,
    '${unknownCostResults}'::jsonb,NULL)`);
  const unknownCostState = await query(`SELECT actual_cost_known,actual_cost_usd,
      budget_reserved_usd,budget_reservation_state
    FROM geogrid_requests WHERE request_id='${unknownCostRequest}'`);
  assert(unknownCostState === 'f|0.000000|0.000900|held_unknown_cost',
    `Unknown cost must retain its reservation: ${unknownCostState}`);
  assert(await query(`SELECT count(*) FROM geogrid_tasks WHERE request_id='${unknownCostRequest}' AND task_id IS NOT NULL`) === '9',
    'All task IDs must be preserved while cost is unknown');
  await expectRejected(query(`SELECT geogrid_reconcile_post_cost(
    '${unknownCostRequest}',0.0000005,'verified-cost-001')`),
    'Costs that cannot be represented exactly must be rejected');
  const concurrentCosts = await Promise.all(Array.from({ length: 8 }, () =>
    query(`SELECT geogrid_reconcile_post_cost(
      '${unknownCostRequest}',0.0005,'verified-cost-001')`)));
  assert(concurrentCosts.every(result => result === 't'),
    'Concurrent identical reconciliation must succeed idempotently');
  assert(await query(`SELECT geogrid_reconcile_post_cost(
    '${unknownCostRequest}',0.0005,'verified-cost-001')`) === 't',
    'Repeated reconciliation must be idempotent');
  await expectRejected(query(`SELECT geogrid_reconcile_post_cost(
    '${unknownCostRequest}',0.0006,'verified-cost-001')`),
    'Conflicting costs must be rejected');
  assert(await query(`SELECT actual_cost_known,actual_cost_accounted,actual_cost_usd,
    budget_reserved_usd,budget_reservation_state FROM geogrid_requests
    WHERE request_id='${unknownCostRequest}'`) === 't|t|0.000500|0.000000|consumed',
    'Cost must be accounted and reservation released once');
  assert(await query("SELECT spent_amount_usd FROM geogrid_budget WHERE scope_type='event' AND scope_key='p01-event'") === '0.000500',
    'Repeated reconciliation must not double count spend');
  console.log('PASS unknown cost: reservation held and cost_known=false');

  const lateResults = JSON.stringify(Array.from({ length: 9 }, (_, point) => ({
    row: Math.floor(point / 3), col: point % 3,
    task_id: `late-${request}-${point}`, status_code: 20100, cost: null
  })));
  await expectRejected(query(`SELECT geogrid_save_post_response(
    '${request}','ffffffff-0000-4000-8000-000000000099','${lateResults}'::jsonb,NULL)`),
    'A late response with a different token must be rejected');
  assert(await query(`SELECT geogrid_save_post_response(
    '${request}','${token}','${lateResults}'::jsonb,NULL)`) === 'submitted',
    'The original authorization must allow late ID persistence after recovery');
  assert(await query(`SELECT count(*) FROM geogrid_tasks
    WHERE request_id='${request}' AND task_id IS NOT NULL`) === '9',
    'Late response must preserve all task IDs');
  assert(await query(`SELECT geogrid_save_post_response(
    '${request}','${token}','${lateResults}'::jsonb,NULL)`) === 'submitted',
    'Repeated identical late response must be idempotent');
  await expectRejected(query(`SELECT geogrid_save_post_response(
    '${request}','${token}','${lateResults}'::jsonb,0)`),
    'Conflicting repeated response must be rejected');
  assert(await query(`SELECT geogrid_reconcile_post_cost(
    '${request}',0,'verified-late-zero')`) === 't',
    'Late response with zero cost should reconcile');
  await expectRejected(query(`SELECT geogrid_authorize_task_post(
    '${request}','${token}','p01-scan-${request}')`),
    'Late response must not reauthorize a paid POST');
  console.log('PASS late IDs: uncertain submission recovered without repost; zero cost reconciled');

  const beforeMigrationRepeat = await query(`SELECT string_agg(
      request_id::text || ':' || status || ':' || budget_reservation_state || ':' ||
      budget_reserved_usd::text || ':' || actual_cost_usd::text || ':' ||
      actual_cost_known::text || ':' || task_post_authorization_count::text, ','
      ORDER BY request_id)
    FROM geogrid_requests`);
  await execFileAsync('psql', ['-X', '-v', 'ON_ERROR_STOP=1', '-f', '/app/migration.sql'], {
    env: process.env, maxBuffer: 1024 * 1024
  });
  const afterMigrationRepeat = await query(`SELECT string_agg(
      request_id::text || ':' || status || ':' || budget_reservation_state || ':' ||
      budget_reserved_usd::text || ':' || actual_cost_usd::text || ':' ||
      actual_cost_known::text || ':' || task_post_authorization_count::text, ','
      ORDER BY request_id)
    FROM geogrid_requests`);
  assert(beforeMigrationRepeat === afterMigrationRepeat,
    'Reapplying the migration must preserve reconciled request states');
  console.log('PASS migration repeat: recorded request state preserved');

  console.log('P01_POSTGRES_TESTS_PASSED');
}

main().catch(error => {
  console.error(`P01_POSTGRES_TESTS_FAILED: ${error.message}`);
  process.exitCode = 1;
});
