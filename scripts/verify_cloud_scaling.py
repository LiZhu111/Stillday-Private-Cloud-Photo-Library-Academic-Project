"""Bounded real-image queue/scaling experiment. Never changes worker capacity."""
import base64
import io
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import boto3
import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://n00000000-a3.cab432.com'
CLUSTER = 'n00000000-a2-cluster'
SERVICE = 'n00000000-a3-worker'
QUEUE = 'https://sqs.ap-southeast-2.amazonaws.com/000000000000/n00000000-a2-classification'

def now():
    return datetime.now(timezone.utc).isoformat()

def main():
    token = (ROOT / '.data/cognito-access-token.txt').read_text().strip()
    part = token.split('.')[1]
    expiry = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))['exp']
    if expiry < time.time() + 1200:
        raise SystemExit('Refresh local Cognito login first (at least 20 minutes remaining required).')
    session = boto3.Session(profile_name='CAB432-STUDENT-000000000000', region_name='ap-southeast-2')
    ecs, sqs, scaling = (session.client(x) for x in ('ecs', 'sqs', 'application-autoscaling'))
    before = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])['services'][0]
    if before['desiredCount'] != 0 or before['runningCount'] != 0:
        raise SystemExit('Wait for workers to become idle before this cold-start experiment.')
    # Compressible synthetic 24MP photograph-like texture; real resize/encode work, no artificial sleep.
    noise = Image.effect_noise((1000, 667), 35).convert('RGB').resize((6000, 4000), Image.Resampling.BICUBIC)
    buf = io.BytesIO()
    noise.save(buf, 'JPEG', quality=85)
    payload = buf.getvalue()
    evidence = {'startedAt': now(), 'plannedImages': 48, 'imagePixels': [6000, 4000],
                'bytesPerImage': len(payload), 'uploaderConcurrency': 4,
                'uploads': [], 'samples': [], 'errors': [],
                'limitations': 'Synthetic batch workload, not 50 concurrent users. No manual capacity changes.'}
    out = ROOT / 'evidence/cloud-batch-scaling.json'
    def save():
        out.write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    with httpx.Client(base_url=BASE, headers={'Authorization': 'Bearer ' + token}, timeout=90) as api:
        created = api.post('/a3/families', json={'name': 'A3 scaling evidence ' + now()})
        if created.status_code != 201:
            raise SystemExit('Create family HTTP ' + str(created.status_code))
        family = created.json()['familyId']
        evidence['familyId'] = family
        path = '/a3/families/' + family
        save()
        def upload(index):
            started = time.perf_counter()
            receipt = api.post(path + '/uploads', json={'name': f'scaling-{index:03}.jpg', 'mime': 'image/jpeg'})
            if receipt.status_code != 201:
                return {'index': index, 'error': 'prepare HTTP ' + str(receipt.status_code)}
            data = receipt.json()
            photo = data['photo']['photoId']
            # Independent client: do not forward bearer credentials to S3.
            with httpx.Client(timeout=120) as origin:
                sent = origin.post(data['upload']['url'], data=data['upload']['fields'], files={'file': (f'scaling-{index:03}.jpg', payload, 'image/jpeg')})
            if sent.status_code not in (200, 204):
                return {'index': index, 'photoId': photo, 'error': 'S3 HTTP ' + str(sent.status_code)}
            finish_start = time.perf_counter()
            done = api.post(path + '/photos/' + photo + '/complete')
            return {'index': index, 'photoId': photo, 'httpStatus': done.status_code,
                    'completeSeconds': round(time.perf_counter() - finish_start, 4),
                    'totalUploadSeconds': round(time.perf_counter() - started, 4), 'time': now()}
        def sample():
            service = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])['services'][0]
            queue = sqs.get_queue_attributes(QueueUrl=QUEUE, AttributeNames=['ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible', 'ApproximateNumberOfMessagesDelayed'])['Attributes']
            result = api.get(path + '/photos')
            item = {'time': now(), 'desired': service['desiredCount'], 'running': service['runningCount'], 'pending': service['pendingCount'], 'queue': queue, 'photosHttpStatus': result.status_code}
            if result.status_code == 200:
                body = result.json()
                photos = body if isinstance(body, list) else body.get('photos', [])
                item['statuses'] = dict(Counter(p.get('status', 'UNKNOWN') for p in photos))
            evidence['samples'].append(item)
            save()
            print(json.dumps(item), flush=True)
            return item
        try:
            sample()
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(upload, i) for i in range(48)]
                for future in as_completed(futures):
                    try:
                        evidence['uploads'].append(future.result())
                    except Exception as exc:
                        # Exception strings may contain a signed URL; record only class name.
                        evidence['errors'].append({'phase': 'upload', 'type': type(exc).__name__})
                    save()
                    if len(evidence['uploads']) % 8 == 0:
                        sample()
            evidence['uploadsFinishedAt'] = now()
            deadline = time.monotonic() + 1000
            while time.monotonic() < deadline:
                try:
                    item = sample()
                    successful = sum(u.get('httpStatus') == 202 for u in evidence['uploads'])
                    ready = item.get('statuses', {}).get('READY', 0)
                    if successful and ready == successful and item['desired'] == 0 and item['running'] == 0:
                        evidence['allAcceptedReadyAndScaledToZero'] = True
                        break
                except Exception as exc:
                    evidence['errors'].append({'phase': 'observe', 'type': type(exc).__name__, 'time': now()})
                    save()
                time.sleep(20)
            result = scaling.describe_scaling_activities(ServiceNamespace='ecs', ResourceId='service/' + CLUSTER + '/' + SERVICE, ScalableDimension='ecs:service:DesiredCount', MaxResults=30)
            evidence['scalingActivities'] = json.loads(json.dumps(result.get('ScalingActivities', []), default=str))
        finally:
            evidence['finishedAt'] = now()
            evidence['maxObservedRunningWorkers'] = max((s['running'] for s in evidence['samples']), default=0)
            evidence['maxObservedDesiredWorkers'] = max((s['desired'] for s in evidence['samples']), default=0)
            save()
    print(json.dumps({'evidenceFile': str(out), 'maxRunning': evidence['maxObservedRunningWorkers'], 'returnedToZero': evidence.get('allAcceptedReadyAndScaledToZero', False)}), flush=True)

if __name__ == '__main__':
    main()
