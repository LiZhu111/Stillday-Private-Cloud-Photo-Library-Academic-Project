"""Preserve existing vectors while adding orientation from authoritative photo records.

Defaults to a dry run. Applying requires idle workers and an empty processing queue.
Backups contain private vector metadata and stay outside the source submission.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
ORIENTATIONS = {'landscape', 'portrait', 'square'}


def prepare_update(vector, record):
    metadata = vector.get('metadata') or {}
    if not record or not metadata.get('familyId') or metadata.get('photoId') != vector['key']:
        return None
    if record.get('familyId') != metadata['familyId'] or record.get('photoId') != vector['key']:
        return None
    if record.get('status') not in {'READY', 'TRASHED'}:
        return None
    width, height = record.get('width'), record.get('height')
    orientation = record.get('orientation')
    if width and height:
        orientation = 'landscape' if width > height else 'portrait' if height > width else 'square'
    if orientation not in ORIENTATIONS or metadata.get('orientation') == orientation:
        return None
    return {'key': vector['key'], 'data': vector['data'], 'metadata': dict(metadata, orientation=orientation)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--profile', default='CAB432-STUDENT-000000000000')
    parser.add_argument('--parameters', type=Path, default=ROOT / 'infrastructure/parameters.local.json')
    args = parser.parse_args()
    params = {item['ParameterKey']: item['ParameterValue'] for item in json.loads(args.parameters.read_text(encoding='utf-8-sig'))}
    assert params['LibraryTable'] == 'n00000000-a2-classifications'
    assert params['VectorBucketName'] == 'n00000000-a2-photo-vectors'
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-2')
    assert session.client('sts').get_caller_identity()['Account'] == '000000000000'
    vectors = session.client('s3vectors')
    table = session.resource('dynamodb').Table(params['LibraryTable'])
    reference = {'vectorBucketName': params['VectorBucketName'], 'indexName': params['VectorIndexName']}
    index = vectors.get_index(**reference)['index']
    if 'orientation' in index.get('metadataConfiguration', {}).get('nonFilterableMetadataKeys', []):
        raise RuntimeError('Orientation must be filterable before migration')

    def assert_idle():
        services = session.client('ecs').describe_services(cluster=params['ExistingClusterName'], services=['n00000000-a3-worker'])
        if services.get('failures') or any(s['runningCount'] or s['desiredCount'] for s in services['services']):
            raise RuntimeError('Wait for image workers to become idle before migrating')
        counts = session.client('sqs').get_queue_attributes(QueueUrl=params['ProcessingQueueUrl'], AttributeNames=[
            'ApproximateNumberOfMessages', 'ApproximateNumberOfMessagesNotVisible', 'ApproximateNumberOfMessagesDelayed'])['Attributes']
        if any(int(value) for value in counts.values()):
            raise RuntimeError('Wait for the processing queue to become empty before migrating')

    def photo(vector):
        metadata = vector.get('metadata') or {}
        family = metadata.get('familyId')
        if not family or metadata.get('photoId') != vector['key']:
            return None
        key = json.dumps(['FAMILY#' + family, 'PHOTO#' + vector['key']], separators=(',', ':'))
        return table.get_item(Key={'imageId': key}, ConsistentRead=True).get('Item', {}).get('payload')

    if args.apply:
        assert_idle()
    arguments = dict(reference, maxResults=100, returnData=True, returnMetadata=True)
    planned = []
    scanned = 0
    while True:
        page = vectors.list_vectors(**arguments)
        for vector in page.get('vectors', []):
            scanned += 1
            update = prepare_update(vector, photo(vector))
            if update is not None:
                planned.append((vector, update))
        if not page.get('nextToken'):
            break
        arguments['nextToken'] = page['nextToken']
    summary = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'apply': args.apply,
               'scanned': scanned, 'planned': len(planned), 'updated': 0, 'verified': 0}
    if args.apply and planned:
        backup_dir = ROOT / 'evidence/private'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / ('vector-orientation-before-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
        backup.write_text(json.dumps([old for old, new in planned]), encoding='utf-8')
        for old, update in planned:
            assert_idle()
            current = vectors.get_vectors(**reference, keys=[old['key']], returnData=True, returnMetadata=True)['vectors']
            if len(current) != 1 or any(current[0].get(k) != old.get(k) for k in ('key', 'data', 'metadata')):
                raise RuntimeError('A vector changed during migration; rerun after processing stops')
            if prepare_update(current[0], photo(current[0])) != update:
                raise RuntimeError('A photo changed during migration; rerun after processing stops')
            vectors.put_vectors(**reference, vectors=[update])
            summary['updated'] += 1
            actual = vectors.get_vectors(**reference, keys=[old['key']], returnData=True, returnMetadata=True)['vectors'][0]
            if any(actual.get(k) != update.get(k) for k in ('key', 'data', 'metadata')):
                raise RuntimeError('Vector verification failed; inspect the private backup')
            summary['verified'] += 1
    if args.apply:
        (ROOT / 'evidence/vector-orientation-migration.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
