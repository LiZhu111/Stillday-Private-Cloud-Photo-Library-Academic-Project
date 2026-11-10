import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient
from test_library import LibraryFixture
from photo_library.api import create_app
from photo_library import chat
from photo_library.search_query import is_simple_photo_search


class ChatToolTests(LibraryFixture):
    def test_semantic_photo_search_uses_nemotron_analysis_and_grounded_answer(self):
        match = {'photoId': 'bird-photo', 'status': 'READY', 'caption': 'A small bird on a branch', 'labels': ['bird']}
        prompt = 'Show me a picture with bird'
        with patch.object(chat, 'semantic_search', return_value=[{'photo': match}]) as semantic, \
             patch.object(chat, 'generate_search_answer', return_value=('I found a bird perched on a branch.', [match])) as answer, \
             patch.object(chat, 'client') as tool_model:
            result = chat.run(self.library, self.user, self.family, prompt)
        semantic.assert_called_once_with(self.library, self.user, self.family, prompt)
        answer.assert_called_once_with(prompt, [match])
        tool_model.assert_not_called()
        self.assertEqual(result['reply'], 'I found a bird perched on a branch.')
        self.assertEqual(result['toolCalls'][0]['result'][0]['photoId'], 'bird-photo')

    def test_landscape_term_is_sent_as_semantic_text_without_axis_handling(self):
        prompt = 'Show me landscape photos'
        match = {'photoId': 'scenery-photo', 'status': 'READY', 'caption': 'A mountain valley', 'labels': ['scenery']}
        with patch.object(chat, 'semantic_search', return_value=[{'photo': match}]) as semantic, \
             patch.object(chat, 'generate_search_answer', return_value=('Here are the scenery photos.', [match])):
            result = chat.run(self.library, self.user, self.family, prompt)
        semantic.assert_called_once_with(self.library, self.user, self.family, prompt)
        self.assertEqual(result['toolCalls'][0]['input']['query'], prompt)

    def test_search_intent_classifier_leaves_structured_actions_to_tools(self):
        for prompt in ('Show me a picture with bird', 'Find cat photos', 'Show me photos of people'):
            with self.subTest(prompt=prompt):
                self.assertTrue(is_simple_photo_search(prompt))
        for prompt in ('Show me all photos', 'Show me photos from 2024', 'Create an album of bird photos', 'Play slideshow'):
            with self.subTest(prompt=prompt):
                self.assertFalse(is_simple_photo_search(prompt))

    def test_bedrock_tool_use_executes_authenticated_photo_operation(self):
        photo_id = self.ready()
        bedrock = SimpleNamespace(converse=MagicMock(side_effect=[
            {'output': {'message': {'role': 'assistant', 'content': [
                {'toolUse': {'toolUseId': 'call-1', 'name': 'list_photos', 'input': {'limit': 10}}}]}}},
            {'output': {'message': {'role': 'assistant', 'content': [{'text': 'I found your photo.'}]}}},
        ]))
        with patch.dict(os.environ, {'CHAT_MODEL_ID': 'test-model'}), patch.object(chat, 'client', return_value=bedrock):
            result = chat.run(self.library, self.user, self.family, 'Show me my photos')
        self.assertEqual(result['reply'], 'I found your photo.')
        self.assertEqual(result['toolCalls'][0]['name'], 'list_photos')
        self.assertEqual(result['toolCalls'][0]['result'][0]['photoId'], photo_id)
        self.assertEqual(bedrock.converse.call_count, 2)

    def test_chat_tools_require_model_configuration(self):
        with patch.dict(os.environ, {'CHAT_MODEL_ID': ''}):
            with self.assertRaisesRegex(RuntimeError, 'not configured'):
                chat.run(self.library, self.user, self.family, 'Find a photo')

    def test_chat_api_exposes_tool_route_but_requires_configured_model(self):
        http = TestClient(create_app(self.library))
        response = http.post(f'/a3/families/{self.family}/chat',
            headers={'Authorization': 'Bearer local-development-only', 'X-Dev-User': self.user},
            json={'prompt': 'Find a photo'})
        self.assertEqual(response.status_code, 503)
        self.assertIn('Operation unavailable', response.json()['error'])

    def test_tool_cannot_read_another_familys_photo(self):
        with self.assertRaises(PermissionError):
            chat.execute(self.library, 'intruder', self.family, 'list_photos', {'limit': 10})

    def test_trash_tool_only_prepares_a_confirmable_request(self):
        photo_id = self.ready()
        result = chat.execute(self.library, self.user, self.family, 'request_trash_photo', {'photoId': photo_id})
        self.assertTrue(result['requiresUserConfirmation'])
        self.assertEqual(self.library.photo(self.user, self.family, photo_id)['status'], 'READY')
