from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
 c=p.chromium.launch_persistent_context(str(root/'.data/pricing-browser'),channel='msedge',headless=True,viewport={'width':1440,'height':1000})
 page=c.pages[0];page.goto('https://calculator.aws/#/estimate',wait_until='domcontentloaded');page.wait_for_timeout(5000)
 print('URL',page.url);print(page.locator('body').inner_text()[:12000])
 print('LINKS',page.get_by_role('link').evaluate_all('(es)=>es.map(e=>({text:e.innerText,href:e.getAttribute("href")}))')[:25])
 c.close()
