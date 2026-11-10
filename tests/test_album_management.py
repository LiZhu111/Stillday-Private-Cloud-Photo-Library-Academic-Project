"""Regression coverage for saved albums and independent photo ownership."""
from starlette.testclient import TestClient
from test_library import LibraryFixture
from photo_library.api import create_app


class AlbumManagementTests(LibraryFixture):
    def setUp(self):
        super().setUp()
        self.http = TestClient(create_app(self.library))
        self.http.headers.update({'Authorization': 'Bearer local-development-only', 'X-Dev-User': self.user})
        self.base = f'/a3/families/{self.family}'

    def tearDown(self):
        self.http.close()
        super().tearDown()

    def album(self, photo_ids, title='Saved selection'):
        response = self.http.post(self.base + '/albums', json={'title': title, 'photoIds': photo_ids})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_remove_from_one_album_preserves_other_album_and_original(self):
        photo = self.ready()
        first, second = self.album([photo]), self.album([photo], 'Another album')
        response = self.http.delete(f"{self.base}/albums/{first['albumId']}/photos/{photo}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.library.get_album(self.user, self.family, first['albumId'])['photoIds'], [])
        self.assertEqual(self.library.get_album(self.user, self.family, second['albumId'])['photoIds'], [photo])
        record = self.library.photo(self.user, self.family, photo)
        self.assertEqual(record['status'], 'READY')
        self.assertTrue(self.library.storage.path(record['key']).exists())
        self.assertEqual([item['photoId'] for item in self.http.get(self.base + '/photos').json()], [photo])

    def test_delete_album_preserves_photos_and_other_albums(self):
        photo = self.ready()
        first, second = self.album([photo]), self.album([photo], 'Keep this album')
        self.assertEqual(self.http.delete(f"{self.base}/albums/{first['albumId']}").status_code, 200)
        self.assertEqual(self.http.get(f"{self.base}/albums/{first['albumId']}").status_code, 404)
        self.assertEqual(self.library.photo(self.user, self.family, photo)['status'], 'READY')
        self.assertEqual(len(self.library.get_album(self.user, self.family, second['albumId'])['photos']), 1)

    def test_rename_and_add_are_persistent_and_do_not_duplicate_photos(self):
        first, second = self.ready(), self.ready()
        album = self.album([first])
        url = f"{self.base}/albums/{album['albumId']}"
        result = self.http.patch(url, json={'title': 'Our weekend', 'photoIdsToAdd': [second, second, first]})
        self.assertEqual(result.status_code, 200)
        saved = self.http.get(url).json()
        self.assertEqual(saved['title'], 'Our weekend')
        self.assertEqual(saved['photoIds'], [first, second])

    def test_trash_from_library_hides_photo_in_every_album_and_can_restore(self):
        photo = self.ready()
        albums = [self.album([photo]), self.album([photo], 'Second selection')]
        self.assertEqual(self.http.post(f'{self.base}/photos/{photo}/trash').status_code, 200)
        for album in albums:
            self.assertEqual(self.http.get(f"{self.base}/albums/{album['albumId']}").json()['photos'], [])
        self.assertEqual(self.library.photo(self.user, self.family, photo)['status'], 'TRASHED')
        self.assertEqual(self.http.post(f'{self.base}/photos/{photo}/restore').status_code, 200)
        for album in albums:
            self.assertEqual(len(self.http.get(f"{self.base}/albums/{album['albumId']}").json()['photos']), 1)

    def test_foreign_photo_and_viewer_edits_are_rejected_without_partial_changes(self):
        photo = self.ready()
        album = self.album([photo])
        other_family = self.library.create_family(self.user, 'Other family')['familyId']
        foreign = self.library.prepare_upload(self.user, other_family, 'other.jpg', 'image/jpeg')['photo']['photoId']
        url = f"{self.base}/albums/{album['albumId']}"
        response = self.http.patch(url, json={'title': 'Must not persist', 'photoIdsToAdd': [foreign]})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.http.get(url).json()['title'], album['title'])
        self.library.add_member(self.user, self.family, 'viewer', 'viewer')
        self.http.headers['X-Dev-User'] = 'viewer'
        self.assertEqual(self.http.patch(url, json={'title': 'Forbidden'}).status_code, 403)
        self.assertEqual(self.http.delete(url + '/photos/' + photo).status_code, 403)
        self.assertEqual(self.http.delete(url).status_code, 403)
