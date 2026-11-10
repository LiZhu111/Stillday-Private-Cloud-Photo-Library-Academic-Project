"""Offline AWS-contract tests; no account credentials or network calls."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from photo_library.config import Settings
from photo_library.intelligence import search
from photo_library.queue import AwsQueue
from photo_library.service import Library
from photo_library.storage import AwsStorage
from photo_library.store import AwsStore, Conflict
from photo_library.worker import protect_task
from botocore.exceptions import ClientError


class AwsAdapterTests(unittest.TestCase):
    def test_assignment2_table_layout_uses_existing_key_and_gsi(self):
        store = object.__new__(AwsStore)
        store.table = Mock()
        store.layout = 'assignment2-classifications'
        pk, sk = 'FAMILY#family-one', 'PHOTO#photo-one'
        store.table.get_item.return_value = {'Item': {'payload': {'photoId':'photo-one','rev':1}}}
        self.assertEqual(store.get(pk, sk), {'photoId':'photo-one','rev':1})
        store.table.get_item.assert_called_once_with(
            Key={'imageId': AwsStore._legacy_id(pk, sk)}, ConsistentRead=True)
        store.table.query.return_value = {'Items':[{'payload':{'photoId':'photo-one'}}]}
        self.assertEqual(store.list(pk, 'PHOTO#'), [{'photoId':'photo-one'}])
        query = store.table.query.call_args.kwargs
        self.assertEqual(query['IndexName'], 'by-user-time')
        self.assertNotIn('ConsistentRead', query)

    def test_reuse_mode_accepts_only_known_assignment2_names(self):
        env = {'MEDIA_BUCKET':'n00000000-a2-agent-media',
               'LIBRARY_TABLE':'n00000000-a2-classifications',
               'PROCESSING_QUEUE_URL':'https://sqs.ap-southeast-2.amazonaws.com/000000000000/n00000000-a2-classification',
               'COGNITO_POOL_ID':'pool', 'COGNITO_CLIENT_ID':'client',
               'REUSE_ASSIGNMENT2_RESOURCES':'true',
               'DYNAMODB_LAYOUT':'assignment2-classifications',
               'VECTOR_BUCKET_NAME':'n00000000-a2-photo-vectors',
               'VECTOR_INDEX_NAME':'n00000000-photo-index'}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(Settings(mode='aws').region, 'ap-southeast-2')
        env['MEDIA_BUCKET'] = 'some-other-bucket'
        with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
            Settings(mode='aws')

    def test_sqs_jobs_use_family_as_fair_group(self):
        sqs = Mock()
        body = {'schemaVersion':1,'familyId':'family-one','photoId':'photo-one'}
        with patch.dict(os.environ, {'PROCESSING_QUEUE_URL':'test-queue'}), patch('photo_library.queue.client', return_value=sqs):
            AwsQueue().send(body)
        self.assertEqual(sqs.send_message.call_args.kwargs['MessageGroupId'], 'family-one')
        self.assertNotIn('MessageDeduplicationId', sqs.send_message.call_args.kwargs)

    def test_original_read_is_version_pinned_and_bounded(self):
        s3 = Mock()
        s3.get_object.return_value = {'Body':io.BytesIO(b'12345')}
        with patch.dict(os.environ, {'MEDIA_BUCKET':'a3-test'}), patch('photo_library.storage.client', return_value=s3):
            self.assertEqual(AwsStorage().read('originals/photo.jpg','version-1',limit=5), b'12345')
            s3.get_object.assert_called_with(Bucket='a3-test',Key='originals/photo.jpg',VersionId='version-1')
            s3.get_object.return_value = {'Body':io.BytesIO(b'123456')}
            with self.assertRaises(ValueError):
                AwsStorage().read('originals/photo.jpg','version-1',limit=5)

    def test_signed_post_has_exact_key_mime_and_size_limits(self):
        s3 = Mock()
        with patch.dict(os.environ, {'MEDIA_BUCKET':'a3-test'}), patch('photo_library.storage.client', return_value=s3):
            AwsStorage().upload('originals/family/photo.jpg','image/jpeg',1234)
        args = s3.generate_presigned_post.call_args.kwargs
        self.assertEqual(args['Key'], 'originals/family/photo.jpg')
        self.assertIn(['content-length-range',1,1234], args['Conditions'])
        self.assertIn({'Content-Type':'image/jpeg'}, args['Conditions'])

    def test_dynamodb_condition_failure_is_not_silently_overwritten(self):
        store = object.__new__(AwsStore)
        store.table = Mock()
        store.layout = 'pk-sk'
        store.table.put_item.side_effect = ClientError({'Error':{'Code':'ConditionalCheckFailedException','Message':'Conflict'}}, 'PutItem')
        with self.assertRaises(Conflict):
            store.put('FAMILY#one','PHOTO#one',{'photoId':'one'},expected=3)
        self.assertEqual(store.table.put_item.call_args.kwargs['ExpressionAttributeValues'], {':r':3})

    def test_semantic_search_filters_family_and_excludes_stale_or_foreign_hits(self):
        with tempfile.TemporaryDirectory() as folder:
            library = Library(Settings(mode='local', data_dir=Path(folder)))
            family = library.create_family('owner','Family')['familyId']
            library.store.put(library.pk(family), 'PHOTO#ready', {'photoId':'ready','status':'READY'})
            library.store.put(library.pk(family), 'PHOTO#deleted', {'photoId':'deleted','status':'TRASHED'})
            vectors = Mock()
            vectors.query_vectors.return_value = {'vectors':[{'key':'ready'},{'key':'deleted'},{'key':'foreign'}]}
            with patch.dict(os.environ, {'VECTOR_BUCKET_NAME':'test','VECTOR_INDEX_NAME':'test','CHAT_MODEL_ID':''}), \
                 patch('photo_library.intelligence.client', return_value=vectors), \
                 patch('photo_library.intelligence.embed', return_value=[0.0]*1024), \
                 patch('photo_library.intelligence.analyse_request', return_value={
                     'semanticQuery':'beach','minimumPeople':None,'maximumPeople':None}):
                hits = search(library,'owner',family,'A beach')
                self.assertEqual([x['photo']['photoId'] for x in hits], ['ready'])
                self.assertEqual(vectors.query_vectors.call_args.kwargs['filter'], {'familyId':{'$eq':family}})
                with self.assertRaises(PermissionError):
                    search(library,'stranger',family,'A beach')

    def test_task_protection_rejects_endpoint_failures(self):
        result = Mock()
        result.__enter__ = Mock(return_value=result)
        result.__exit__ = Mock(return_value=None)
        result.read.return_value = b'{"failures":[{"reason":"DEPLOYMENT_BLOCKED"}]}'
        with patch.dict(os.environ, {'ECS_AGENT_URI':'http://127.0.0.1:51679'}), \
             patch('photo_library.worker.urllib.request.urlopen', return_value=result):
            with self.assertRaises(RuntimeError):
                protect_task(True)


if __name__ == '__main__':
    unittest.main()





