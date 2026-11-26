// Test-only faults are injected into a copied workflow imported in isolated n8n.
module.exports = workflow => {
  workflow.settings.saveDataErrorExecution = 'all';
  const find = name => {
    const node = workflow.nodes.find(item => item.name === name);
    if (!node) throw new Error(`Missing processor node: ${name}`);
    return node;
  };
  const scenario = "$('Atomically Claim One Request').item.json.google_maps_url";
  const is = value => `(${scenario} || '').includes('fault=${value}&')`;

  for (const [name, fault] of [
    ['Build Nine Standard Tasks', 'grid'],
    ['Associate and Classify task_post Response', 'associate'],
    ['Build Failed Submission Point Callbacks', 'callback-build'],
  ]) {
    const node = find(name);
    node.parameters.jsCode = `if (${is(fault)}) throw new Error('Synthetic contract failure');\n` +
      node.parameters.jsCode;
  }
  for (const [name, fault] of [
    ['Persist Grid Before Paid Submission', 'grid-persist'],
    ['Authorize Paid task_post Exactly Once', 'authorize'],
    ['Persist DataForSEO IDs and Cost', 'ids-persist'],
    ['Log Persisted Submission Outcomes', 'outcomes'],
    ['Record Failed Point Callback Delivery', 'callback-persist'],
  ]) {
    const node = find(name);
    node.parameters.query = `={{ ${is(fault)} ? 'SELECT 1 / 0' : ${JSON.stringify(node.parameters.query)} }}`;
  }
  for (const [name, fault] of [
    ['Register Business in Smart Grid', 'register'],
    ['Create Idempotent Smart Grid Scan', 'scan'],
    ['Paid DataForSEO task_post - No Automatic Retry', 'post'],
  ]) {
    const node = find(name);
    node.parameters.url = `={{ ${is(fault)} ? 'http://mock-services:3/unreachable' : ${JSON.stringify(node.parameters.url)} }}`;
  }
  const places = find('Resolve Exact Place Details');
  const original = places.parameters.url.slice(3, -3).trim();
  places.parameters.url = `={{ ${is('timeout')} ? 'http://mock-services:8081/__mock__/failure/timeout' : ${is('rate')} ? 'http://mock-services:8081/__mock__/failure/429' : ${is('auth')} ? 'http://mock-services:8081/__mock__/failure/401' : ${is('places')} ? 'http://mock-services:8081/__mock__/failure/503' : (${original}) }}`;
  places.parameters.options.timeout = 500;
  const post = find('Paid DataForSEO task_post - No Automatic Retry');
  const postOriginal = JSON.stringify('https://api.dataforseo.com/v3/serp/google/maps/task_post');
  post.parameters.url = `={{ ${is('post')} ? 'http://mock-services:3/unreachable' : (${is('callback-build')} || ${is('callback-report')} || ${is('callback-persist')}) ? 'http://mock-services:8081/__mock__/post-rejected' : ${postOriginal} }}`;
  const callback = find('Report Failed Submission Points');
  const callbackOriginal = callback.parameters.url.slice(3, -3).trim();
  callback.parameters.url = `={{ ${is('callback-report')} ? 'http://mock-services:3/unreachable' : (${callbackOriginal}) }}`;
};
