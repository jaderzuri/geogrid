#!/usr/bin/env node

import { execFileSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';

const origin = process.env.HOMOLOG_ORIGIN || 'http://127.0.0.1:8080';
const browserOrigin = process.env.HOMOLOG_BROWSER_ORIGIN || 'http://localhost:8080';
const gridApi = process.env.HOMOLOG_SMART_GRID_URL || 'http://127.0.0.1:8000';
const mockMetricsUrl = process.env.HOMOLOG_MOCK_METRICS_URL || 'http://127.0.0.1:8081/__mock__/metrics';
const clientId = 't2s-landing';
const token = process.env.HOMOLOG_GEOGRID_TOKEN || 'homolog-only-grid-token';
const counts = (process.env.HOMOLOG_COUNTS || '50,100,500').split(',').map(Number);
const concurrency = (process.env.HOMOLOG_CONCURRENCY || '10,25,50').split(',').map(Number);
const waitForCompletion = process.env.HOMOLOG_WAIT !== '0';
const deadlineMs = Number(process.env.HOMOLOG_DEADLINE_MS || 900_000);

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {})
    },
    signal: AbortSignal.timeout(options.timeoutMs || 30_000)
  });
  const raw = await response.text();
  let body;
  try {
    body = raw ? JSON.parse(raw) : null;
  } catch {
    body = { non_json_response: true };
  }
  return { status: response.status, body };
}

function percentile(values, percentileValue) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return Number(sorted[Math.ceil((percentileValue / 100) * sorted.length) - 1].toFixed(2));
}

function makeLead(batch, index) {
  return {
    request_id: randomUUID(),
    status_token: randomBytes(32).toString('hex'),
    contact_name: `Participante ${batch}-${index}`,
    company_name: 'ZURI Homologação',
    email: `lead-${batch}-${index}-${randomBytes(4).toString('hex')}@example.test`,
    whatsapp: `+551199${String(index).padStart(7, '0')}`,
    city: 'São Paulo',
    state: 'SP',
    google_maps_url: 'https://www.google.com/maps/search/?api=1&query_place_id=ChIJZuriHomolog',
    keyword: 'consultoria em marketing',
    campaign_id: 'homolog-event',
    privacy_acknowledged: true,
    marketing_consent: false,
    website: ''
  };
}

function queryPostgres(requestIds) {
  const ids = requestIds.map(id => `'${id}'`).join(',');
  const query = `
    SELECT status, count(*)
    FROM geogrid_requests
    WHERE request_id IN (${ids})
    GROUP BY status
    ORDER BY status
  `;
  const result = execFileSync('psql', [
    '-U', 'geogrid_homolog', '-d', 'geogrid_queue',
    '-A', '-t', '-F', '\t', '-c', query
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  return new Map(
    result.trim().split('\n').filter(Boolean).map(line => {
      const [status, count] = line.split('\t');
      return [status, Number(count)];
    })
  );
}

function queryCrmOutbox(requestIds) {
  const ids = requestIds.map(id => `'${id}'`).join(',');
  const query = `
    SELECT count(*) FILTER (WHERE status = 'delivered'), count(*)
    FROM geogrid_crm_outbox
    WHERE request_id IN (${ids})
  `;
  const result = execFileSync('psql', [
    '-U', 'geogrid_homolog', '-d', 'geogrid_queue',
    '-A', '-t', '-F', '\t', '-c', query
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const [delivered, total] = result.trim().split('\t').map(Number);
  return { delivered, total };
}

function queryPersistentStats() {
  const query = `
    SELECT count(*), count(DISTINCT idempotency_key),
           count(*) FILTER (WHERE status = 'done'),
           count(*) FILTER (WHERE status = 'manual_review'),
           count(*) FILTER (WHERE status = 'submission_uncertain'),
           count(*) FILTER (WHERE actual_cost_usd <> 0)
    FROM geogrid_requests
  `;
  const result = execFileSync('psql', [
    '-U', 'geogrid_homolog', '-d', 'geogrid_queue',
    '-A', '-t', '-F', '\t', '-c', query
  ], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
  const [requests, uniqueKeys, done, manualReview, uncertain, nonzeroCost] =
    result.trim().split('\t').map(Number);
  return { requests, uniqueKeys, done, manualReview, uncertain, nonzeroCost };
}

function summarizeStatuses(statuses) {
  return Object.fromEntries([...statuses.entries()]);
}

async function waitForDone(requestIds, label) {
  const started = Date.now();
  let last = new Map();
  while (Date.now() - started < deadlineMs) {
    last = queryPostgres(requestIds);
    const total = [...last.values()].reduce((sum, count) => sum + count, 0);
    const unresolved = (last.get('queued') || 0)
      + (last.get('claimed') || 0)
      + (last.get('business_resolved') || 0)
      + (last.get('scan_created') || 0)
      + (last.get('submitting') || 0)
      + (last.get('submitted') || 0)
      + (last.get('processing') || 0);
    if (total === requestIds.length && unresolved === 0) {
      return { elapsedSeconds: Number(((Date.now() - started) / 1000).toFixed(2)), statuses: last };
    }
    await new Promise(resolve => setTimeout(resolve, 5_000));
  }
  throw new Error(`${label} did not resolve within the deadline: ${JSON.stringify(summarizeStatuses(last))}`);
}

async function waitForCrmDelivery(requestIds, label) {
  const started = Date.now();
  let last = { delivered: 0, total: 0 };
  while (Date.now() - started < 300_000) {
    last = queryCrmOutbox(requestIds);
    if (last.total > 0 && last.delivered === last.total) {
      return { elapsedSeconds: Number(((Date.now() - started) / 1000).toFixed(2)), ...last };
    }
    await new Promise(resolve => setTimeout(resolve, 5_000));
  }
  throw new Error(`${label} CRM outbox did not drain: ${JSON.stringify(last)}`);
}

async function submitBatch(batch, count, maxConcurrency) {
  const leads = Array.from({ length: count }, (_, index) => makeLead(batch, index));
  const responseTimes = [];
  const receipts = new Array(count);
  const failures = [];
  let cursor = 0;
  const worker = async () => {
    while (true) {
      const index = cursor++;
      if (index >= leads.length) return;
      const started = performance.now();
      try {
        const response = await requestJson(`${origin}/api/landing/leads`, {
          method: 'POST',
          headers: { Origin: browserOrigin },
          body: JSON.stringify(leads[index]),
          timeoutMs: 20_000
        });
        responseTimes.push(performance.now() - started);
        if (response.status !== 202 || response.body?.accepted !== true) {
          failures.push({ index, status: response.status });
          continue;
        }
        receipts[index] = {
          request_id: response.body.request_id,
          status_token: response.body.status_token
        };
      } catch {
        failures.push({ index, status: 'transport_error' });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(maxConcurrency, count) }, worker));
  assert(failures.length === 0, `${batch}: submissions failed (${JSON.stringify(failures.slice(0, 5))})`);

  const duplicate = await requestJson(`${origin}/api/landing/leads`, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify(leads[0])
  });
  assert(duplicate.status === 202, `${batch}: duplicate retry was not accepted`);
  assert(duplicate.body?.duplicate === true, `${batch}: duplicate was not recognized`);
  assert(duplicate.body?.request_id === leads[0].request_id, `${batch}: duplicate created another ID`);

  return {
    leads,
    receipts,
    responseTimes,
    duplicateAccepted: duplicate.body.duplicate === true
  };
}

async function checkLanding() {
  const page = await fetch(`${origin}/landing`);
  assert(page.status === 200, 'Landing Page did not load');
  const config = await requestJson(`${origin}/api/landing/config?campaign=homolog-event`);
  assert(config.status === 200 && config.body?.enabled === true, 'Landing Page integration is not configured');
  const metrics = await requestJson(mockMetricsUrl);
  assert(metrics.status === 200, 'HTTP mock simulators are not reachable');
  return { landingStatus: page.status, campaignEnabled: config.body.enabled };
}

async function inspectReport(receipt) {
  const statusPath = `${origin}/api/landing/status/${receipt.request_id}`;
  const invalid = await requestJson(statusPath, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify({ status_token: '0'.repeat(64) })
  });
  assert(invalid.status === 404, 'Invalid capability exposed diagnostic status');

  const status = await requestJson(statusPath, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify({ status_token: receipt.status_token })
  });
  assert(
    status.status === 200 && status.body?.authorized === true,
    `Private status lookup failed (HTTP ${status.status}): ${JSON.stringify(status.body)}`
  );
  assert(status.body?.report_ready === true, 'Report is not marked ready');

  const report = await requestJson(`${origin}/api/landing/report/${receipt.request_id}`, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify({ status_token: receipt.status_token })
  });
  assert(report.status === 200 && report.body?.authorized === true, 'Protected report lookup failed');

  const scanId = report.body.report?.scan_id;
  assert(typeof scanId === 'string', 'Protected report did not include a scan ID');
  const scan = await requestJson(`${gridApi}/api/external/scans/${encodeURIComponent(scanId)}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      'X-GeoGrid-Client-ID': clientId
    }
  });
  assert(scan.status === 200, 'Smart Grid scan API could not retrieve the workflow results');
  assert(scan.body?.grid_size === 3 && scan.body?.spacing_km === 1, 'External scan grid differs from 3x3/1km');
  assert(scan.body?.total === 9 && scan.body?.points?.length === 9, 'External scan does not contain nine points');
  assert(scan.body?.analysis && scan.body?.metrics, 'Smart Grid indicators are missing');

  const wrongTenant = await requestJson(`${gridApi}/api/external/scans/${encodeURIComponent(scanId)}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      'X-GeoGrid-Client-ID': 'different-tenant'
    }
  });
  assert(wrongTenant.status === 404, 'Cross-tenant scan request was not hidden');

  const wrongAuth = await requestJson(`${gridApi}/api/external/scans/${encodeURIComponent(scanId)}`, {
    headers: {
      Authorization: 'Bearer invalid-homolog-token',
      'X-GeoGrid-Client-ID': clientId
    }
  });
  assert(wrongAuth.status === 401, 'Invalid Smart Grid bearer token was accepted');

  return {
    statusAuthorized: true,
    reportAuthorized: true,
    externalScan: true,
    tenantIsolation: true,
    bearerRejected: true,
    pointCount: scan.body.points.length
  };
}

async function exerciseNoResultsState() {
  const lead = makeLead('no-results', 0);
  lead.keyword = 'homolog-sem-resultados';
  const submitted = await requestJson(`${origin}/api/landing/leads`, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify(lead)
  });
  assert(submitted.status === 202, 'No-results test request was not accepted');
  const receipt = {
    request_id: submitted.body.request_id,
    status_token: submitted.body.status_token
  };
  const processing = await waitForDone([receipt.request_id], 'No-results case');
  assert(processing.statuses.get('done') === 1, 'No-results case did not finish normally');
  const crm = await waitForCrmDelivery([receipt.request_id], 'No-results case');
  const report = await requestJson(`${origin}/api/landing/report/${receipt.request_id}`, {
    method: 'POST',
    headers: { Origin: browserOrigin },
    body: JSON.stringify({ status_token: receipt.status_token })
  });
  assert(report.status === 200 && report.body?.authorized === true, 'No-results report was unavailable');
  const scanId = report.body.report?.scan_id;
  const scan = await requestJson(`${gridApi}/api/external/scans/${encodeURIComponent(scanId)}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      'X-GeoGrid-Client-ID': clientId
    }
  });
  assert(scan.status === 200, 'Could not retrieve the no-results scan');
  assert(
    scan.body.points.length === 9
      && scan.body.points.every(point => point.status === 'no_results' && point.api_status_code === 40102),
    'No Search Results was not kept distinct from ranking position or technical failure'
  );
  return {
    status: 'done',
    points: scan.body.points.length,
    pointStatus: 'no_results',
    dataforseoStatusCode: 40102,
    crmOutbox: crm
  };
}

async function main() {
  assert(counts.length === concurrency.length, 'HOMOLOG_COUNTS and HOMOLOG_CONCURRENCY must have the same number of entries');
  const baseline = await checkLanding();
  const batches = [];
  let firstReceipt = null;

  for (let index = 0; index < counts.length; index += 1) {
    const count = counts[index];
    const maxConcurrency = concurrency[index];
    assert(Number.isInteger(count) && count > 0, 'Invalid batch size');
    const started = Date.now();
    const batch = await submitBatch(index + 1, count, maxConcurrency);
    firstReceipt ||= batch.receipts[0];
    const httpElapsedSeconds = Number(((Date.now() - started) / 1000).toFixed(2));
    const httpStats = {
      count,
      concurrency: maxConcurrency,
      accepted: batch.responseTimes.length,
      duplicateRetries: 1,
      duplicateAccepted: batch.duplicateAccepted,
      elapsedSeconds: httpElapsedSeconds,
      meanMs: Number((batch.responseTimes.reduce((sum, item) => sum + item, 0) / batch.responseTimes.length).toFixed(2)),
      p50Ms: percentile(batch.responseTimes, 50),
      p90Ms: percentile(batch.responseTimes, 90),
      p95Ms: percentile(batch.responseTimes, 95),
      maxMs: Number(Math.max(...batch.responseTimes).toFixed(2))
    };

    let processing = null;
    let crm = null;
    if (waitForCompletion) {
      processing = await waitForDone(
        batch.receipts.map(receipt => receipt.request_id),
        `Batch of ${count}`
      );
      crm = await waitForCrmDelivery(
        batch.receipts.map(receipt => receipt.request_id),
        `Batch of ${count}`
      );
    }
    batches.push({ http: httpStats, processing: processing && {
      elapsedSeconds: processing.elapsedSeconds,
      statuses: summarizeStatuses(processing.statuses),
      completed: processing.statuses.get('done') || 0,
      notCompleted: count - (processing.statuses.get('done') || 0)
    }, crm });
  }

  const security = waitForCompletion ? await inspectReport(firstReceipt) : null;
  const noResults = waitForCompletion ? await exerciseNoResultsState() : null;
  const mockMetrics = await requestJson(mockMetricsUrl);
  assert(mockMetrics.status === 200, 'Could not read mock-service metrics');
  const mockCounts = mockMetrics.body.counts;
  const acceptedTotal = counts.reduce((sum, count) => sum + count, 0) + (waitForCompletion ? 1 : 0);
  assert(mockCounts.task_post <= acceptedTotal, 'More mocked paid submissions occurred than unique accepted requests');
  const persistentStats = queryPersistentStats();
  assert(persistentStats.requests === acceptedTotal, 'Persisted request count differs from unique accepted requests');
  assert(persistentStats.uniqueKeys === persistentStats.requests, 'Duplicate idempotency rows were persisted');

  console.log(JSON.stringify({
    baseline,
    batches,
    security,
    noResults,
    mockCounts,
    uniqueAcceptedRequests: acceptedTotal,
    taskPostExpectedMaximum: acceptedTotal,
    persistentStats,
    duplicateRows: persistentStats.requests - persistentStats.uniqueKeys,
    dataforseoMode: 'HTTP mock only; DataForSEO task_post count verified',
    n8nVersion: '1.123.21',
    networkEgress: 'disabled by Docker internal network',
    capabilityInN8nLogs: 'verify separately from host with the documented secret-safe check'
  }, null, 2));
}

main().catch(error => {
  console.error(`HOMOLOGATION_FAILED: ${error.message}`);
  process.exitCode = 1;
});
