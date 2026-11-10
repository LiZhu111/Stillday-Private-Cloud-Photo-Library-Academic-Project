from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
 c=p.chromium.launch_persistent_context(str(root/'.data/pricing-browser'),channel='msedge',headless=True,viewport={'width':1440,'height':1000})
 page=c.pages[0];page.goto('https://calculator.aws/',wait_until='domcontentloaded');page.get_by_role('button',name='Create estimate',exact=True).click()
 for cpu,memory,label in [('0.25','0.5','Stillday API - 720 hours'),('1','2','Stillday worker conservative baseline - 720 hours')]:
  page.get_by_placeholder('Search for a service').fill('Fargate');page.get_by_role('button',name='Configure AWS Fargate',exact=True).click()
  page.get_by_placeholder('Enter a description for your estimate').fill(label)
  page.locator('button').filter(has_text='US East (Ohio)').click();page.get_by_text('Asia Pacific (Sydney)',exact=True).click()
  page.locator('button').filter(has_text='per day').click();page.get_by_text('per month',exact=True).click()
  page.get_by_label('Number of tasks or pods Value',exact=True).fill('1')
  page.get_by_label('Average duration Value',exact=True).fill('43200')
  if cpu!='1':
   page.locator('button').filter(has_text=__import__('re').compile('^1$')).click();page.get_by_text(cpu,exact=True).click()
  page.get_by_label('Amount of memory allocated Value',exact=True).fill(memory)
  page.get_by_role('button',name='Save and add service',exact=True).first.click()
  page.get_by_placeholder('Search for a service').wait_for()
 print('Compute entries saved.');print(page.url)
 # Persist the estimate through the application summary before closing.
 page.get_by_role('link',name='My Estimate',exact=True).first.click();page.wait_for_timeout(1500)
 print(page.locator('body').inner_text()[:9000]);(root/'.data/pricing-url.txt').write_text(page.url)
 c.close()
