"""Protect processing inputs when the course account disallows versioning."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from botocore.exceptions import ClientError
from photo_library.config import Settings
from photo_library.service import Library
from photo_library.storage import AwsStorage

class SnapshotTests(unittest.TestCase):
    def test_versioned_original_does_not_need_copy(self):
        with patch.dict(os.environ, {'MEDIA_BUCKET':'test'}), patch('photo_library.storage.client') as client:
            self.assertEqual(AwsStorage().freeze('originals/a.jpg', {'VersionId':'v1'}), ('originals/a.jpg','v1'))
            client.assert_not_called()

    def test_unversioned_original_uses_etag_conditional_copy_to_new_key(self):
        s3=Mock()
        s3.copy_object.return_value={}
        with patch.dict(os.environ, {'MEDIA_BUCKET':'test'}), patch('photo_library.storage.client',return_value=s3):
            key, version=AwsStorage().freeze('originals/a.jpg', {'ETag':'"initial"'})
        self.assertNotEqual(key, 'originals/a.jpg')
        self.assertIsNone(version)
        self.assertEqual(s3.copy_object.call_args.kwargs['CopySourceIfMatch'], '"initial"')
        self.assertEqual(s3.copy_object.call_args.kwargs['CopySource'], {'Bucket':'test','Key':'originals/a.jpg'})

    def test_changed_source_is_rejected(self):
        s3=Mock()
        s3.copy_object.side_effect=ClientError({'Error':{'Code':'PreconditionFailed','Message':'Changed'}},'CopyObject')
        with patch.dict(os.environ, {'MEDIA_BUCKET':'test'}), patch('photo_library.storage.client',return_value=s3):
            with self.assertRaises(ClientError):
                AwsStorage().freeze('originals/a.jpg', {'ETag':'"initial"'})

    def test_completion_reuses_snapshot_when_enqueue_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            library=Library(Settings(mode='local',data_dir=Path(directory)))
            family=library.create_family('owner','Family')['familyId']
            photo=library.prepare_upload('owner',family,'a.jpg','image/jpeg')['photo']
            original=photo['key']
            library.settings=SimpleNamespace(mode='aws',max_upload=1024)
            library.storage=Mock()
            library.storage.head.return_value={'ContentLength':5,'ETag':'"initial"'}
            library.storage.freeze.return_value=(original+'.sealed-one',None)
            library.queue=Mock()
            library.queue.send.side_effect=[RuntimeError('Queue unavailable'),None]
            with self.assertRaises(RuntimeError):
                library.complete_upload('owner',family,photo['photoId'])
            result=library.complete_upload('owner',family,photo['photoId'])
            self.assertEqual(result['key'],original+'.sealed-one')
            self.assertEqual(result['uploadKey'],original)
            self.assertEqual(result['status'],'QUEUED')
            library.storage.freeze.assert_called_once()

    def test_snapshot_original_preview_is_authorised_without_version_id(self):
        from starlette.testclient import TestClient
        from photo_library.api import create_app
        with tempfile.TemporaryDirectory() as directory:
            library=Library(Settings(mode='local',data_dir=Path(directory)))
            family=library.create_family('owner','Family')['familyId']
            photo=library.prepare_upload('owner',family,'a.jpg','image/jpeg')['photo']
            original=photo['key']
            photo.update(status='QUEUED',key=original+'.sealed-one',uploadKey=original,versionId=None)
            library.save_photo(family,photo)
            library.settings=SimpleNamespace(mode='aws')
            library.storage=Mock()
            library.storage.download.return_value='https://example.invalid/signed'
            with patch('photo_library.api.authenticate',return_value='owner'), TestClient(create_app(library)) as client:
                url=f'/a3/families/{family}/photos/{photo["photoId"]}/download?size=original'
                self.assertEqual(client.get(url).status_code,200)
                library.storage.download.assert_called_once_with(original+'.sealed-one',None)
                with patch('photo_library.api.authenticate',return_value='stranger'):
                    self.assertEqual(client.get(url).status_code,401)
                photo=library.photo('owner',family,photo['photoId'])
                photo['key']=original
                library.save_photo(family,photo)
                self.assertEqual(client.get(url).status_code,404)

if __name__ == '__main__':
    unittest.main()
