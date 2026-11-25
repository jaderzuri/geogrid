#!/bin/sh
set -eu

IMPORT_DIR=/tmp/geogrid-homolog-workflows
mkdir -p "$IMPORT_DIR"

if [ -f /home/node/.n8n/.geogrid-homolog-workflows-imported ]; then
    echo "Homologation workflows already imported; leaving persistent state unchanged."
    exit 0
fi

node <<'NODE'
const fs = require('node:fs');
const path = require('node:path');
const source = '/source';
const destination = '/tmp/geogrid-homolog-workflows';
const ids = {
  postgres: 'ee000000-0000-4000-8000-000000000001',
  dataforseo: 'ee000000-0000-4000-8000-000000000002',
  places: 'ee000000-0000-4000-8000-000000000003',
  smartgrid: 'ee000000-0000-4000-8000-000000000004',
  crm: 'ee000000-0000-4000-8000-000000000005'
};
const credentials = [
  {
    id: ids.postgres,
    name: 'GeoGrid Queue PostgreSQL (homolog only)',
    type: 'postgres',
    data: {
      host: 'postgres',
      database: 'geogrid_queue',
      user: 'geogrid_homolog',
      password: process.env.HOMOLOG_POSTGRES_PASSWORD,
      port: 5432,
      ssl: 'disable',
      sshTunnel: false,
      connectionTimeout: 30000
    }
  },
  {
    id: ids.dataforseo,
    name: 'DataForSEO Basic Auth (local simulator only)',
    type: 'httpBasicAuth',
    data: {
      user: process.env.MOCK_DATAFORSEO_USER,
      password: process.env.MOCK_DATAFORSEO_PASSWORD
    }
  },
  {
    id: ids.places,
    name: 'Google Places API Key (local simulator only)',
    type: 'httpHeaderAuth',
    data: { name: 'X-Goog-Api-Key', value: process.env.MOCK_PLACES_API_KEY }
  },
  {
    id: ids.smartgrid,
    name: 'Smart Grid Bearer (local container only)',
    type: 'httpHeaderAuth',
    data: { name: 'Authorization', value: `Bearer ${process.env.HOMOLOG_GEOGRID_TOKEN}` }
  },
  {
    id: ids.crm,
    name: 'CRM Adapter Bearer (local simulator only)',
    type: 'httpHeaderAuth',
    data: { name: 'Authorization', value: `Bearer ${process.env.MOCK_CRM_TOKEN}` }
  }
];
fs.writeFileSync('/tmp/geogrid-homolog-credentials.json', JSON.stringify(credentials));

for (const name of fs.readdirSync(source).filter(name => /^0[1-6]-.*\.json$/.test(name))) {
  const workflow = JSON.parse(fs.readFileSync(path.join(source, name), 'utf8'));
  workflow.active = true;
  for (const node of workflow.nodes) {
    if (node.type === 'n8n-nodes-base.scheduleTrigger') {
      for (const interval of node.parameters.rule?.interval || []) {
        interval.field = 'seconds';
        interval.secondsInterval = 1;
        delete interval.minutesInterval;
      }
    }
    if (node.credentials?.postgres) node.credentials.postgres.id = ids.postgres;
    if (node.credentials?.httpBasicAuth) node.credentials.httpBasicAuth.id = ids.dataforseo;
    if (node.credentials?.httpHeaderAuth) {
      const name = node.credentials.httpHeaderAuth.name || '';
      node.credentials.httpHeaderAuth.id = name.startsWith('Google Places')
        ? ids.places
        : name.startsWith('CRM Adapter')
          ? ids.crm
          : ids.smartgrid;
    }
  }
  fs.writeFileSync(path.join(destination, name), JSON.stringify(workflow));
}
NODE

n8n import:credentials --input=/tmp/geogrid-homolog-credentials.json
n8n import:workflow --separate --input="$IMPORT_DIR"
n8n update:workflow --all --active=true
n8n list:workflow
touch /home/node/.n8n/.geogrid-homolog-workflows-imported
