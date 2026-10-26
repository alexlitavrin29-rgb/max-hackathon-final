"""Run component-correct tests on an internal Docker network and disposable PostgreSQL."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

root=Path(__file__).resolve().parents[2]
project='tochka-acceptance-v2-audit-20261026'
compose=['docker','compose','-p',project,'--env-file','submission/.env','-f','submission/compose.yaml']
dsn=os.getenv('PLANNER_TEST_DSN','postgresql://support_router:CHANGE_ME_LOCAL_ONLY@postgres/support_router')
address=urlsplit(dsn)
if address.scheme!='postgresql' or (address.hostname,address.path)!=('postgres','/support_router'):
    raise SystemExit('Use only the disposable local acceptance PostgreSQL service')
rollback=root/'submission/rollback-source'
manifest=json.loads((rollback/'PROVENANCE.json').read_text(encoding='utf-8'))
for name,digest in manifest['files'].items():
    if hashlib.sha256((rollback/name).read_bytes()).hexdigest()!=digest:
        raise SystemExit('Historical rollback fixture checksum mismatch: '+name)
bot_cases=[
 'project/admin/tests/test_orphan_payments.py::test_max_menu_uses_same_cards_and_preserves_configuration',
 'project/admin/tests/test_orphan_payments.py::test_max_callbacks_open_new_card_and_return',
 'project/admin/tests/test_benefits_navigation.py::test_max_categories_share_answers_and_keep_back_navigation',
 'project/admin/tests/test_help_points.py::test_child_menu_replaces_reminder_section_only_for_child']
clean=['env','-u','LLM_PROVIDER','-u','LLM_BASE_URL','-u','LLM_MODEL','-u','LLM_TIMEOUT_SECONDS','-u','PLANNER_ENABLED','-u','PLANNER_AI_ENABLED','-u','LLM_ENABLED']
def output(args):return subprocess.check_output(args,cwd=root,text=True,encoding='utf-8').strip()
subprocess.run(compose+['build','seed','bot'],cwd=root,check=True)
config=json.loads(output(compose+['--profile','bot','config','--format','json']))
postgres=output(compose+['ps','-q','postgres'])
if not postgres:raise SystemExit('Start the disposable Compose stack first')
network=project+'-isolated-tests'
subprocess.run(['docker','network','create','--internal',network],cwd=root,check=True,stdout=subprocess.DEVNULL)
connected=False
try:
    subprocess.run(['docker','network','connect','--alias','postgres',network,postgres],cwd=root,check=True)
    connected=True
    base=['docker','run','--rm','--network',network,'--volume',str(rollback.resolve())+':/rollback:ro',
     '-e','MAX_BOT_TOKEN=local-preview-no-max-access','-e','HELP_API_URL=','-e','HELP_CITY_FIRST=',
     '-e','TRUDVSEM_CACHE_PATH=','-e','REEF_CACHE_PATH=','-e','PLANNER_TEST_DSN='+dsn,
     '-e','PLANNER_ROLLBACK_ROOT=/rollback']
    main=subprocess.run(base+[config['services']['miniapp']['image'],*clean,'python','-m','pytest','-q','-rs','-p','no:cacheprovider',
     'project/llm/tests','project/admin/tests','project/miniapp/tests','tools/submission/test_backup_retention.py','tools/submission/test_data_api_runner.py',
     *['--deselect='+case for case in bot_cases],'--basetemp=/tmp/acceptance-components'],cwd=root)
    bot=subprocess.run(base+[config['services']['bot']['image'],*clean,'python','-m','pytest','-q','-rs','-p','no:cacheprovider',*bot_cases,
     '--basetemp=/tmp/acceptance-bot-components'],cwd=root)
finally:
    if connected:subprocess.run(['docker','network','disconnect',network,postgres],cwd=root,check=True)
    subprocess.run(['docker','network','rm',network],cwd=root,check=True,stdout=subprocess.DEVNULL)
raise SystemExit(0 if main.returncode==bot.returncode==0 else 1)
