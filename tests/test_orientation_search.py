"""Semantic photo search regressions."""
import io
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient
from PIL import Image
from test_library import LibraryFixture
from photo_library import intelligence
from photo_library.api import create_app


class SemanticSearchTests(LibraryFixture):
    def test_request_is_analyzed_and_family_scoped_vectors_are_retrieved(self):
        photo = {'photoId': 'bird', 'status': 'READY', 'caption': 'A bird by a tree'}
        library = SimpleNamespace(member=MagicMock(), photo=lambda user, family, key: photo)
        vectors = SimpleNamespace(query_vectors=MagicMock(return_value={'vectors': [
            {'key': 'bird', 'distance': 0.12, 'metadata': {}}]}))
        plan = {'semanticQuery': 'a bird in nature', 'minimumPeople': None, 'maximumPeople': None}
        with patch.dict(os.environ, {'VECTOR_BUCKET_NAME': 'bucket', 'VECTOR_INDEX_NAME': 'index'}), \
             patch.object(intelligence, 'client', return_value=vectors), \
             patch.object(intelligence, 'embed', return_value=[0.1]), \
             patch.object(intelligence, 'analyse_request', return_value=plan) as analyse:
            result = intelligence.search(library, self.user, self.family, 'Show me a bird')
        analyse.assert_called_once_with('Show me a bird')
        self.assertEqual(vectors.query_vectors.call_args.kwargs['filter'], {'familyId': {'$eq': self.family}})
        self.assertEqual(vectors.query_vectors.call_args.kwargs['queryVector'], {'float32': [0.1]})
        self.assertEqual(result[0]['photo']['photoId'], 'bird')

    def test_pagination_and_ready_status_filter_are_preserved(self):
        ready = {'photoId': 'ready', 'status': 'READY'}
        deleted = {'photoId': 'deleted', 'status': 'TRASHED'}
        photos = {'ready': ready, 'deleted': deleted}
        library = SimpleNamespace(member=MagicMock(), photo=lambda user, family, key: photos[key])
        vectors = SimpleNamespace(query_vectors=MagicMock(side_effect=[
            {'vectors': [], 'nextToken': 'next'}, {'vectors': [{'key': 'ready'}, {'key': 'deleted'}]}]))
        with patch.dict(os.environ, {'VECTOR_BUCKET_NAME': 'bucket', 'VECTOR_INDEX_NAME': 'index'}), \
             patch.object(intelligence, 'client', return_value=vectors), \
             patch.object(intelligence, 'embed', return_value=[0.1]), \
             patch.object(intelligence, 'analyse_request', return_value={
                 'semanticQuery': 'bird', 'minimumPeople': None, 'maximumPeople': None}):
            result = intelligence.search(library, self.user, self.family, 'bird')
        self.assertEqual([item['photo']['photoId'] for item in result], ['ready'])
        self.assertEqual(vectors.query_vectors.call_args_list[1].kwargs['nextToken'], 'next')

    def test_search_api_uses_only_query_semantics(self):
        http = TestClient(create_app(self.library))
        headers = {'Authorization': 'Bearer local-development-only', 'X-Dev-User': self.user}
        with patch('photo_library.api.search', return_value=[]) as search:
            response = http.post(f'/a3/families/{self.family}/search', headers=headers,
                                 json={'query': 'Show me a bird', 'orientation': 'portrait'})
        self.assertEqual(response.status_code, 200, response.text)
        search.assert_called_once_with(self.library, self.user, self.family, 'Show me a bird')

    def test_gemma_caption_and_labels_are_saved_without_orientation_filter_metadata(self):
        bedrock = SimpleNamespace(converse=MagicMock(return_value={'output': {'message': {'content': [
            {'text': '{"caption":"A person outdoors","labels":["people","park"],"peopleCount":1}'}]}}}))
        vectors = SimpleNamespace(put_vectors=MagicMock())
        with patch.dict(os.environ, {'VISION_MODEL_ID': 'vision', 'VECTOR_BUCKET_NAME': 'bucket', 'VECTOR_INDEX_NAME': 'index'}), \
             patch.object(intelligence, 'client', side_effect=lambda name: vectors if name == 's3vectors' else bedrock), \
             patch.object(intelligence, 'embed', return_value=[0.1, 0.2]):
            result = intelligence.describe_and_index(b'image', self.family, 'photo')
        vector = vectors.put_vectors.call_args.kwargs['vectors'][0]
        self.assertEqual(vector['metadata']['caption'], 'A person outdoors')
        self.assertEqual(vector['metadata']['labels'], ['people', 'park'])
        self.assertEqual(vector['metadata']['familyId'], self.family)
        self.assertNotIn('orientation', vector['metadata'])
        self.assertTrue(result['semanticIndexed'])

    def test_worker_calls_gemma_indexer_without_orientation_argument(self):
        data = io.BytesIO()
        Image.new('RGB', (80, 160), 'navy').save(data, 'JPEG')
        with patch('photo_library.worker.describe_and_index', return_value={}) as indexer:
            self.ready(data.getvalue())
        self.assertEqual(indexer.call_args.args[1:], (self.family, indexer.call_args.args[2]))
        self.assertNotIn('orientation', indexer.call_args.kwargs)
