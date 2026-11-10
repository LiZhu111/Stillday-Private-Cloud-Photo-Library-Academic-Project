"""Browser regressions with a temporary local API, synthetic images and mocked AI.

Run: python -m unittest discover -s tests/browser -v
Requires requirements-dev.txt and an installed Microsoft Edge browser.
"""
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
import uvicorn

from photo_library.api import create_app
from photo_library.config import Settings
from photo_library.service import Library
from photo_library.worker import run_once


class PhotoUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.environment = patch.dict(os.environ, {'APP_MODE': 'local', 'VISION_MODEL_ID': '', 'CHAT_MODEL_ID': '',
            'DEV_TOKEN': 'local-development-only', 'DEV_USER': 'local-owner'})
        cls.environment.start()
        cls.temp = tempfile.TemporaryDirectory()
        cls.library = Library(Settings(mode='local', data_dir=Path(cls.temp.name)))
        cls.family = cls.library.create_family('local-owner', 'Browser regression family')['familyId']
        cls.photos = {}
        shapes = [('wide-people', 1200, 700, 'people'), ('tall-people', 700, 1200, 'people'),
            ('wide-scenery', 1500, 600, 'landscape'), ('tall-scenery', 600, 1500, 'landscape'),
            ('square', 800, 800, 'other'), ('panorama', 2000, 250, 'other'), ('very-tall', 250, 2000, 'other')]
        for name, width, height, category in shapes:
            picture = Image.new('RGB', (width, height), '#456789')
            draw = ImageDraw.Draw(picture)
            draw.rectangle((0, 0, width - 1, height - 1), outline='yellow', width=12)
            draw.rectangle((0, 0, 60, 60), fill='red')
            draw.rectangle((width - 61, height - 61, width - 1, height - 1), fill='lime')
            data = io.BytesIO()
            picture.save(data, 'JPEG')
            photo = cls.library.prepare_upload('local-owner', cls.family, name + '.jpg', 'image/jpeg')['photo']
            cls.library.local_upload('local-owner', cls.family, photo['photoId'], data.getvalue())
            run_once(cls.library)
            cls.photos[name] = {'id': photo['photoId'], 'orientation': 'landscape' if width > height else 'portrait' if height > width else 'square', 'category': category}

        def interpret(query):
            category = 'people' if 'people' in query or '\u4eba\u7269' in query else 'landscape'
            return {'semanticQuery': category, 'minimumPeople': None, 'maximumPeople': None}

        def query_vectors(**arguments):
            clauses = arguments['filter'].get('$and', [arguments['filter']])
            values = {key: condition['$eq'] for clause in clauses for key, condition in clause.items()}
            assert values['familyId'] == cls.family
            category = 'people' if arguments['queryVector']['float32'] == [1.0] else 'landscape'
            return {'vectors': [{'key': item['id']} for item in cls.photos.values()
                if item['category'] == category]}

        cls.patches = [patch.dict(os.environ, {'CHAT_MODEL_ID': '', 'VECTOR_BUCKET_NAME': 'browser-bucket', 'VECTOR_INDEX_NAME': 'browser-index'}),
            patch('photo_library.intelligence.analyse_request', side_effect=interpret),
            patch('photo_library.intelligence.embed', side_effect=lambda query: [1.0] if query == 'people' else [2.0]),
            patch('photo_library.intelligence.client', return_value=SimpleNamespace(query_vectors=query_vectors)),
            patch('photo_library.chat.generate_search_answer', side_effect=lambda query, candidates: ('Matching photos found.', candidates))]
        for item in cls.patches:
            item.start()
        cls.worker_stop = threading.Event()
        def consume_jobs():
            while not cls.worker_stop.is_set():
                run_once(cls.library)
                cls.worker_stop.wait(0.05)
        cls.worker_thread = threading.Thread(target=consume_jobs, daemon=True)
        cls.worker_thread.start()
        cls.socket = socket.socket()
        cls.socket.bind(('127.0.0.1', 0))
        cls.url = f'http://127.0.0.1:{cls.socket.getsockname()[1]}'
        cls.server = uvicorn.Server(uvicorn.Config(create_app(cls.library), log_level='error'))
        cls.thread = threading.Thread(target=lambda: cls.server.run(sockets=[cls.socket]), daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 15
        while not cls.server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError('Local browser test server did not start')
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='msedge', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.worker_stop.set()
        cls.worker_thread.join(timeout=10)
        cls.browser.close()
        cls.playwright.stop()
        cls.server.should_exit = True
        cls.thread.join(timeout=10)
        cls.socket.close()
        for item in reversed(cls.patches):
            item.stop()
        cls.environment.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 900})
        self.context.add_init_script('localStorage.setItem("stillday.family", ' + json.dumps(self.family) + '); localStorage.setItem("stillday.user", "local-owner");')
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.route('https://fonts.googleapis.com/**', lambda route: route.abort())
        self.page.route('https://fonts.gstatic.com/**', lambda route: route.abort())
        self.page.goto(self.url)
        self.page.locator('.photo-card').first.wait_for()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def assert_complete_fit(self):
        self.page.wait_for_function("""() => {
            const i = document.querySelector('.player-image');
            return i && i.complete && i.naturalWidth && i.classList.contains('loaded') && i.style.width;
        }""")
        self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        geometry = self.page.locator('.player-image').evaluate("""image => {
            const r = image.getBoundingClientRect(), stage = image.parentElement.getBoundingClientRect();
            const style = getComputedStyle(image), clipped = [];
            for (let parent = image.parentElement; parent; parent = parent.parentElement) {
                const p = parent.getBoundingClientRect(), s = getComputedStyle(parent);
                if (/hidden|clip|auto|scroll/.test(s.overflowX) && (r.left < p.left - 2 || r.right > p.right + 2)) clipped.push('horizontal');
                if (/hidden|clip|auto|scroll/.test(s.overflowY) && (r.top < p.top - 2 || r.bottom > p.bottom + 2)) clipped.push('vertical');
            }
            return {width:r.width, height:r.height, left:r.left, top:r.top, right:r.right, bottom:r.bottom,
                stageWidth:stage.width, stageHeight:stage.height, viewportWidth:innerWidth, viewportHeight:innerHeight,
                naturalWidth:image.naturalWidth, naturalHeight:image.naturalHeight, objectFit:style.objectFit,
                clipPath:style.clipPath, transform:style.transform, clipped};
        }""")
        g = geometry
        self.assertEqual(g['clipped'], [], g)
        self.assertEqual(g['clipPath'], 'none', g)
        self.assertEqual(g['transform'], 'none', g)
        self.assertNotEqual(g['objectFit'], 'cover', g)
        self.assertGreaterEqual(g['left'], -2)
        self.assertGreaterEqual(g['top'], -2)
        self.assertLessEqual(g['right'], g['viewportWidth'] + 2, g)
        self.assertLessEqual(g['bottom'], g['viewportHeight'] + 2, g)
        scale = min(g['stageWidth'] / g['naturalWidth'], g['stageHeight'] / g['naturalHeight'])
        self.assertGreater(scale, 0)
        self.assertAlmostEqual(g['width'], g['naturalWidth'] * scale, delta=2, msg=str(g))
        self.assertAlmostEqual(g['height'], g['naturalHeight'] * scale, delta=2, msg=str(g))

    def open_photo(self, name):
        photo = self.photos[name]['id']
        self.page.locator(f'.photo-card[data-photo-id="{photo}"] .photo-frame').click()
        self.assert_complete_fit()

    def test_all_aspect_ratios_fit_preview_and_fullscreen(self):
        for name in ('wide-people', 'tall-people', 'square', 'panorama', 'very-tall'):
            with self.subTest(name=name):
                self.open_photo(name)
                self.page.locator('.player-fullscreen').click()
                self.page.wait_for_function('document.fullscreenElement !== null')
                self.assert_complete_fit()
                self.page.locator('.player-fullscreen').click()
                self.page.wait_for_function('document.fullscreenElement === null')
                self.page.locator('.player-close').click()

    def test_resize_navigation_and_close_preserve_full_image(self):
        self.open_photo('tall-people')
        for width, height in ((390, 844), (844, 390), (1280, 720)):
            self.page.set_viewport_size({'width': width, 'height': height})
            self.assert_complete_fit()
        before = self.page.locator('.player-counter').inner_text()
        self.page.locator('.player-arrow.next').click()
        self.page.wait_for_function('(before) => document.querySelector(".player-counter").textContent !== before', arg=before)
        self.assert_complete_fit()
        self.page.keyboard.press('ArrowLeft')
        self.page.wait_for_function('(before) => document.querySelector(".player-counter").textContent === before', arg=before)
        self.assert_complete_fit()
        self.page.keyboard.press('Escape')
        self.assertEqual(self.page.locator('.player-overlay').count(), 0)

    def test_semantic_category_search_ignores_orientation_words(self):
        cases = [
            ('Show all horizontal photos of people', {'wide-people', 'tall-people'}),
            ('Show vertical landscape photos', {'wide-scenery', 'tall-scenery'}),
            ('Show horizontal landscape photos', {'wide-scenery', 'tall-scenery'}),
        ]
        for prompt, names in cases:
            with self.subTest(prompt=prompt):
                self.page.locator('#chat-input').fill(prompt)
                self.page.locator('#chat-form button[type="submit"]').click()
                expected = {self.photos[name]['id'] for name in names}
                self.page.wait_for_function("""expected => {
                    const ids = [...document.querySelectorAll('.chat-results .photo-card')].map(card => card.dataset.photoId);
                    return ids.length === expected.length && expected.every(id => ids.includes(id));
                }""", arg=list(expected))

