"""Family-scoped application use cases. Models never choose the caller identity."""
from datetime import date, datetime, timezone
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import uuid
from .analysis import distance
from .aws import client
from .planner import validate_filters, plan
from .store import LocalStore, AwsStore, Conflict
from .storage import LocalStorage, AwsStorage
from .queue import LocalQueue, AwsQueue


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', value):
        raise ValueError('Invalid identifier')
    return value


def label(value, maximum=100):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid text value')
    return value.strip()


def _album_title(hint, prompt, photos, existing_titles):
    """Create a specific family album title, avoiding names already in use."""
    existing = [str(value).strip() for value in existing_titles if isinstance(value, str) and value.strip()]
    model = os.getenv('CHAT_MODEL_ID')
    title = ''
    if model:
        examples = [{'caption':str(item.get('caption', ''))[:220],
                     'labels':item.get('labels', [])[:8] if isinstance(item.get('labels'), list) else [],
                     'people':item.get('people', [])[:8] if isinstance(item.get('people'), list) else [],
                     'capturedDate':item.get('capturedDate'), 'orientation':item.get('orientation')}
                    for item in photos[:30]]
        try:
            response = client('bedrock-runtime').converse(modelId=model,
                system=[{'text':'Name a family photo album using its request and photo details. Return only JSON '
                         'with a single string field "title". Make it specific and concise (3-8 words), using '
                         'clear people, occasion, place, season, or visual details only when supported. Do not '
                         'invent facts. Do not return a name that matches or closely resembles any existing '
                         'album title; choose a distinct alternative. Do not include quotation marks.'}],
                messages=[{'role':'user','content':[{'text':json.dumps({'request':prompt or hint,
                    'providedTitle':hint, 'photos':examples, 'existingAlbumTitles':existing},ensure_ascii=False)}]}],
                inferenceConfig={'maxTokens':120,'temperature':0.3})
            text=''.join(part.get('text','') for part in response['output']['message'].get('content',[])).strip()
            if text.startswith('```'):
                text=text.removeprefix('```json').removeprefix('```').removesuffix('```').strip()
            candidate=json.loads(text).get('title')
            if isinstance(candidate,str) and 2 <= len(candidate.strip()) <= 100:
                title=candidate.strip()
        except Exception:
            pass
    if not title:
        title=(hint or '').strip()
        if title.lower() in {'photo search','new album','photo album','album'} and photos:
            title=str(photos[0].get('caption') or '').split('.')[0][:75].strip() or 'Family moments'
        title=title[:100] or 'Family moments'
    used={re.sub(r'\s+',' ',value).casefold() for value in existing}
    base=title[:100]
    candidate=base
    suffix=2
    while re.sub(r'\s+',' ',candidate).casefold() in used:
        ending=f' ({suffix})'
        candidate=base[:100-len(ending)].rstrip()+ending
        suffix+=1
    return candidate


class Library:
    def __init__(self, settings):
        self.settings = settings
        self._photo_change_listener = ContextVar('photo_change_listener', default=None)
        if settings.mode == 'local':
            self.store = LocalStore(settings.data_dir)
            self.storage = LocalStorage(settings.data_dir)
            self.queue = LocalQueue(settings.data_dir)
        else:
            self.store, self.storage, self.queue = AwsStore(settings), AwsStorage(), AwsQueue()

    @contextmanager
    def observe_photo_changes(self, listener):
        token = self._photo_change_listener.set(listener)
        try:
            yield
        finally:
            self._photo_change_listener.reset(token)

    def _photo_changed(self, family):
        listener = self._photo_change_listener.get()
        if listener is not None:
            listener(family)

    def pk(self, family):
        return 'FAMILY#' + identifier(family)

    def member(self, user, family, write=False, owner=False):
        value = self.store.get(self.pk(family), 'MEMBER#' + identifier(user))
        if not value or (write and value['role'] not in ('owner', 'contributor')) or (owner and value['role'] != 'owner'):
            raise PermissionError('Family access denied')
        return value

    def create_family(self, user, name):
        family = uuid.uuid4().hex
        self.store.put(self.pk(family), 'MEMBER#' + identifier(user),
                       {'familyId': family, 'name': label(name), 'userId': user, 'role': 'owner'})
        return {'familyId': family, 'name': name}

    def add_member(self, user, family, member_id, role):
        self.member(user, family, owner=True)
        identifier(member_id)
        if role not in ('contributor', 'viewer'):
            raise ValueError('Use contributor or viewer')
        key = 'MEMBER#' + member_id
        old = self.store.get(self.pk(family), key)
        if old and old['role'] == 'owner':
            raise ValueError('The owner role cannot be changed')
        return self.store.put(self.pk(family), key, {'userId': member_id, 'role': role}, old['rev'] if old else 0)

    def photo(self, user, family, photo_id, write=False):
        self.member(user, family, write=write)
        item = self.store.get(self.pk(family), 'PHOTO#' + identifier(photo_id))
        if not item:
            raise LookupError('Photo not found')
        return item

    def save_photo(self, family, item):
        saved = self.store.put(self.pk(family), 'PHOTO#' + item['photoId'], item, item['rev'])
        self._photo_changed(family)
        return saved

    def photos(self, user, family, include_trash=False):
        self.member(user, family)
        items = self.store.list(self.pk(family), 'PHOTO#')
        return [x for x in items if include_trash or x['status'] != 'TRASHED']

    def reanalyze_photos(self, user, family):
        self.member(user, family, write=True)
        queued, failed = [], []
        for item in self.photos(user, family):
            needs_count = bool(os.getenv('VISION_MODEL_ID')) and not item.get('peopleCountAnalyzed')
            if item['status'] != 'READY' or (item.get('caption') and item.get('semanticIndexed') and not needs_count):
                continue
            old = dict(item)
            request_id = uuid.uuid4().hex
            item.update(status='QUEUED', reanalysisId=request_id, analysisError=None)
            try:
                self.save_photo(family, item)
                try:
                    self.queue.send({'schemaVersion':1, 'familyId':family,
                                     'photoId':item['photoId'], 'reanalysisId':request_id})
                except Exception:
                    current = self.store.get(self.pk(family), 'PHOTO#' + item['photoId'])
                    if current.get('reanalysisId') == request_id and current['status'] == 'QUEUED':
                        current.update(status='READY', analysisError='QueueUnavailable')
                        self.save_photo(family, current)
                    failed.append(item['photoId'])
                    continue
                queued.append(item['photoId'])
            except Exception:
                current = self.store.get(self.pk(family), 'PHOTO#' + item['photoId'])
                if current and current.get('reanalysisId') == request_id and current['status'] == 'QUEUED':
                    current.update(status=old['status'], analysisError='QueueUnavailable')
                    self.save_photo(family, current)
                failed.append(item['photoId'])
        return {'queued':len(queued), 'failed':len(failed), 'photoIds':queued}

    def prepare_upload(self, user, family, name, mime, captured=None):
        self.member(user, family, write=True)
        label(name, 200)
        extensions = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
        if mime not in extensions:
            raise ValueError('Use JPEG, PNG or WebP')
        if captured:
            captured = date.fromisoformat(captured).isoformat()
        photo_id = uuid.uuid4().hex
        key = f'originals/{family}/{photo_id}.{extensions[mime]}'
        item = self.store.put(self.pk(family), 'PHOTO#' + photo_id, {
            'photoId': photo_id, 'familyId': family, 'uploadedBy': user, 'name': name,
            'key': key, 'mime': mime, 'status': 'UPLOADING', 'uploadedAt': now(),
            'capturedDate': captured, 'dateSource': 'user' if captured else 'unknown',
            'people': [], 'favorite': False, 'hideMemory': False, 'keep': False})
        self._photo_changed(family)
        if self.settings.mode == 'aws':
            upload = self.storage.upload(key, mime, self.settings.max_upload)
        else:
            upload = {'method': 'PUT', 'url': f'/a3/families/{family}/photos/{photo_id}/content'}
        return {'photo': item, 'upload': upload}

    def local_upload(self, user, family, photo_id, raw):
        if self.settings.mode != 'local':
            raise ValueError('Upload directly using the signed S3 POST')
        item = self.photo(user, family, photo_id, write=True)
        if item['status'] != 'UPLOADING':
            raise Conflict('Upload already completed')
        if not 0 < len(raw) <= self.settings.max_upload:
            raise ValueError('Invalid upload size')
        self.storage.put(item['key'], raw, item['mime'])
        return self.complete_upload(user, family, photo_id)

    def complete_upload(self, user, family, photo_id):
        item = self.photo(user, family, photo_id, write=True)
        if item['status'] not in ('UPLOADING', 'QUEUE_FAILED', 'QUEUED', 'FAILED'):
            return item
        if item['status'] == 'UPLOADING':
            head = self.storage.head(item['key'])
            if not 0 < head['ContentLength'] <= self.settings.max_upload:
                raise ValueError('Invalid upload size')
            if self.settings.mode == 'aws':
                uploaded_key = item['key']
                item['key'], item['versionId'] = self.storage.freeze(uploaded_key, head)
                if item['key'] != uploaded_key:
                    item['uploadKey'] = uploaded_key
            else:
                item['versionId'] = head.get('VersionId')
        item['status'] = 'QUEUED'
        item = self.save_photo(family, item)
        try:
            self.queue.send({'schemaVersion': 1, 'familyId': family, 'photoId': photo_id})
        except Exception:
            item['status'] = 'QUEUE_FAILED'
            try:
                self.save_photo(family, item)
            except Conflict:
                pass
            raise RuntimeError('Upload is saved; retry the complete endpoint to enqueue processing')
        return item

    def update_photo(self, user, family, photo_id, changes):
        item = self.photo(user, family, photo_id, write=True)
        if set(changes) - {'people', 'favorite', 'hideMemory', 'keep', 'capturedDate'}:
            raise ValueError('Unsupported photo changes')
        if 'people' in changes:
            if not isinstance(changes['people'], list) or len(changes['people']) > 30:
                raise ValueError('Invalid people list')
            changes['people'] = list(dict.fromkeys(label(x) for x in changes['people']))
        for key in ('favorite', 'hideMemory', 'keep'):
            if key in changes and type(changes[key]) is not bool:
                raise ValueError('Expected a boolean')
        if 'capturedDate' in changes:
            changes['capturedDate'] = date.fromisoformat(changes['capturedDate']).isoformat()
            changes['dateSource'] = 'user'
        item.update(changes)
        return self.save_photo(family, item)

    def trash(self, user, family, photo_id, restore=False):
        item = self.photo(user, family, photo_id, write=True)
        if restore:
            if item['status'] != 'TRASHED':
                raise Conflict('Photo is not in the recycle bin')
            item['status'] = 'READY' if item.get('variants') else 'FAILED'
            item.pop('trashedAt', None)
        else:
            if item['status'] == 'TRASHED':
                return item
            item.update(status='TRASHED', trashedAt=now())
        return self.save_photo(family, item)

    def suggestions(self, user, family):
        photos = [x for x in self.photos(user, family) if x['status'] == 'READY']
        if len(photos) > 500:
            raise ValueError('Prototype review is limited to 500 photos; add an indexed candidate search before scaling this feature')
        candidates = [x for x in photos if not x.get('keep')]
        groups, seen = [], set()
        for i, first in enumerate(candidates):
            if first['photoId'] in seen:
                continue
            matches = []
            for other in candidates[i+1:]:
                if other['photoId'] in seen:
                    continue
                exact = first['sha256'] == other['sha256']
                similar = (first.get('capturedDate') and first['capturedDate'] == other.get('capturedDate')
                           and distance(first['dhash'], other['dhash']) <= 5)
                if exact or similar:
                    matches.append({'photoId': other['photoId'], 'reason': 'identical-file' if exact else 'visual-similarity-candidate'})
                    seen.add(other['photoId'])
            if matches:
                groups.append({'referencePhotoId': first['photoId'], 'matches': matches})
        return {'groups': groups, 'qualityReview': [{'photoId': x['photoId'], 'reasons': x['qualityWarnings']}
                for x in candidates if x.get('qualityWarnings')], 'automaticDeletion': False}

    def on_this_day(self, user, family, day):
        target = date.fromisoformat(day)
        matches = [x for x in self.photos(user, family) if x['status'] == 'READY' and
                   not x.get('hideMemory') and x.get('capturedDate') and
                   x['capturedDate'][5:] == target.isoformat()[5:] and x['capturedDate'][:4] < str(target.year)]
        matches.sort(key=lambda x: (x.get('favorite', False), float(x.get('sharpness', 0))), reverse=True)
        unique = {}
        for item in matches:
            unique.setdefault(item['sha256'], item)
        return list(unique.values())[:30]

    def album(self, user, family, title, filters=None, prompt=None, photo_ids=None):
        self.member(user, family, write=True)
        if photo_ids is not None:
            if not isinstance(photo_ids, list) or len(photo_ids) > 100:
                raise ValueError('photoIds must be an array with at most 100 IDs')
            selected = []
            selected_photos = []
            seen = set()
            for photo_id in photo_ids:
                item = self.photo(user, family, photo_id)
                if item['status'] == 'READY' and photo_id not in seen:
                    selected.append(item['photoId'])
                    selected_photos.append(item)
                    seen.add(photo_id)
            album_title = _album_title(title, prompt, selected_photos,
                                       [item.get('title') for item in self.albums(user, family)])
            album_id = uuid.uuid4().hex
            return self.store.put(self.pk(family), 'ALBUM#' + album_id,
                {'albumId': album_id, 'title': label(album_title), 'filters': None, 'createdAt': now(),
                 'photoIds': selected, 'fixed': True})
        filters = filters or {}
        if prompt is not None:
            filters = plan(prompt)
        filters = validate_filters(filters)
        matches = []
        for item in self.photos(user, family):
            if item['status'] != 'READY':
                continue
            captured = item.get('capturedDate')
            if 'person' in filters and filters['person'] not in item['people']:
                continue
            if 'orientation' in filters and filters['orientation'] != item['orientation']:
                continue
            if 'since' in filters and (not captured or captured < filters['since']):
                continue
            if 'until' in filters and (not captured or captured > filters['until']):
                continue
            if float(item.get('sharpness', 0)) < filters.get('minSharpness', 0):
                continue
            matches.append(item)
        matches.sort(key=lambda x: (x.get('favorite', False), float(x.get('sharpness', 0))), reverse=True)
        unique = {}
        for item in matches:
            unique.setdefault(item['sha256'], item)
        selected_photos = list(unique.values())[:filters['limit']]
        album_title = _album_title(title, prompt, selected_photos,
                                   [item.get('title') for item in self.albums(user, family)])
        album_id = uuid.uuid4().hex
        return self.store.put(self.pk(family), 'ALBUM#' + album_id,
            {'albumId': album_id, 'title': label(album_title), 'filters': filters, 'createdAt': now(),
             'photoIds': [x['photoId'] for x in selected_photos]})

    def get_album(self, user, family, album_id):
        self.member(user, family)
        item = self.store.get(self.pk(family), 'ALBUM#' + identifier(album_id))
        if not item:
            raise LookupError('Album not found')
        # Re-check current state, so old albums do not expose trashed photos.
        photos = [self.photo(user, family, x) for x in item['photoIds']]
        return dict(item, photos=[x for x in photos if x['status'] == 'READY'])

    def albums(self, user, family):
        self.member(user, family)
        items = self.store.list(self.pk(family), 'ALBUM#')
        items.sort(key=lambda item: item.get('createdAt', ''), reverse=True)
        return items

    def remove_album_photo(self, user, family, album_id, photo_id):
        self.member(user, family, write=True)
        album_id, photo_id = identifier(album_id), identifier(photo_id)
        key = 'ALBUM#' + album_id
        album = self.store.get(self.pk(family), key)
        if not album:
            raise LookupError('Album not found')
        photo_ids = album.get('photoIds', [])
        if photo_id not in photo_ids:
            return album
        album['photoIds'] = [item for item in photo_ids if item != photo_id]
        return self.store.put(self.pk(family), key, album, int(album.get('rev', 1)))

    def update_album(self, user, family, album_id, title=None, photo_ids_to_add=None):
        self.member(user, family, write=True)
        album_id = identifier(album_id)
        key = 'ALBUM#' + album_id
        album = self.store.get(self.pk(family), key)
        if not album:
            raise LookupError('Album not found')
        if title is not None:
            album['title'] = label(title)
        if photo_ids_to_add is not None:
            if not isinstance(photo_ids_to_add, list) or len(photo_ids_to_add) > 100:
                raise ValueError('photoIdsToAdd must be an array with at most 100 IDs')
            existing = list(dict.fromkeys(album.get('photoIds', [])))
            added = []
            seen = set(existing)
            for photo_id in photo_ids_to_add:
                item = self.photo(user, family, photo_id)
                if item['status'] == 'READY' and photo_id not in seen:
                    added.append(item['photoId'])
                    seen.add(photo_id)
            if len(existing) + len(added) > 100:
                raise ValueError('An album can contain at most 100 photos')
            album['photoIds'] = existing + added
        return self.store.put(self.pk(family), key, album, int(album.get('rev', 1)))

    def delete_album(self, user, family, album_id):
        self.member(user, family, write=True)
        album_id = identifier(album_id)
        key = 'ALBUM#' + album_id
        album = self.store.get(self.pk(family), key)
        if not album:
            raise LookupError('Album not found')
        self.store.delete(self.pk(family), key, int(album.get('rev', 1)))
        return {'albumId': album_id, 'deleted': True}

    def recreation(self, user, family, original_id, new_id):
        self.member(user, family, write=True)
        if original_id == new_id:
            raise ValueError('Choose two different photos')
        for key in (original_id, new_id):
            if self.photo(user, family, key)['status'] != 'READY':
                raise ValueError('Both photos must be ready')
        recreation_id = uuid.uuid4().hex
        return self.store.put(self.pk(family), 'RECREATION#' + recreation_id,
            {'recreationId': recreation_id, 'originalPhotoId': original_id, 'newPhotoId': new_id,
             'createdAt': now(), 'type': 'real-photo-comparison'})

