import { randomBytes, randomUUID } from 'node:crypto';

const requests = Array.from({ length: 25 }, (_, index) => {
  const body = {
    request_id: randomUUID(),
    status_token: randomBytes(32).toString('hex'),
    contact_name: `Synthetic ${index}`,
    company_name: 'ZURI Homologação',
    email: `p01-restart-${index}-${randomBytes(4).toString('hex')}@example.test`,
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
  return fetch('http://edge:8080/api/landing/leads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: 'http://localhost:8080' },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(60000)
  }).then(response => response.status);
});

const statuses = await Promise.all(requests);
if (statuses.some(status => status !== 202)) {
  throw new Error(`Synthetic request failed: ${statuses.filter(status => status !== 202).join(',')}`);
}
console.log(`Accepted synthetic requests: ${statuses.length}`);
