"""Verify authenticated preview and process-cache behaviour without printing secrets."""
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
BASE='https://n00000000-a3.cab432.com'

def main():
    token=(ROOT/'.data/cognito-access-token.txt').read_text().strip()
    part=token.split('.')[1]
    expiry=json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4))).get('exp',0)
    if expiry<time.time()+60:
        raise SystemExit('Token expired or nearly expired. Run python scripts/cognito_login.py locally first.')
    state=json.loads((ROOT/'evidence/cloud-smoke-20261022.json').read_text())
    path='/a3/families/'+state['familyId']
    evidence={'time':datetime.now(timezone.utc).isoformat(),'requests':[],'cacheScope':'API process; browser caching disabled'}
    with httpx.Client(base_url=BASE,headers={'Authorization':'Bearer '+token},timeout=30) as api:
        def photos(label):
            started=time.perf_counter();r=api.get(path+'/photos')
            evidence['requests'].append({'label':label,'status':r.status_code,'xCache':r.headers.get('x-cache'),'cacheControl':r.headers.get('cache-control'),'seconds':round(time.perf_counter()-started,4)})
            if r.status_code!=200:raise RuntimeError('Photo list rejected with HTTP '+str(r.status_code))
            return r
        time.sleep(5.2)
        cold=photos('after TTL')
        warm=photos('repeat')
        assert warm.headers.get('x-cache')=='HIT','No hit observed; retry after the rolling deployment has stabilised'
        assert warm.headers.get('cache-control')=='no-store'
        time.sleep(5.2)
        expired=photos('TTL expired')
        assert expired.headers.get('x-cache')=='MISS'
        photo=api.get(path+'/photos/'+state['photoId'])
        assert photo.status_code==200
        # Preserve the original value while performing a real write to test invalidation.
        changed=api.patch(path+'/photos/'+state['photoId'],json={'favorite':photo.json().get('favorite',False)})
        assert changed.status_code==200
        updated=photos('after metadata write')
        assert updated.headers.get('x-cache')=='MISS'
        result=api.get(path+'/photos/'+state['photoId']+'/download?size=original')
        assert result.status_code==200,'Original preview HTTP '+str(result.status_code)
        # The signed URL stays in memory and receives no bearer token.
        with httpx.Client(timeout=30) as origin:
            image=origin.get(result.json()['url'])
            assert image.status_code==200 and image.content.startswith(bytes.fromhex('ffd8'))
            evidence['originalPreview']={'status':image.status_code,'bytes':len(image.content)}
            denied=origin.get(BASE+path+'/photos')
            evidence['unauthenticatedStatus']=denied.status_code
            assert denied.status_code==401
    output=ROOT/'evidence/cloud-cache-verification.json'
    output.write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence,indent=2))

if __name__=='__main__':main()
