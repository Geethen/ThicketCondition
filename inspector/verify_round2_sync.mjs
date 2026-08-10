// Focused Round 2/disagreement/Google-Sheets-outbox verification.
import { chromium } from 'playwright';
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const received = [];
const server = http.createServer(async (req,res) => {
  if (req.url.startsWith('/sync') && req.method === 'POST') {
    let body=''; for await (const chunk of req) body += chunk;
    received.push(JSON.parse(body));
    res.setHeader('content-type','application/json');res.end('{"ok":true}');return;
  }
  try {
    const pathname=decodeURIComponent(req.url.split('?')[0]);
    const target=path.join(DIR,pathname==='/'?'index.html':pathname);
    if(path.relative(DIR,target).startsWith('..'))throw new Error('outside');
    const body=await readFile(target);
    res.setHeader('content-type',path.extname(target)==='.json'?'application/json':'text/html');res.end(body);
  } catch {res.statusCode=404;res.end('nf');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
const sync=`http://127.0.0.1:${port}/sync`;
const url=`http://127.0.0.1:${port}/index.html?mode=coordinator&syncEndpoint=${encodeURIComponent(sync)}`;

const browser=await chromium.launch();
const context=await browser.newContext();
const page=await context.newPage();
await page.goto(url,{waitUntil:'load'});
await page.fill('#labelerName','SYNC_TEST');await page.click('#startBtn');
await page.uncheck('#autoAdvance');
const first=await page.evaluate(()=>window.REQUIRED_POINTS[0]);
await page.click('.lblbtn[data-lbl="moderate"]');
await page.waitForFunction(()=>document.querySelector('#syncStatus').textContent.includes('sent'));

const upsert=received.find(x=>x.action==='upsert');
const upsertOK=!!upsert && upsert.dataset===await page.evaluate(()=>DATASET_ID)
  && upsert.labeler==='SYNC_TEST' && upsert.point.id===first.id
  && upsert.point.source==='new' && upsert.point.required===true
  && upsert.record.label==='moderate';

await page.evaluate(id=>{gotoId(id);clearLabel();},first.id);
await page.waitForFunction(()=>document.querySelector('#syncStatus').textContent.includes('sent'));
await page.waitForTimeout(100);
const clear=received.find(x=>x.action==='clear'&&x.point.id===first.id);
const queueEmpty=await page.evaluate(()=>JSON.parse(localStorage.getItem(
  Object.keys(localStorage).find(k=>k.startsWith('thicket-inspector-sheet-sync-queue-')))||'[]').length===0);

await page.selectOption('#pointFilter','disagreement');await page.click('#nextUnlabeled');
const disagreementOK=await page.evaluate(()=>{
  const id=document.querySelector('#curId').textContent;
  return Object.hasOwn(window.DISAGREEMENT_MANIFEST,id)
    && document.querySelectorAll('#priorLabels .prior-label').length>=2
    && document.querySelector('#mSampleSource').textContent.includes('disagreement exploration');
});

console.log('sheet upsert / clear / outbox drained:',upsertOK,!!clear,queueEmpty);
console.log('disagreement queue and prior-label context:',disagreementOK);
await browser.close();server.close();
const ok=upsertOK&&!!clear&&queueEmpty&&disagreementOK;
console.log(ok?'\n✅ ROUND 2 + SYNC PASSED':'\n❌ ROUND 2 + SYNC FAILED');
process.exit(ok?0:1);
