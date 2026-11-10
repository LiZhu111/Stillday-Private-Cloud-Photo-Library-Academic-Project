"""Developer-facing client for the A3 API; all displayed text is English."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
import httpx


def main():
    parser = argparse.ArgumentParser(description='Intelligent Cloud Photo Library')
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--family')
    commands = parser.add_subparsers(dest='command', required=True)
    family = commands.add_parser('create-family')
    family.add_argument('name')
    upload = commands.add_parser('upload')
    upload.add_argument('file', type=Path)
    upload.add_argument('--date', help='Capture date: YYYY-MM-DD')
    commands.add_parser('photos')
    commands.add_parser('suggestions')
    memories = commands.add_parser('memories')
    memories.add_argument('date')
    album = commands.add_parser('album')
    album.add_argument('title')
    album.add_argument('--person')
    album.add_argument('--prompt')
    args = parser.parse_args()
    token = os.getenv('PHOTOLIBRARY_TOKEN', os.getenv('DEV_TOKEN', 'local-development-only'))
    if token == 'local-development-only' and urlparse(args.base_url).hostname not in {'127.0.0.1','localhost'}:
        parser.error('Set PHOTOLIBRARY_TOKEN to a Cognito access token for remote use')
    if args.command != 'create-family' and not args.family:
        parser.error('--family is required')
    base = f'/a3/families/{args.family}'
    with httpx.Client(base_url=args.base_url, headers={'Authorization':'Bearer '+token}, timeout=90) as client:
        if args.command == 'create-family':
            result = client.post('/a3/families', json={'name':args.name})
        elif args.command == 'upload':
            if not 0 < args.file.stat().st_size <= 20 * 1024 * 1024:
                parser.error('Choose an image between 1 byte and 20 MiB')
            mime = mimetypes.guess_type(str(args.file))[0]
            receipt = client.post(base+'/uploads', json={'name':args.file.name,'mime':mime,'capturedDate':args.date})
            receipt.raise_for_status()
            receipt = receipt.json()
            if 'fields' in receipt['upload']:
                # Separate client prevents sending the user's bearer token to S3.
                with args.file.open('rb') as stream:
                    sent = httpx.post(receipt['upload']['url'], data=receipt['upload']['fields'],
                                      files={'file':(args.file.name, stream, mime)}, timeout=120)
                sent.raise_for_status()
                result = client.post(base+'/photos/'+receipt['photo']['photoId']+'/complete')
            else:
                result = client.put(receipt['upload']['url'], content=args.file.read_bytes())
        elif args.command == 'album':
            data = {'title':args.title,'filters':{'person':args.person} if args.person else {}}
            if args.prompt:
                data['prompt'] = args.prompt
            result = client.post(base+'/albums', json=data)
        elif args.command == 'memories':
            result = client.get(base+'/memories/on-this-day', params={'date':args.date})
        else:
            result = client.get(base+'/'+args.command)
        print(json.dumps(result.json(), indent=2))
        result.raise_for_status()


if __name__ == '__main__':
    main()

