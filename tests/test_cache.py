import unittest
from unittest.mock import patch
from starlette.testclient import TestClient
from photo_library.api import create_app
from photo_library.cache import PhotoListCache
from test_library import LibraryFixture

class CacheTests(unittest.TestCase):
    def test_expiry_and_budget(self):
        clock=[0]
        cache=PhotoListCache(ttl=5,max_bytes=6,clock=lambda:clock[0])
        _,g=cache.get(('a',False))
        cache.put(('a',False),b'1234',g)
        cache.put(('b',False),b'5678',g)
        self.assertIsNone(cache.get(('a',False))[0])
        self.assertEqual(cache.get(('b',False))[0],b'5678')
        clock[0]=5
        self.assertIsNone(cache.get(('b',False))[0])
        self.assertEqual(cache.bytes,0)

    def test_inflight_query_cannot_restore_invalidated_data(self):
        cache=PhotoListCache()
        _,g=cache.get(('a',False))
        cache.invalidate('a')
        cache.put(('a',False),b'old',g)
        self.assertIsNone(cache.get(('a',False))[0])

class CacheApiTests(LibraryFixture):
    def client_with_warm_cache(self):
        client = TestClient(create_app(self.library, PhotoListCache(ttl=60)))
        client.headers.update({'Authorization': 'Bearer local-development-only', 'X-Dev-User': self.user})
        url = f'/a3/families/{self.family}/photos'
        client.get(url)
        self.assertEqual(client.get(url).headers['x-cache'], 'HIT')
        return client, url

    def test_read_only_search_chat_and_failed_writes_keep_cached_list(self):
        photo = self.ready()
        client, url = self.client_with_warm_cache()
        with client, patch('photo_library.api.search', return_value=[]), patch('photo_library.api.run_chat', return_value={'toolCalls': []}):
            self.assertEqual(client.post(f'/a3/families/{self.family}/search', json={'query': 'people'}).status_code, 200)
            self.assertEqual(client.get(url).headers['x-cache'], 'HIT')
            self.assertEqual(client.post(f'/a3/families/{self.family}/chat', json={'prompt': 'Show my photos'}).status_code, 200)
            self.assertEqual(client.get(url).headers['x-cache'], 'HIT')
            self.assertEqual(client.patch(url + '/' + photo, json={'favorite': 'invalid'}).status_code, 400)
            self.assertEqual(client.get(url).headers['x-cache'], 'HIT')
            self.assertEqual(client.patch(url + '/' + photo, json={'favorite': True}, headers={'X-Dev-User': 'intruder'}).status_code, 403)
            self.assertEqual(client.get(url).headers['x-cache'], 'HIT')

    def test_committed_chat_change_invalidates_even_if_later_model_call_fails(self):
        photo = self.ready()
        client, url = self.client_with_warm_cache()
        def mutate_then_fail(library, user, family, prompt):
            library.update_photo(user, family, photo, {'favorite': True})
            raise RuntimeError('Later model request failed')
        with client, patch('photo_library.api.run_chat', side_effect=mutate_then_fail):
            self.assertEqual(client.post(f'/a3/families/{self.family}/chat', json={'prompt': 'Favourite this photo'}).status_code, 503)
            response = client.get(url)
            self.assertEqual(response.headers['x-cache'], 'MISS')
            self.assertTrue(response.json()[0]['favorite'])

    def test_failed_enqueue_invalidates_the_already_committed_photo_state(self):
        photo = self.upload()
        client, url = self.client_with_warm_cache()
        with client, patch.object(self.library.queue, 'send', side_effect=RuntimeError('Queue unavailable')):
            self.assertEqual(client.post(url + '/' + photo + '/complete').status_code, 503)
            response = client.get(url)
            self.assertEqual(response.headers['x-cache'], 'MISS')
            self.assertEqual(response.json()[0]['status'], 'QUEUE_FAILED')

    def test_album_only_mutation_does_not_evict_photo_list(self):
        photo = self.ready()
        client, url = self.client_with_warm_cache()
        with client:
            response = client.post(f'/a3/families/{self.family}/albums', json={'title': 'Selection', 'photoIds': [photo]})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(client.get(url).headers['x-cache'], 'HIT')

    def test_hits_skip_photo_query_but_never_skip_authorisation(self):
        with patch.dict('os.environ',{'DEV_USER':self.user}), TestClient(create_app(self.library)) as client:
            client.headers['Authorization']='Bearer local-development-only'
            url=f'/a3/families/{self.family}/photos'
            with patch.object(self.library.store,'list',wraps=self.library.store.list) as listing:
                self.assertEqual(client.get(url).headers['x-cache'],'MISS')
                hit=client.get(url)
                self.assertEqual(hit.headers['x-cache'],'HIT')
                self.assertEqual(hit.headers['cache-control'],'no-store')
                self.assertEqual(listing.call_count,1)
                self.assertEqual(client.get(url,headers={'X-Dev-User':'stranger'}).status_code,403)
                self.assertEqual(client.get(url,headers={'Authorization':'Bearer invalid'}).status_code,403)

    def test_write_invalidates_cached_list(self):
        photo=self.ready()
        with patch.dict('os.environ',{'DEV_USER':self.user}), TestClient(create_app(self.library)) as client:
            client.headers['Authorization']='Bearer local-development-only'
            base=f'/a3/families/{self.family}'
            client.get(base+'/photos')
            self.assertEqual(client.get(base+'/photos').headers['x-cache'],'HIT')
            self.assertEqual(client.patch(base+'/photos/'+photo,json={'favorite':True}).status_code,200)
            result=client.get(base+'/photos')
            self.assertEqual(result.headers['x-cache'],'MISS')
            self.assertTrue(result.json()[0]['favorite'])
            self.assertEqual(client.get(base+'/photos?trash=true').headers['x-cache'],'MISS')

if __name__=='__main__': unittest.main()
