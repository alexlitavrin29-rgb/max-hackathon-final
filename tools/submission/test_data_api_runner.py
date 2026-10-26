"""Contract substitutions and public/full mode boundaries, without network calls."""
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch
from uuid import UUID

from tools.submission.check_api import extract, run, substitute


class SubstituteTests(TestCase):
    def test_whole_placeholder_preserves_json_type(self):
        values={'version':3,'confirmed':True,'empty':None,'number':'03'}
        self.assertEqual(substitute('${version}',values),3)
        self.assertIs(substitute('${confirmed}',values),True)
        self.assertIsNone(substitute('${empty}',values))
        self.assertEqual(substitute('${number}',values),'03')

    def test_embedded_placeholder_is_text(self):
        self.assertEqual(substitute('run-${version}:дата',{'version':3}),'run-3:дата')

    def test_nested_values_preserve_keys(self):
        result=substitute({'${literal-key}':[{'expected_version':'${v}'},'task-${id}']},{'v':2,'id':'abc'})
        self.assertEqual(result,{'${literal-key}':[{'expected_version':2},'task-abc']})

    def test_missing_variable_fails_before_request(self):
        with self.assertRaises(KeyError):substitute('${missing}',{})

    def test_extract_nested_array(self):
        self.assertEqual(extract({'tasks':[{'version':3}]},'$.tasks[0].version'),3)

    def test_extract_rejects_expressions(self):
        with self.assertRaises(ValueError):extract({},'$.__class__()')


class ModeTests(TestCase):
    def test_public_mode_skips_user_writes_and_cleanup(self):
        config={'api':{},'checks':[{'id':'write','role':'user','method':'POST','path':'/api/planner/commit'}],
                'cleanup':[{'id':'delete','role':'user','method':'DELETE','path':'/api/account'}]}
        with patch('tools.submission.check_api.urlopen') as request:
            result=run(config,'https://example.invalid')
        request.assert_not_called()
        self.assertTrue(result['ok'])
        self.assertEqual(result['results'][0]['id'],'write')
        self.assertIn('skipped',result['results'][0])

    def test_full_mode_rejects_public_host_before_network(self):
        with patch('tools.submission.check_api.urlopen') as request:
            with self.assertRaisesRegex(ValueError,'loopback'):
                run({'checks':[]},'https://135-106-229-210.sslip.io','synthetic-only',True)
        request.assert_not_called()

    def test_full_mode_generates_distinct_uuid_values(self):
        config={'api':{},'checks':[{'id':'uuid','role':'user','method':'POST','path':'/test',
                'request':{'body':{'id':'${goalId}','task':'${taskId}','key':'${runId}'}},
                'expected':{'statusCodes':[200]}}]}
        class Response(BytesIO):
            status=200
            headers={'Content-Type':'application/json'}
        with patch('tools.submission.check_api.time.sleep'), \
             patch('tools.submission.check_api.urlopen',return_value=Response(b'{}')) as request:
            result=run(config,'http://127.0.0.1:18976','synthetic-only',True)
        import json
        values=list(json.loads(request.call_args.args[0].data).values())
        self.assertEqual(len(set(values)),3)
        for value in values:self.assertEqual(str(UUID(value)),value)
        self.assertTrue(result['ok'])

    def test_response_expectations_use_the_same_generated_uuid(self):
        config={'api':{},'checks':[{'id':'uuid','role':'user','method':'POST','path':'/test',
                'request':{'body':{'id':'${goalId}'}},
                'expected':{'statusCodes':[200],'bodySchema':{'type':'object',
                    'properties':{'id':{'const':'${goalId}'}}}}}]}
        class Response(BytesIO):
            status=200
            headers={'Content-Type':'application/json'}
        def echo(request,**_):return Response(request.data)
        with patch('tools.submission.check_api.time.sleep'), \
             patch('tools.submission.check_api.urlopen',side_effect=echo):
            result=run(config,'http://127.0.0.1:18976','synthetic-only',True)
        self.assertTrue(result['ok'])
