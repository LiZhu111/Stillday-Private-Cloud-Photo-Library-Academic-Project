"""Durable local task queue / SQS long-polling adapter with fair tenant groups."""
import json
from contextlib import contextmanager
import os
import sqlite3
import time
import uuid
from .aws import client


class LocalQueue:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'queue.sqlite'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT, available REAL, attempts INTEGER, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS dead_letters (id TEXT PRIMARY KEY, body TEXT, attempts INTEGER)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                yield db
        finally:
            db.close()

    def send(self, body):
        with self.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,0,NULL)', (uuid.uuid4().hex, json.dumps(body), time.time()))

    def receive(self, visibility, max_attempts):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,body,attempts FROM jobs WHERE available<=? ORDER BY available LIMIT 1', (time.time(),)).fetchone()
            if not row:
                return None
            job_id, body, attempts = row
            if attempts >= max_attempts:
                db.execute('INSERT OR REPLACE INTO dead_letters VALUES (?,?,?)', (job_id, body, attempts))
                db.execute('DELETE FROM jobs WHERE id=?', (job_id,))
                return None
            receipt = uuid.uuid4().hex
            db.execute('UPDATE jobs SET available=?,attempts=attempts+1,receipt=? WHERE id=?', (time.time()+visibility, receipt, job_id))
        return {'body': json.loads(body), 'receipt': receipt, 'attempts': attempts + 1}

    def extend(self, receipt, seconds):
        with self.connect() as db:
            db.execute('UPDATE jobs SET available=? WHERE receipt=?', (time.time()+seconds, receipt))

    def ack(self, receipt):
        with self.connect() as db:
            db.execute('DELETE FROM jobs WHERE receipt=?', (receipt,))


class AwsQueue:
    def __init__(self):
        self.url = os.environ['PROCESSING_QUEUE_URL']

    def send(self, body):
        client('sqs').send_message(QueueUrl=self.url, MessageBody=json.dumps(body), MessageGroupId=body['familyId'])

    def receive(self, visibility, max_attempts):
        result = client('sqs').receive_message(QueueUrl=self.url, MaxNumberOfMessages=1,
            WaitTimeSeconds=20, VisibilityTimeout=visibility, MessageSystemAttributeNames=['ApproximateReceiveCount'])
        if not result.get('Messages'):
            return None
        message = result['Messages'][0]
        return {'body': json.loads(message['Body']), 'receipt': message['ReceiptHandle'],
                'attempts': int(message['Attributes']['ApproximateReceiveCount'])}

    def extend(self, receipt, seconds):
        client('sqs').change_message_visibility(QueueUrl=self.url, ReceiptHandle=receipt, VisibilityTimeout=seconds)

    def ack(self, receipt):
        client('sqs').delete_message(QueueUrl=self.url, ReceiptHandle=receipt)

