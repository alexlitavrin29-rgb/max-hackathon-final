"""Bind server-selected required searches to the signed MAX owner and epoch."""
import hashlib
import hmac
import json
import os

RESOURCES = {'app_work', 'app_rental', 'app_help'}


def policy_bytes(owner, epoch, question, resources):
    if (not isinstance(resources, list) or len(resources) > 3
            or any(not isinstance(item, str) or item not in RESOURCES for item in resources)
            or len(set(resources)) != len(resources)):
        raise ValueError('Invalid search policy')
    return json.dumps(['planner-search-policy-v1', str(owner), int(epoch), question, resources],
                      ensure_ascii=False, separators=(',', ':')).encode()


def issue_policy(owner, epoch, question, resources):
    key = os.getenv('MAX_BOT_TOKEN', '')
    if not key:
        raise ValueError('Missing server signing configuration')
    return hmac.new(key.encode(), policy_bytes(owner, epoch, question, resources), hashlib.sha256).hexdigest()


def verify_policy(owner, epoch, context):
    resources = context.get('required_resources', [])
    supplied = context.get('policy')
    if not resources and supplied is None:
        return []
    expected = issue_policy(owner, epoch, context['question'], resources)
    if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
        raise ValueError('Invalid search policy')
    return resources
