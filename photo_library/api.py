"""Small English-language HTTP interface; no public demo mode in AWS."""
from functools import partial
import json
import logging
import os
from pathlib import Path
from decimal import Decimal
from datetime import datetime, timezone
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, FileResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from botocore.exceptions import ClientError
from .auth import authenticate
from .config import Settings
from .cache import PhotoListCache
from .service import Library
from .store import Conflict
from .intelligence import search
from .chat import run as run_chat
from .search_query import is_simple_photo_search
from . import search_jobs

LOG = logging.getLogger('photo-api')


def response(value, status=200):
    return JSONResponse(json.loads(json.dumps(value, default=lambda x: float(x) if isinstance(x, Decimal) else str(x))),
                        status_code=status, headers={'Cache-Control': 'no-store'})


async def read_body(request, limit):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, 'Request body is too large')
    return bytes(body)


def create_app(library=None, photo_cache=None):
    library = library or Library(Settings())
    photo_cache = photo_cache or PhotoListCache()

    async def dispatch(request, operation):
        with library.observe_photo_changes(photo_cache.invalidate):
            return await dispatch_observed(request, operation)

    async def dispatch_observed(request, operation):
        try:
            if operation == 'health':
                return response({'status': 'ok', 'service': 'photo-library-api', 'mode': library.settings.mode,
                                 'chatToolsAvailable': bool(os.getenv('CHAT_MODEL_ID'))})
            user = await run_in_threadpool(authenticate, request.headers.get('authorization', ''),
                                           library.settings, request.headers.get('x-dev-user'))
            family, photo_id = request.path_params.get('family'), request.path_params.get('photo')
            # Reject cross-tenant callers before reading upload data or invoking a model.
            if family:
                await run_in_threadpool(library.member, user, family)
            data = {}
            if request.method in ('POST', 'PATCH'):
                raw = await read_body(request, 16384)
                data = json.loads(raw) if raw else {}
                if not isinstance(data, dict):
                    raise ValueError('Expected a JSON object')
            if operation == 'family':
                result = await run_in_threadpool(library.create_family, user, data['name'])
            elif operation == 'member':
                result = await run_in_threadpool(library.add_member, user, family, data['userId'], data['role'])
            elif operation == 'photos':
                trash = request.query_params.get('trash') == 'true'
                cache_key = (family, trash)
                cached, generation = photo_cache.get(cache_key)
                headers = {'Cache-Control':'no-store', 'X-Cache-Scope':'api-process',
                           'X-Cache-TTL':str(photo_cache.ttl)}
                if cached is not None:
                    headers['X-Cache'] = 'HIT'
                    return Response(cached,media_type='application/json',headers=headers)
                result = await run_in_threadpool(library.photos, user, family, trash)
                rendered = response(result)
                photo_cache.put(cache_key, rendered.body, generation)
                rendered.headers.update(headers)
                rendered.headers['X-Cache'] = 'MISS'
                return rendered
            elif operation == 'upload':
                result = await run_in_threadpool(library.prepare_upload, user, family, data['name'], data['mime'], data.get('capturedDate'))
            elif operation == 'content':
                raw = await read_body(request, library.settings.max_upload)
                result = await run_in_threadpool(library.local_upload, user, family, photo_id, raw)
            elif operation == 'complete':
                result = await run_in_threadpool(library.complete_upload, user, family, photo_id)
            elif operation == 'photo':
                result = await run_in_threadpool(library.photo, user, family, photo_id)
            elif operation == 'metadata':
                result = await run_in_threadpool(library.update_photo, user, family, photo_id, data)
            elif operation in ('trash', 'restore'):
                result = await run_in_threadpool(library.trash, user, family, photo_id, operation == 'restore')
            elif operation == 'suggestions':
                result = await run_in_threadpool(library.suggestions, user, family)
            elif operation == 'search_status':
                result = await run_in_threadpool(search_jobs.get, library, user, family, request.path_params['job'])
            elif operation == 'search':
                if data.get('background') is True:
                    result = await run_in_threadpool(search_jobs.submit, library, user, family,
                        data['query'], 'search', data.get('requestId'))
                    return response(result, 202)
                result = await run_in_threadpool(search, library, user, family, data['query'])
            elif operation == 'chat':
                if isinstance(data.get('prompt'), str) and (data.get('background') is True or is_simple_photo_search(data['prompt'])):
                    result = await run_in_threadpool(search_jobs.submit, library, user, family,
                        data['prompt'], 'chat', data.get('requestId'))
                    return response(result, 202)
                result = await run_in_threadpool(run_chat, library, user, family, data['prompt'])
            elif operation == 'reanalyze':
                result = await run_in_threadpool(library.reanalyze_photos, user, family)
            elif operation == 'memories':
                day = request.query_params.get('date', datetime.now(timezone.utc).date().isoformat())
                result = await run_in_threadpool(library.on_this_day, user, family, day)
            elif operation == 'album':
                result = await run_in_threadpool(library.album, user, family, data['title'], data.get('filters', {}), data.get('prompt'), data.get('photoIds'))
            elif operation == 'albums':
                result = await run_in_threadpool(library.albums, user, family)
            elif operation == 'get_album':
                result = await run_in_threadpool(library.get_album, user, family, request.path_params['album'])
            elif operation == 'update_album':
                result = await run_in_threadpool(library.update_album, user, family, request.path_params['album'],
                                                 data.get('title'), data.get('photoIdsToAdd', []))
            elif operation == 'remove_album_photo':
                result = await run_in_threadpool(library.remove_album_photo, user, family,
                                                 request.path_params['album'], request.path_params['photo'])
            elif operation == 'delete_album':
                result = await run_in_threadpool(library.delete_album, user, family, request.path_params['album'])
            elif operation == 'recreation':
                result = await run_in_threadpool(library.recreation, user, family, data['originalPhotoId'], data['newPhotoId'])
            elif operation == 'recreations':
                records = await run_in_threadpool(library.store.list, library.pk(family), 'RECREATION#')
                ready = {x['photoId'] for x in await run_in_threadpool(library.photos, user, family) if x['status'] == 'READY'}
                result = [x for x in records if x['originalPhotoId'] in ready and x['newPhotoId'] in ready]
            elif operation == 'download':
                item = await run_in_threadpool(library.photo, user, family, photo_id)
                size = request.query_params.get('size', '1280')
                if size == 'original':
                    # Pending originals may be previewed by an authenticated family
                    # member; derived display sizes remain unavailable until READY.
                    if item['status'] not in ('READY', 'QUEUED', 'PROCESSING', 'TRASHED'):
                        raise LookupError('Original photo is not available for preview')
                    if library.settings.mode == 'aws' and not (item.get('versionId') or
                            (item.get('uploadKey') and item['key'] != item['uploadKey'])):
                        raise LookupError('Original photo upload is not complete')
                    key, version, mime = item['key'], item.get('versionId'), item['mime']
                else:
                    if item['status'] not in ('READY', 'TRASHED'):
                        raise LookupError('Photo is still being processed')
                    variant = next((x for x in item.get('variants', []) if x['size'] == size), None)
                    if variant is None:
                        raise ValueError('Use size 320, 1280, 2560 or original')
                    key, version, mime = variant['key'], None, 'image/jpeg'
                if library.settings.mode == 'local':
                    return FileResponse(library.storage.path(key), media_type=mime, headers={'Cache-Control': 'private, no-store'})
                result = {'url': await run_in_threadpool(library.storage.download, key, version), 'expiresIn': 300}
            else:
                raise LookupError('Unknown operation')
            return response(result, 202 if operation in ('complete', 'content', 'reanalyze') else 201 if operation in ('family', 'upload', 'album', 'recreation') else 200)
        except HTTPException as error:
            return response({'error': error.detail}, error.status_code)
        except PermissionError as error:
            return response({'error': str(error)}, 403 if request.headers.get('authorization') else 401)
        except Conflict as error:
            return response({'error': str(error)}, 409)
        except KeyError:
            return response({'error': 'A required field is missing'}, 400)
        except (ValueError, TypeError) as error:
            return response({'error': str(error)}, 400)
        except LookupError as error:
            return response({'error': str(error)}, 404)
        except RuntimeError as error:
            LOG.warning('Operation unavailable: %s', operation)
            if operation == 'chat' and 'Bedrock chat tools are not configured' in str(error):
                return response({'error': 'Operation unavailable: AI chat is not configured. Try a saved-name or date search, or use the photo filters.'}, 503)
            if operation == 'album' and 'Natural-language planning is not configured' in str(error):
                return response({'error': 'Operation unavailable: natural-language album planning is not configured. Use the explicit person, date, or orientation filters.'}, 503)
            return response({'error': 'Operation unavailable; configure the required service and retry'}, 503)
        except Exception as error:
            if isinstance(error, ClientError) and error.response.get('Error', {}).get('Code') == 'AccessDeniedException' and operation in ('chat', 'album'):
                LOG.warning('AI model access denied by AWS policy for %s', operation)
                return response({'error': 'The configured AI model is blocked by AWS account policy. Use explicit photo filters or search captions already saved in the library.'}, 503)
            LOG.exception('Application operation failed: %s', operation)
            return response({'error': 'Operation unavailable; retry or use explicit album filters'}, 503)

    base = '/a3/families/{family}'
    routes = [('/healthz', 'health', ['GET']), ('/a3/families', 'family', ['POST']),
              (base+'/members', 'member', ['POST']), (base+'/photos', 'photos', ['GET']),
              (base+'/uploads', 'upload', ['POST']), (base+'/suggestions', 'suggestions', ['GET']),
              (base+'/search', 'search', ['POST']),
              (base+'/search-jobs/{job}', 'search_status', ['GET']),
              (base+'/chat', 'chat', ['POST']), (base+'/reanalyze', 'reanalyze', ['POST']),
              (base+'/memories/on-this-day', 'memories', ['GET']), (base+'/albums', 'albums', ['GET']), (base+'/albums', 'album', ['POST']),
              (base+'/albums/{album}', 'get_album', ['GET']),
              (base+'/albums/{album}', 'update_album', ['PATCH']),
              (base+'/albums/{album}', 'delete_album', ['DELETE']),
              (base+'/albums/{album}/photos/{photo}', 'remove_album_photo', ['DELETE']),
              (base+'/recreations', 'recreation', ['POST']), (base+'/recreations', 'recreations', ['GET'])]
    for suffix, operation, methods in [('', 'photo', ['GET']), ('', 'metadata', ['PATCH']),
        ('/content', 'content', ['PUT']), ('/complete', 'complete', ['POST']),
        ('/trash', 'trash', ['POST']), ('/restore', 'restore', ['POST']), ('/download', 'download', ['GET'])]:
        routes.append((base+'/photos/{photo}'+suffix, operation, methods))
    routes = [Route(path, partial(dispatch, operation=operation), methods=methods)
              for path, operation, methods in routes]
    web_root = Path(__file__).resolve().parent.parent / 'frontend'
    if web_root.exists():
        async def frontend_index(request):
            return FileResponse(web_root / 'index.html', headers={'Cache-Control': 'no-store'})
        routes.append(Route('/', frontend_index, methods=['GET']))
        routes.append(Mount('/', app=StaticFiles(directory=web_root, html=True), name='frontend'))
    return Starlette(routes=routes)


# Use uvicorn photo_library.api:create_app --factory to avoid import-time resources.
