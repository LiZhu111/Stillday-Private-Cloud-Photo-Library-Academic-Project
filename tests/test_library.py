import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image
from starlette.testclient import TestClient
from photo_library.analysis import analyse
from photo_library.api import create_app
from photo_library.config import Settings
from photo_library.planner import validate_filters, plan
from photo_library.service import Library
from photo_library.store import Conflict
from photo_library.worker import run_once, process


def image_bytes(color='navy'):
    output = io.BytesIO()
    Image.new('RGB', (640, 480), color).save(output, 'JPEG')
    return output.getvalue()


class LibraryFixture(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'VISION_MODEL_ID':'', 'CHAT_MODEL_ID':'', 'DEV_TOKEN':'local-development-only'})
        self.environment.start()
        self.temp = tempfile.TemporaryDirectory()
        self.library = Library(Settings(mode='local', data_dir=Path(self.temp.name)))
        self.user = 'owner-one'
        self.family = self.library.create_family(self.user, 'Family One')['familyId']

    def tearDown(self):
        self.temp.cleanup()
        self.environment.stop()

    def upload(self, raw=None, captured='2020-10-19'):
        item = self.library.prepare_upload(self.user, self.family, 'sample.jpg', 'image/jpeg', captured)['photo']
        self.library.local_upload(self.user, self.family, item['photoId'], raw or image_bytes())
        return item['photoId']

    def ready(self, raw=None):
        photo_id = self.upload(raw)
        run_once(self.library)
        return photo_id

class LibraryTests(LibraryFixture):
    def test_mpo_phone_photo_uses_primary_jpeg_frame(self):
        output=io.BytesIO()
        Image.new('RGB',(24,16),'red').save(output,format='MPO',save_all=True,
                                             append_images=[Image.new('RGB',(24,16),'blue')])
        metadata,variants=analyse(output.getvalue())
        self.assertEqual((metadata['width'],metadata['height']),(24,16))
        self.assertEqual(len(variants),3)

    def test_async_pipeline_survives_new_application_instance(self):
        photo_id = self.upload()
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'QUEUED')
        other_process = Library(self.library.settings)
        run_once(other_process)
        item = self.library.photo(self.user, self.family, photo_id)
        self.assertEqual(item['status'], 'READY')
        self.assertEqual(len(item['variants']), 3)
        self.assertEqual(item['capturedDate'], '2020-10-19')
        for variant in item['variants']:
            self.assertLessEqual(variant['width'], 640)  # Never upscale.
            self.assertTrue(self.library.storage.path(variant['key']).exists())

    def test_cross_family_access_is_denied(self):
        photo_id = self.ready()
        for operation in (lambda: self.library.photo('other-user', self.family, photo_id),
                          lambda: self.library.suggestions('other-user', self.family),
                          lambda: self.library.album('other-user', self.family, 'Stolen', {})):
            with self.assertRaises(PermissionError):
                operation()

    def test_viewer_cannot_upload_or_modify_members(self):
        self.library.add_member(self.user, self.family, 'viewer', 'viewer')
        self.assertEqual(self.library.photos('viewer', self.family), [])
        with self.assertRaises(PermissionError):
            self.library.prepare_upload('viewer', self.family, 'x.jpg', 'image/jpeg')
        with self.assertRaises(PermissionError):
            self.library.add_member('viewer', self.family, 'other', 'contributor')

    def test_duplicate_delivery_does_not_repeat_completed_work(self):
        photo_id = self.ready()
        self.library.queue.send({'schemaVersion':1,'familyId':self.family,'photoId':photo_id})
        with patch('photo_library.worker.analyse', side_effect=AssertionError('Must not run')):
            run_once(self.library)

    def test_active_lease_prevents_parallel_processing(self):
        photo_id = self.upload()
        item = self.library.photo(self.user, self.family, photo_id)
        item.update(status='PROCESSING', leaseToken='another-worker', leaseUntil=time.time()+100)
        self.library.save_photo(self.family, item)
        message = self.library.queue.receive(180, 5)
        with patch('photo_library.worker.analyse', side_effect=AssertionError('Must not run')):
            self.assertFalse(process(self.library, message))

    def test_expired_lease_can_be_recovered(self):
        photo_id = self.upload()
        item = self.library.photo(self.user, self.family, photo_id)
        item.update(status='PROCESSING', leaseToken='dead-worker', leaseUntil=time.time()-1)
        self.library.save_photo(self.family, item)
        run_once(self.library)
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'READY')

    def test_recycle_bin_removes_album_and_memory_results_and_restores(self):
        photo_id = self.ready()
        album = self.library.album(self.user, self.family, 'Selected', {})
        self.library.trash(self.user, self.family, photo_id)
        self.assertEqual(self.library.photos(self.user, self.family), [])
        self.assertEqual(self.library.get_album(self.user, self.family, album['albumId'])['photos'], [])
        self.assertEqual(self.library.on_this_day(self.user, self.family, '2026-10-19'), [])
        self.library.trash(self.user, self.family, photo_id, restore=True)
        self.assertEqual(len(self.library.on_this_day(self.user, self.family, '2026-10-19')), 1)

    def test_trash_during_processing_is_not_resurrected(self):
        photo_id = self.upload()
        def concurrent_trash(raw):
            self.library.trash(self.user, self.family, photo_id)
            return analyse(raw)
        with patch('photo_library.worker.analyse', side_effect=concurrent_trash):
            run_once(self.library)
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'TRASHED')

    def test_duplicate_suggestions_are_non_destructive(self):
        first, second = self.ready(), self.ready()
        suggestions = self.library.suggestions(self.user, self.family)
        self.assertFalse(suggestions['automaticDeletion'])
        self.assertEqual(suggestions['groups'][0]['matches'][0]['reason'], 'identical-file')
        self.assertEqual(len(self.library.photos(self.user, self.family)), 2)
        self.library.update_photo(self.user, self.family, first, {'keep':True})
        self.assertEqual(self.library.suggestions(self.user, self.family)['groups'], [])

    def test_memories_use_capture_date_not_upload_date(self):
        photo_id = self.ready()
        self.assertEqual(len(self.library.on_this_day(self.user, self.family, '2026-10-19')), 1)
        self.assertEqual(self.library.on_this_day(self.user, self.family, '2020-10-19'), [])
        self.assertEqual(self.library.on_this_day(self.user, self.family, '2026-10-20'), [])
        self.library.update_photo(self.user, self.family, photo_id, {'hideMemory':True})
        self.assertEqual(self.library.on_this_day(self.user, self.family, '2026-10-19'), [])

    def test_manual_album_filters_and_exact_duplicate_removal(self):
        first = self.ready()
        self.ready()
        self.library.update_photo(self.user, self.family, first, {'people':['Mum']})
        album = self.library.album(self.user, self.family, 'Mum', {'person':'Mum', 'orientation':'landscape'})
        self.assertEqual(album['photoIds'], [first])
        self.assertEqual(len(self.library.album(self.user, self.family, 'All', {})['photoIds']), 1)

    def test_recreation_requires_two_authorised_photos(self):
        first, second = self.ready(), self.ready(image_bytes('red'))
        pair = self.library.recreation(self.user, self.family, first, second)
        self.assertEqual(pair['type'], 'real-photo-comparison')
        with self.assertRaises(ValueError):
            self.library.recreation(self.user, self.family, first, first)

    def test_failed_enqueue_has_explicit_retry_path(self):
        receipt = self.library.prepare_upload(self.user, self.family, 'sample.jpg', 'image/jpeg')
        photo_id = receipt['photo']['photoId']
        with patch.object(self.library.queue, 'send', side_effect=RuntimeError('Queue unavailable')):
            with self.assertRaises(RuntimeError):
                self.library.local_upload(self.user, self.family, photo_id, image_bytes())
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'QUEUE_FAILED')
        self.library.complete_upload(self.user, self.family, photo_id)
        run_once(self.library)
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'READY')

    def test_bad_image_retries_then_moves_to_dead_letter_queue(self):
        photo_id = self.upload(b'not an image')
        with self.assertLogs('photo-worker', level='ERROR'):
            for _ in range(5):
                run_once(self.library)
                with self.library.queue.connect() as db:
                    db.execute('UPDATE jobs SET available=0')
        run_once(self.library)
        with self.library.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dead_letters').fetchone()[0], 1)
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'FAILED')

    def test_revision_conflict_does_not_overwrite_data(self):
        photo_id = self.ready()
        stale = self.library.photo(self.user, self.family, photo_id)
        self.library.update_photo(self.user, self.family, photo_id, {'favorite':True})
        with self.assertRaises(Conflict):
            self.library.save_photo(self.family, stale)
        self.assertTrue(self.library.photo(self.user, self.family, photo_id)['favorite'])

    def test_model_output_cannot_select_identity_or_delete(self):
        for filters in ({'familyId':'another-family'}, {'delete':True}, {'limit':True}, {'limit':10000}):
            with self.assertRaises(ValueError):
                validate_filters(filters)

    def test_invalid_model_output_is_rejected(self):
        from unittest.mock import Mock
        fake = Mock()
        fake.converse.return_value = {'output':{'message':{'content':[{'text':'{"delete":true}'}]}}}
        with patch.dict(os.environ, {'CHAT_MODEL_ID':'test-model'}), patch('photo_library.planner.client', return_value=fake):
            with self.assertRaises(ValueError):
                plan('Delete everything')

    def test_local_storage_rejects_path_escape(self):
        with self.assertRaises(ValueError):
            self.library.storage.path('../secret.txt')

    def test_aws_configuration_rejects_a2_resources(self):
        values = {'MEDIA_BUCKET':'n00000000-a2-media', 'LIBRARY_TABLE':'n00000000-a3-library',
                  'PROCESSING_QUEUE_URL':'https://example/n00000000-a3-processing',
                  'COGNITO_POOL_ID':'existing-pool','COGNITO_CLIENT_ID':'existing-client'}
        with patch.dict(os.environ, values):
            with self.assertRaises(ValueError):
                Settings(mode='aws')


class ApiTests(LibraryFixture):
    def test_http_flow_and_download_authorisation(self):
        with patch.dict(os.environ, {'DEV_USER':self.user}), TestClient(create_app(self.library)) as client:
            self.assertEqual(client.get('/healthz').status_code, 200)
            base = f'/a3/families/{self.family}'
            self.assertEqual(client.get(base+'/photos').status_code, 401)
            client.headers['Authorization'] = 'Bearer local-development-only'
            upload = client.post(base+'/uploads', json={'name':'photo.jpg','mime':'image/jpeg'})
            self.assertEqual(upload.status_code, 201, upload.text)
            receipt = upload.json()
            result = client.put(receipt['upload']['url'], content=image_bytes())
            self.assertEqual(result.status_code, 202, result.text)
            photo_id = receipt['photo']['photoId']
            preview = client.get(base+f'/photos/{photo_id}/download?size=original')
            self.assertEqual(preview.status_code, 200)
            self.assertTrue(preview.content.startswith(b'\xff\xd8'))
            self.assertEqual(client.get(base+f'/photos/{photo_id}/download?size=320').status_code, 404)
            run_once(self.library)
            download = client.get(base+f'/photos/{photo_id}/download')
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.headers['content-type'], 'image/jpeg')
            self.assertEqual(client.post(base+f'/photos/{photo_id}/trash').status_code, 200)
            self.assertEqual(client.get(base+f'/photos/{photo_id}/download').status_code, 200)
            self.assertEqual(client.get(base+f'/photos/{photo_id}/download?size=original').status_code, 200)
            client.headers['X-Dev-User'] = 'intruder'
            self.assertEqual(client.get(base+f'/photos/{photo_id}/download').status_code, 403)
            client.headers.pop('Authorization')
            self.assertEqual(client.get(base+f'/photos/{photo_id}/download').status_code, 401)


if __name__ == '__main__':
    unittest.main()

