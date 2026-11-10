"""Family-scoped durable semantic searches consumed by the existing queue worker."""
import logging
import threading
import time
import uuid
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectTimeoutError
from .search_context import execution
from .service import identifier
from .store import Conflict

LOG = logging.getLogger('semantic-search')
TOTAL_SECONDS = 600
TERMINAL = {'COMPLETED', 'FAILED'}
LABELS = {'queued': 'Waiting for a search worker',
          'analyzing_request': 'Understanding your search',
          'retrieving_photos': 'Finding matching photos',
          'generating_answer': 'Preparing your search results'}


def _update(library, family, job_id, changes, token=None):
    for _ in range(5):
        item = library.store.get(library.pk(family), 'SEARCH#' + identifier(job_id))
        if not item or item['status'] in TERMINAL:
            return None
        if token is not None and item.get('leaseToken') != token:
            return None
        try:
            return library.store.put(library.pk(family), 'SEARCH#' + job_id,
                                     dict(item, **changes), expected=item['rev'])
        except Conflict:
            continue
    raise Conflict('Search state changed repeatedly')


def submit(library, user, family, prompt, output='chat', request_id=None):
    library.member(user, family)
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise ValueError('Search text must contain 1-2000 characters')
    if output not in {'chat', 'search'}:
        raise ValueError('Invalid search output format')
    job_id = identifier(request_id) if request_id else uuid.uuid4().hex
    existing = library.store.get(library.pk(family), 'SEARCH#' + job_id)
    if existing:
        if existing['userId'] != user:
            raise PermissionError('Search access denied')
        if existing['prompt'] != prompt or existing['output'] != output:
            raise ValueError('This search request ID was already used for another request')
        return get(library, user, family, job_id)
    created = time.time()
    item = dict(jobId=job_id, userId=user, familyId=family, prompt=prompt,
                output=output, status='QUEUED', stage='queued', createdAt=created,
                deadline=created + TOTAL_SECONDS)
    try:
        library.store.put(library.pk(family), 'SEARCH#' + job_id, item)
    except Conflict:
        return submit(library, user, family, prompt, output, job_id)
    try:
        library.queue.send({'schemaVersion': 1, 'jobType': 'semantic-search',
                            'familyId': family, 'jobId': job_id})
    except Exception:
        _update(library, family, job_id, {'status': 'FAILED',
                'error': 'The search could not be queued. Please submit a new search.'})
        LOG.exception('Search enqueue failed job=%s', job_id)
    return get(library, user, family, job_id)


def get(library, user, family, job_id):
    library.member(user, family)
    item = library.store.get(library.pk(family), 'SEARCH#' + identifier(job_id))
    if not item:
        raise LookupError('Search task not found')
    if item['userId'] != user:
        raise PermissionError('Search access denied')
    if item['status'] not in TERMINAL and time.time() >= float(item['deadline']):
        _update(library, family, job_id, {'status': 'FAILED',
                'error': 'The search exceeded its ten-minute time limit. Please try again.'})
        item = library.store.get(library.pk(family), 'SEARCH#' + job_id)
    result = {key: item[key] for key in ('jobId', 'status', 'stage', 'result', 'error') if key in item}
    result['message'] = LABELS.get(item['stage'], 'Processing your search')
    result['pollAfterMs'] = 2000
    return result


def process(library, message):
    from .chat import run
    from .intelligence import search
    from .worker import protect_task
    body = message['body']
    if body.get('schemaVersion') != 1:
        raise ValueError('Unsupported search job schema')
    family, job_id = identifier(body['familyId']), identifier(body['jobId'])
    item = library.store.get(library.pk(family), 'SEARCH#' + job_id)
    if not item or item['status'] in TERMINAL:
        return True
    if item['status'] == 'RUNNING' and float(item.get('leaseUntil', 0)) > time.time():
        return False
    token = uuid.uuid4().hex
    try:
        item = library.store.put(library.pk(family), 'SEARCH#' + job_id,
            dict(item, status='RUNNING', leaseToken=token,
                 leaseUntil=time.time() + library.settings.visibility), expected=item['rev'])
    except Conflict:
        return False
    stop = threading.Event()
    lost = threading.Event()
    stage = 'queued'
    started = time.monotonic()

    def progress(value):
        nonlocal stage
        LOG.info('Search stage job=%s stage=%s elapsedSeconds=%.2f', job_id, value,
                 time.monotonic() - started)
        stage = value
        if lost.is_set() or not _update(library, family, job_id, {'stage': value}, token):
            raise RuntimeError('The search worker lost its lease')

    def heartbeat():
        while not stop.wait(30):
            try:
                library.queue.extend(message['receipt'], library.settings.visibility)
                protect_task(True)
                if not _update(library, family, job_id,
                        {'leaseUntil': time.time() + library.settings.visibility}, token):
                    lost.set()
                    return
            except Exception:
                lost.set()
                LOG.exception('Search heartbeat failed job=%s', job_id)
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    try:
        protect_task(True)
        thread.start()
        with execution(progress, float(item['deadline'])):
            progress('analyzing_request')
            if item['output'] == 'chat':
                result = run(library, item['userId'], family, item['prompt'])
            else:
                result = search(library, item['userId'], family, item['prompt'])
            if time.time() >= float(item['deadline']):
                raise TimeoutError('The search exceeded its total time limit')
            if lost.is_set():
                raise RuntimeError('The search worker lost its lease')
            _update(library, family, job_id, {'status': 'COMPLETED', 'result': result}, token)
        LOG.info('Search completed job=%s elapsedSeconds=%.2f', job_id, time.monotonic() - started)
    except Exception as error:
        if lost.is_set():
            raise
        label = LABELS.get(stage, 'Processing your search')
        if isinstance(error, (ReadTimeoutError, ConnectTimeoutError)):
            reason = 'The AI service did not respond within its time limit. Please try again.'
        elif isinstance(error, ClientError):
            code = error.response.get('Error', {}).get('Code', '')
            reason = ('The AI service denied access to the configured model.' if code == 'AccessDeniedException'
                      else 'An AI or search service is unavailable. Please try again later.')
        elif isinstance(error, TimeoutError):
            reason = 'The search exceeded its ten-minute time limit. Please try again.'
        elif isinstance(error, PermissionError):
            reason = 'Your access to this family library has changed.'
        elif isinstance(error, RuntimeError) and 'not configured' in str(error):
            reason = 'AI semantic search is not configured for this test environment.'
        else:
            reason = 'The search could not complete this stage. Please try again.'
        _update(library, family, job_id, {'status': 'FAILED', 'error': label + ': ' + reason}, token)
        LOG.exception('Search failed job=%s stage=%s elapsedSeconds=%.2f', job_id, stage,
                      time.monotonic() - started)
    finally:
        stop.set()
        if thread.is_alive():
            thread.join(timeout=6)
        try:
            protect_task(False)
        except Exception:
            LOG.exception('Could not clear search task protection')
    return True
