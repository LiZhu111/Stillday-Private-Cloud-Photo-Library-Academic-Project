"""One image per worker; durable leases, visibility renewal and graceful drain."""
import argparse
import json
import logging
import os
import signal
import threading
import time
import urllib.request
import uuid
from .analysis import analyse
from .intelligence import describe_and_index
from .config import Settings
from .service import Library, now
from .store import Conflict

LOG = logging.getLogger('photo-worker')


def protect_task(enabled):
    endpoint = os.getenv('ECS_AGENT_URI')
    if endpoint:
        data = json.dumps({'ProtectionEnabled': enabled, 'ExpiresInMinutes': 5}).encode()
        request = urllib.request.Request(endpoint + '/task-protection/v1/state', data=data,
                                          headers={'Content-Type': 'application/json'}, method='PUT')
        with urllib.request.urlopen(request, timeout=5) as result:
            response = json.loads(result.read())
        if response.get('error') or response.get('failures'):
            raise RuntimeError('ECS task protection failed')


def owned_update(library, family, photo_id, token, changes):
    for _ in range(5):
        item = library.store.get(library.pk(family), 'PHOTO#' + photo_id)
        if item['status'] != 'PROCESSING' or item.get('leaseToken') != token:
            return False
        updates = dict(changes)
        if updates.get('status') == 'READY' and item.get('dateSource') == 'user':
            updates.pop('capturedDate', None)
            updates.pop('dateSource', None)
        item.update(updates)
        try:
            library.save_photo(family, item)
            return True
        except Conflict:
            continue
    raise Conflict('Processing state changed repeatedly')


def process(library, message):
    body = message['body']
    if body.get('schemaVersion') != 1:
        raise ValueError('Unsupported job schema')
    family, photo_id = body['familyId'], body['photoId']
    from .service import identifier
    identifier(photo_id)
    item = library.store.get(library.pk(family), 'PHOTO#' + photo_id)
    if not item:
        raise ValueError('Photo record is missing')
    reanalysis_id = body.get('reanalysisId')
    if item['status'] == 'TRASHED':
        return True
    if item['status'] == 'READY' and (not reanalysis_id or
            item.get('reanalysisId') != reanalysis_id or
            item.get('completedReanalysisId') == reanalysis_id):
        return True
    if item['status'] == 'UPLOADING':
        raise ValueError('Upload has not been completed')
    if item['status'] == 'PROCESSING' and float(item.get('leaseUntil', 0)) > time.time():
        return False
    token = uuid.uuid4().hex
    item.update(status='PROCESSING', leaseToken=token, leaseUntil=time.time()+library.settings.visibility,
                attempts=message['attempts'], processingStartedAt=now())
    item = library.save_photo(family, item)
    stop = threading.Event()
    lost = threading.Event()

    def heartbeat():
        while not stop.wait(30):
            try:
                library.queue.extend(message['receipt'], library.settings.visibility)
                protect_task(True)
                if not owned_update(library, family, photo_id, token,
                                    {'leaseUntil': time.time()+library.settings.visibility}):
                    lost.set()
                    return
            except Exception:
                lost.set()
                LOG.exception('Lease renewal failed')
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    started = time.monotonic()
    try:
        protect_task(True)
        thread.start()
        raw = library.storage.read(item['key'], item.get('versionId'), library.settings.max_upload)
        metadata, variants = analyse(raw)
        outputs = []
        for size, data, width, height in variants:
            key = f'derived/{family}/{photo_id}/{size}.jpg'
            library.storage.put(key, data, 'image/jpeg')
            outputs.append({'size': size, 'key': key, 'width': width, 'height': height, 'bytes': len(data)})
        metadata.update(describe_and_index(next(data for size, data, _, _ in variants if size == '1280'),
                                           family, photo_id))
        stop.set()
        thread.join()
        if lost.is_set():
            raise RuntimeError('Lease lost; do not commit this attempt')
        # Preserve user-edited metadata made while processing was running.
        current = library.store.get(library.pk(family), 'PHOTO#' + photo_id)
        if current.get('dateSource') == 'user':
            metadata.pop('capturedDate', None)
        else:
            metadata['dateSource'] = 'exif' if metadata['capturedDate'] else 'unknown'
        completion = dict(metadata, status='READY', variants=outputs, completedAt=now(), leaseUntil=0, error=None)
        if reanalysis_id:
            completion.update(reanalysisId=reanalysis_id, completedReanalysisId=reanalysis_id, analysisError=None)
        completed = owned_update(library, family, photo_id, token, completion)
        LOG.info(json.dumps({'event': 'image_processed', 'photoId': photo_id,
                             'seconds': round(time.monotonic()-started, 3), 'committed': completed}))
        if not completed:
            current = library.store.get(library.pk(family), 'PHOTO#' + photo_id)
            return current['status'] in ('READY', 'TRASHED')
        return True
    except Exception as error:
        if reanalysis_id and item.get('reanalysisId') == reanalysis_id:
            owned_update(library, family, photo_id, token,
                         {'status': 'READY', 'leaseUntil': 0, 'analysisError': type(error).__name__})
        else:
            owned_update(library, family, photo_id, token,
                         {'status': 'FAILED', 'leaseUntil': 0, 'error': type(error).__name__})
        raise
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=70)
        try:
            protect_task(False)
        except Exception:
            LOG.exception('Could not clear task protection; it will expire')


def run_once(library):
    message = library.queue.receive(library.settings.visibility, library.settings.max_attempts)
    if not message:
        return False
    try:
        if message['body'].get('jobType') == 'semantic-search':
            from .search_jobs import process as process_search
            completed = process_search(library, message)
        else:
            completed = process(library, message)
        if completed:
            library.queue.ack(message['receipt'])
    except Exception:
        LOG.exception('Image task failed; message remains available for retry/DLQ')
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Poll at most one job')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    library = Library(Settings())
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    while not stopping.is_set():
        try:
            received = run_once(library)
        except Exception:
            LOG.exception('Queue poll failed')
            received = False
        if args.once:
            break
        if not received:
            stopping.wait(2)


if __name__ == '__main__':
    main()

