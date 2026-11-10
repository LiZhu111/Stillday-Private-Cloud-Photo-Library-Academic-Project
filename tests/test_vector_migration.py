import copy
import unittest
from scripts.backfill_vector_orientation import prepare_update


class VectorMigrationTests(unittest.TestCase):
    def setUp(self):
        self.vector = {'key': 'photo', 'data': {'float32': [0.1, 0.2]},
                       'metadata': {'familyId': 'family', 'photoId': 'photo', 'caption': 'A mountain', 'labels': ['nature']}}
        self.record = {'familyId': 'family', 'photoId': 'photo', 'status': 'READY', 'width': 80, 'height': 120}

    def test_preserves_embedding_and_all_metadata_without_mutating_input(self):
        original = copy.deepcopy(self.vector)
        result = prepare_update(self.vector, self.record)
        self.assertEqual(result['data'], original['data'])
        self.assertEqual(result['metadata'], dict(original['metadata'], orientation='portrait'))
        self.assertEqual(self.vector, original)
        self.assertIsNone(prepare_update(result, self.record))

    def test_skips_foreign_missing_and_busy_records(self):
        for record in (None, dict(self.record, familyId='other'), dict(self.record, status='PROCESSING')):
            with self.subTest(record=record):
                self.assertIsNone(prepare_update(self.vector, record))

    def test_uses_dimensions_when_old_orientation_is_incorrect(self):
        result = prepare_update(self.vector, dict(self.record, orientation='landscape'))
        self.assertEqual(result['metadata']['orientation'], 'portrait')
