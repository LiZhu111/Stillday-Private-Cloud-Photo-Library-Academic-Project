"""Verify durable searches independently of browser and model latency."""
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from botocore.exceptions import ReadTimeoutError
from starlette.testclient import TestClient
from test_library import LibraryFixture
from photo_library.api import create_app
from photo_library import aws, chat, intelligence, search_jobs
from photo_library.worker import run_once
from photo_library.service import Library


class SearchJobTests(LibraryFixture):
    def setUp(self):
        super().setUp()
        self.http = TestClient(create_app(self.library))
        self.headers = {'Authorization': 'Bearer local-development-only', 'X-Dev-User': self.user}
        self.url = f'/a3/families/{self.family}'

    def submit(self, **extra):
        return self.http.post(self.url + '/chat', headers=self.headers,
            json=dict(prompt='Show me a picture with bird', background=True, **extra))

    def status(self, job):
        return self.http.get(self.url + '/search-jobs/' + job['jobId'], headers=self.headers)

    def test_slow_analysis_returns_accepted_and_can_be_polled_until_complete(self):
        photo_id = self.ready()
        photo = self.library.photo(self.user, self.family, photo_id)
        started, release = threading.Event(), threading.Event()
        events = []

        def analyse(query):
            events.append('analyse')
            started.set()
            if not release.wait(5):
                raise RuntimeError('Test worker was not released')
            return {'semanticQuery': 'bird on a branch', 'minimumPeople': None, 'maximumPeople': None}

        def retrieve(**kwargs):
            events.append('vectors')
            return {'vectors': [{'key': photo_id, 'distance': 0.1}]}

        def answer(query, candidates):
            events.append('answer')
            return 'I found a bird.', candidates

        with patch.dict(os.environ, {'VECTOR_BUCKET_NAME': 'test', 'VECTOR_INDEX_NAME': 'test'}), \
             patch.object(intelligence, 'analyse_request', side_effect=analyse), \
             patch.object(intelligence, 'embed', return_value=[0.1]), \
             patch.object(intelligence, 'client', return_value=SimpleNamespace(query_vectors=retrieve)), \
             patch.object(chat, 'generate_search_answer', side_effect=answer):
            response = self.submit()
            self.assertEqual(response.status_code, 202)
            job = response.json()
            self.assertEqual(job['status'], 'QUEUED')
            thread = threading.Thread(target=run_once, args=(Library(self.library.settings),))
            thread.start()
            try:
                self.assertTrue(started.wait(3))
                current = self.status(job).json()
                self.assertEqual(current['status'], 'RUNNING')
                self.assertEqual(current['stage'], 'analyzing_request')
                self.assertNotIn('prompt', current)
            finally:
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())
            current = self.status(job).json()
        self.assertEqual(current['status'], 'COMPLETED')
        self.assertEqual(current['result']['reply'], 'I found a bird.')
        self.assertEqual(current['result']['toolCalls'][0]['result'][0]['photoId'], photo['photoId'])
        self.assertEqual(events, ['analyse', 'vectors', 'answer'])

    def test_bedrock_timeout_exposes_analysis_stage_without_retrieving_vectors(self):
        with patch.dict(os.environ, {'CHAT_MODEL_ID': 'nemotron', 'VECTOR_BUCKET_NAME': 'test', 'VECTOR_INDEX_NAME': 'test'}), \
             patch.object(intelligence, 'search_model_client') as model, \
             patch.object(intelligence, 'embed') as embed:
            model.return_value.converse.side_effect = ReadTimeoutError(endpoint_url='https://example.invalid')
            job = self.submit().json()
            run_once(self.library)
            current = self.status(job).json()
        self.assertEqual(current['status'], 'FAILED')
        self.assertEqual(current['stage'], 'analyzing_request')
        self.assertIn('AI service did not respond', current['error'])
        embed.assert_not_called()

    def test_job_status_is_private_to_creator_even_with_family_membership(self):
        job = self.submit().json()
        self.library.add_member(self.user, self.family, 'viewer-two', 'viewer')
        headers = dict(self.headers, **{'X-Dev-User': 'viewer-two'})
        response = self.http.get(self.url + '/search-jobs/' + job['jobId'], headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_repeated_request_id_does_not_enqueue_another_search(self):
        with patch.object(self.library.queue, 'send', wraps=self.library.queue.send) as send:
            first = self.submit(requestId='request-one').json()
            second = self.submit(requestId='request-one').json()
        self.assertEqual(first['jobId'], second['jobId'])
        send.assert_called_once()

    def test_completed_duplicate_delivery_does_not_repeat_models(self):
        job = self.submit().json()
        search_jobs._update(self.library, self.family, job['jobId'], {'status': 'COMPLETED', 'result': {}})
        with patch.object(chat, 'run') as model:
            run_once(self.library)
        model.assert_not_called()
        self.assertIsNone(self.library.queue.receive(1, 5))

    def test_expired_task_fails_without_running_model(self):
        job = self.submit().json()
        search_jobs._update(self.library, self.family, job['jobId'], {'deadline': time.time() - 1})
        current = self.status(job).json()
        self.assertEqual(current['status'], 'FAILED')
        self.assertIn('time limit', current['error'])
        with patch.object(chat, 'run') as model:
            run_once(self.library)
        model.assert_not_called()

    def test_queue_failure_has_a_terminal_status(self):
        with patch.object(self.library.queue, 'send', side_effect=RuntimeError('Queue unavailable')):
            job = self.submit().json()
        self.assertEqual(job['status'], 'FAILED')
        self.assertIn('could not be queued', job['error'])

    def test_search_sdk_has_longer_wait_without_automatic_duplicate_inference(self):
        aws.search_model_client.cache_clear()
        try:
            with patch.object(aws.boto3, 'client', return_value=MagicMock()) as factory:
                aws.search_model_client()
            config = factory.call_args.kwargs['config']
            self.assertEqual(config.read_timeout, 180)
            self.assertEqual(config.retries['total_max_attempts'], 1)
        finally:
            aws.search_model_client.cache_clear()

    def test_answer_timeout_is_reported_after_vector_retrieval(self):
        photo_id = self.ready()
        vectors = SimpleNamespace(query_vectors=MagicMock(return_value={'vectors': [{'key': photo_id}]}))
        plan = {'semanticQuery': 'bird', 'minimumPeople': None, 'maximumPeople': None}
        with patch.dict(os.environ, {'CHAT_MODEL_ID': 'nemotron', 'VECTOR_BUCKET_NAME': 'test', 'VECTOR_INDEX_NAME': 'test'}), \
             patch.object(intelligence, 'analyse_request', return_value=plan), \
             patch.object(intelligence, 'embed', return_value=[0.1]), \
             patch.object(intelligence, 'client', return_value=vectors), \
             patch.object(intelligence, 'search_model_client') as model:
            model.return_value.converse.side_effect = ReadTimeoutError(endpoint_url='https://example.invalid')
            job = self.submit().json()
            run_once(self.library)
        current = self.status(job).json()
        self.assertEqual(current['status'], 'FAILED')
        self.assertEqual(current['stage'], 'generating_answer')
        vectors.query_vectors.assert_called_once()

    def test_search_endpoint_can_return_queued_vector_results(self):
        photo_id = self.ready()
        with patch.object(intelligence, 'search', return_value=[{'photo': {'photoId': photo_id}}]):
            response = self.http.post(self.url + '/search', headers=self.headers,
                json={'query': 'bird', 'background': True})
            self.assertEqual(response.status_code, 202)
            run_once(self.library)
        current = self.status(response.json()).json()
        self.assertEqual(current['status'], 'COMPLETED')
        self.assertEqual(current['result'][0]['photo']['photoId'], photo_id)
