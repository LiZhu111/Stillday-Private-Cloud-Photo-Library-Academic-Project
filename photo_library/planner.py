"""A2 Bedrock Converse reuse, restricted to non-destructive album filters."""
from datetime import date
import json
import os
from .aws import client


def validate_filters(value):
    allowed = {'person', 'since', 'until', 'orientation', 'limit', 'minSharpness'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unsupported album filters')
    result = dict(value)
    for key in ('since', 'until'):
        if key in result:
            result[key] = date.fromisoformat(result[key]).isoformat()
    if result.get('since', '') > result.get('until', '9999-12-31'):
        raise ValueError('Invalid date range')
    if 'person' in result and (not isinstance(result['person'], str) or not 1 <= len(result['person']) <= 100):
        raise ValueError('Invalid person label')
    if 'orientation' in result and result['orientation'] not in {'portrait', 'landscape', 'square'}:
        raise ValueError('Invalid orientation')
    count = result.setdefault('limit', 30)
    if type(count) is not int or not 1 <= count <= 100:
        raise ValueError('Album limit must be 1-100')
    if 'minSharpness' in result and (type(result['minSharpness']) not in (int, float) or
                                    not 0 <= result['minSharpness'] <= 255):
        raise ValueError('Invalid sharpness threshold')
    return result


def plan(prompt):
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2000:
        raise ValueError('Prompt must contain 1-2000 characters')
    if not os.getenv('CHAT_MODEL_ID'):
        raise RuntimeError('Natural-language planning is not configured; use explicit filters')
    response = client('bedrock-runtime').converse(modelId=os.environ['CHAT_MODEL_ID'],
        system=[{'text': 'Return only a JSON object containing supported photo album filters: '
                 'person (an exact user-supplied label), since/until (YYYY-MM-DD), '
                 'orientation (portrait/landscape/square), limit (1-100), minSharpness (0-255). '
                 'Do not invent a person or date. Never return identities, URLs, tools or deletion actions. '
                 'If the request cannot be expressed with these fields, return {"unsupported": true}.'}],
        messages=[{'role': 'user', 'content': [{'text': prompt}]}],
        inferenceConfig={'maxTokens': 300, 'temperature': 0})
    text = ''.join(x.get('text', '') for x in response['output']['message']['content'])
    return validate_filters(json.loads(text))

