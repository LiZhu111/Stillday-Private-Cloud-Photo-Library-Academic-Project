"""Local interactive helper for the public AWS calculator; no AWS credentials."""
import sys,json
from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
 c=p.chromium.launch_persistent_context(str(root/'.data/pricing-browser'),channel='msedge',headless=True,viewport={'width':1440,'height':1000})
 page=c.pages[0];page.goto('https://calculator.aws/#/estimate',wait_until='domcontentloaded');page.get_by_text('Estimate summary',exact=True).wait_for()
 print('READY',flush=True)
 for line in sys.stdin:
  try:
   command=json.loads(line)
   for a in command.get('actions',[]):
    op=a['op']
    if op=='goto':page.goto(a['url'],wait_until='domcontentloaded');continue
    if op=='wait':page.wait_for_timeout(min(a.get('ms',1000),5000));continue
    if op=='snapshot':print(page.locator('body').aria_snapshot()[:a.get('limit',15000)],flush=True);continue
    if op=='text':print(page.locator('body').inner_text()[:a.get('limit',14000)],flush=True);continue
    if op=='save':
     (root/'evidence'/a['name']).write_text(page.locator('body').inner_text(),encoding='utf-8');continue
    if op=='inputs':
     print(json.dumps(page.locator('input').evaluate_all('(es)=>es.map(e=>({id:e.id,type:e.type,label:e.getAttribute("aria-label"),placeholder:e.placeholder,value:e.value}))')),flush=True);continue
    if 'role' in a:loc=page.get_by_role(a['role'],name=a.get('name'),exact=a.get('exact',True))
    elif 'label' in a:loc=page.get_by_label(a['label'],exact=True)
    elif 'placeholder' in a:loc=page.get_by_placeholder(a['placeholder'],exact=True)
    elif 'text' in a:loc=page.get_by_text(a['text'],exact=a.get('exact',True))
    else:loc=page.locator(a['css'])
    if 'nth' in a:loc=loc.nth(a['nth'])
    if op=='click':loc.click(timeout=10000)
    elif op=='fill':loc.fill(a['value'],timeout=10000)
    elif op=='check':loc.check(timeout=10000)
    elif op=='uncheck':loc.uncheck(timeout=10000)
    elif op=='waitfor':loc.wait_for(timeout=15000)
   print('DONE '+page.url,flush=True)
  except Exception as e:print('ERROR '+str(e)[:2200],flush=True)
 c.close()
