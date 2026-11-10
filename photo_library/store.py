"""Shared record interface: SQLite locally; DynamoDB in AWS.

Every replacement is conditional on a revision. Keys always include a family.
"""
import json
from contextlib import contextmanager
import os
import sqlite3
from decimal import Decimal
import boto3
from botocore.exceptions import ClientError


class Conflict(Exception):
    pass


class LocalStore:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'library.sqlite'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS records (pk TEXT, sk TEXT, payload TEXT, rev INTEGER, PRIMARY KEY(pk,sk))')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, pk, sk):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM records WHERE pk=? AND sk=?', (pk, sk)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, pk, sk, value, expected=0):
        value = dict(value, rev=expected + 1)
        with self.connect() as db:
            try:
                if expected == 0:
                    db.execute('INSERT INTO records VALUES (?,?,?,?)', (pk, sk, json.dumps(value), 1))
                else:
                    result = db.execute('UPDATE records SET payload=?,rev=? WHERE pk=? AND sk=? AND rev=?',
                                        (json.dumps(value), expected + 1, pk, sk, expected))
                    if result.rowcount != 1:
                        raise Conflict('Concurrent update; retry the request')
            except sqlite3.IntegrityError as error:
                raise Conflict('Record already exists') from error
        return value

    def list(self, pk, prefix):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM records WHERE pk=? AND substr(sk,1,?)=? ORDER BY sk',
                              (pk, len(prefix), prefix)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def delete(self, pk, sk, expected):
        with self.connect() as db:
            result = db.execute('DELETE FROM records WHERE pk=? AND sk=? AND rev=?',
                                (pk, sk, int(expected)))
            if result.rowcount != 1:
                raise Conflict('Record changed before it could be deleted; refresh and retry')


class AwsStore:
    def __init__(self, settings):
        self.table = boto3.resource('dynamodb', region_name=settings.region).Table(os.environ['LIBRARY_TABLE'])
        self.layout = os.getenv('DYNAMODB_LAYOUT', 'pk-sk')
        if self.layout not in {'pk-sk', 'assignment2-classifications'}:
            raise ValueError('Unsupported DynamoDB layout')

    @staticmethod
    def _legacy_id(pk, sk):
        # JSON encoding is unambiguous even though both logical keys contain '#'.
        return json.dumps([pk, sk], separators=(',', ':'))

    @staticmethod
    def _legacy_payload(item):
        payload = item.get('payload') if item else None
        return dict(payload) if payload is not None else None

    def get(self, pk, sk):
        if self.layout == 'assignment2-classifications':
            item = self.table.get_item(Key={'imageId': self._legacy_id(pk, sk)}, ConsistentRead=True).get('Item')
            return self._legacy_payload(item)
        return self.table.get_item(Key={'pk': pk, 'sk': sk}, ConsistentRead=True).get('Item')

    def put(self, pk, sk, value, expected=0):
        if self.layout == 'assignment2-classifications':
            payload = json.loads(json.dumps(dict(value, rev=int(expected) + 1), default=float), parse_float=Decimal)
            item = {'imageId': self._legacy_id(pk, sk), 'userId': pk, 'classifiedAt': sk,
                    'payload': payload, 'rev': int(expected) + 1}
            args = {'Item': item, 'ConditionExpression': 'attribute_not_exists(imageId)' if expected == 0 else 'rev = :r'}
            if expected:
                args['ExpressionAttributeValues'] = {':r': expected}
            try:
                self.table.put_item(**args)
            except ClientError as error:
                if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                    raise Conflict('Concurrent update; retry the request') from error
                raise
            return dict(payload)
        item = json.loads(json.dumps(dict(value, pk=pk, sk=sk, rev=int(expected) + 1), default=float), parse_float=Decimal)
        args = {'Item': item, 'ConditionExpression': 'attribute_not_exists(pk)' if expected == 0 else 'rev = :r'}
        if expected:
            args['ExpressionAttributeValues'] = {':r': expected}
        try:
            self.table.put_item(**args)
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise Conflict('Concurrent update; retry the request') from error
            raise
        return item

    def list(self, pk, prefix):
        from boto3.dynamodb.conditions import Key
        if self.layout == 'assignment2-classifications':
            args = {'IndexName':'by-user-time',
                    'KeyConditionExpression':Key('userId').eq(pk) & Key('classifiedAt').begins_with(prefix)}
            result = []
            while True:
                page = self.table.query(**args)
                result.extend(self._legacy_payload(item) for item in page['Items'])
                if 'LastEvaluatedKey' not in page:
                    return result
                args['ExclusiveStartKey'] = page['LastEvaluatedKey']
        args = {'KeyConditionExpression': Key('pk').eq(pk) & Key('sk').begins_with(prefix), 'ConsistentRead': True}
        result = []
        while True:
            page = self.table.query(**args)
            result.extend(page['Items'])
            if 'LastEvaluatedKey' not in page:
                return result
            args['ExclusiveStartKey'] = page['LastEvaluatedKey']

    def delete(self, pk, sk, expected):
        key = {'imageId': self._legacy_id(pk, sk)} if self.layout == 'assignment2-classifications' else {'pk': pk, 'sk': sk}
        try:
            self.table.delete_item(Key=key, ConditionExpression='rev = :r',
                                   ExpressionAttributeValues={':r': int(expected)})
        except ClientError as error:
            if error.response['Error']['Code'] == 'ConditionalCheckFailedException':
                raise Conflict('Record changed before it could be deleted; refresh and retry') from error
            raise

