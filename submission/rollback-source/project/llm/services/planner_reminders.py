"""Requested planner reminders only. No AI, automatic POST retry or startup migration."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import logging
import re
from uuid import UUID
from pathlib import Path
import time
from uuid import uuid4

from ..channels.max import MaxError
from .planner_store import PlannerStore, apply_migration, notify_at
from .help_routes import bot_name, task_payload
from .planner_availability import record_bot_availability

logger = logging.getLogger(__name__)
DELIVERY_MIGRATION = Path(__file__).resolve().parents[2] / 'deploy/migrations/003_planner_delivery.sql'


async def apply_delivery_migration(pool):
    await apply_migration(pool)
    raw = DELIVERY_MIGRATION.read_bytes(); digest = hashlib.sha256(raw).hexdigest()
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute('SELECT pg_advisory_xact_lock(78738101)')
            old = await c.fetchval('SELECT sha256 FROM schema_migrations WHERE version=$1', DELIVERY_MIGRATION.stem)
            if old:
                if old != digest:
                    raise RuntimeError('Planner delivery migration checksum mismatch')
                return
            await c.execute(raw.decode())
            await c.execute('INSERT INTO schema_migrations(version,sha256) VALUES($1,$2)', DELIVERY_MIGRATION.stem, digest)


class PlannerReminders:
    def __init__(self, pool, transport, *, allowed_owners, max_lateness_seconds,
                 clock=None, batch=5, lease_seconds=30, send_timeout=10, heartbeat_path=None):
        owners = frozenset(str(owner) for owner in allowed_owners)
        if (not owners or len(owners)>10 or any(not o.isascii() or not o.isdecimal()
                or not 0<int(o)<2**63 or str(int(o))!=o for o in owners)):
            raise ValueError('Explicit finite planner owner allowlist required')
        if max_lateness_seconds != 3600 or not 1<=batch<=5 or not 0<send_timeout<lease_seconds<=60:
            raise ValueError('Invalid planner delivery bounds or unapproved lateness policy')
        self.pool, self.transport = pool, transport
        self.allowed_owners, self.max_lateness = owners, timedelta(seconds=max_lateness_seconds)
        self.clock, self.batch = clock, batch
        self.lease = timedelta(seconds=lease_seconds); self.send_timeout = send_timeout
        self.heartbeat_path = Path(heartbeat_path) if heartbeat_path else None
        self.startup_ready = None
        self.stopping = asyncio.Event(); self.health = {'status':'starting','updated_at':time.time()}

    @asynccontextmanager
    async def owner(self, owner):
        async with self.pool.acquire(timeout=3) as c:
            async with c.transaction():
                await c.execute("SET LOCAL lock_timeout='2s'")
                await c.execute("SET LOCAL statement_timeout='3s'")
                await c.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', owner)
                yield c

    async def now(self, c):
        return self.clock() if self.clock else await c.fetchval('SELECT clock_timestamp()')

    async def check_schema(self):
        async with self.pool.acquire(timeout=3) as c:
            digest = await c.fetchval('SELECT sha256 FROM schema_migrations WHERE version=$1', DELIVERY_MIGRATION.stem)
        if digest != hashlib.sha256(DELIVERY_MIGRATION.read_bytes()).hexdigest():
            raise RuntimeError('Apply and verify planner delivery migration explicitly before enabling')

    async def locked(self, c, owner, task_id, identifier):
        epoch = await c.fetchrow('SELECT epoch,planner_stopped FROM account_epochs WHERE owner=$1', owner)
        task = await c.fetchrow('SELECT * FROM planner_tasks WHERE owner=$1 AND id=$2 FOR UPDATE', owner, task_id)
        row = await c.fetchrow('SELECT * FROM planner_notifications WHERE owner=$1 AND id=$2 FOR UPDATE', owner, identifier)
        return epoch, task, row

    @staticmethod
    def valid(epoch, task, row):
        return bool(task and row and not (epoch and epoch['planner_stopped'])
                    and row['account_epoch']==task['account_epoch']==(epoch['epoch'] if epoch else 0)
                    and row['generation']==task['schedule_generation'] and row['task_version']==task['version']
                    and task['status']!='done' and notify_at(task)==row['notify_at'])

    async def terminal(self, c, row, state, now, code=None):
        await c.execute('''UPDATE planner_notifications SET state=$3,finished_at=$4,error_code=$5,
            lease_until=NULL WHERE owner=$1 AND id=$2''', row['owner'], row['id'], state, now, code)

    async def claim(self):
        async with self.pool.acquire(timeout=3) as c:
            now = await self.now(c)
            candidates = await c.fetch('''SELECT owner,id,task_id FROM planner_notifications
                WHERE owner=ANY($1::text[]) AND (
                (state='pending' AND notify_at<=$2) OR
                (state='claimed' AND lease_until<=$2) OR
                (state='sending' AND (lease_until IS NULL OR lease_until<=$2)))
                ORDER BY notify_at,owner,id LIMIT $3''', list(self.allowed_owners), now, self.batch*4)
        tickets = []
        for candidate in candidates:
            async with self.owner(candidate['owner']) as c:
                now = await self.now(c)
                epoch, task, row = await self.locked(c, candidate['owner'], candidate['task_id'], candidate['id'])
                if not row:
                    continue
                if row['state']=='sending':
                    if row['lease_until'] is None or row['lease_until']<=now:
                        await self.terminal(c,row,'unknown',now,'interrupted_send')
                    continue
                if row['state'] not in {'pending','claimed'} or row['notify_at']>now:
                    continue
                if row['state']=='claimed' and row['lease_until']>now:
                    continue
                if not self.valid(epoch,task,row):
                    await self.terminal(c,row,'cancelled',now)
                    continue
                if now-row['notify_at']>self.max_lateness:
                    await self.terminal(c,row,'missed',now)
                    continue
                token = uuid4()
                await c.execute('''UPDATE planner_notifications SET state='claimed',claim_token=$3,
                    lease_until=$4 WHERE owner=$1 AND id=$2''', row['owner'],row['id'],token,now+self.lease)
                tickets.append(dict(owner=row['owner'],id=row['id'],task_id=row['task_id'],claim_token=token))
            if len(tickets)>=self.batch:
                break
        return tickets

    async def prepare(self, ticket):
        if ticket['owner'] not in self.allowed_owners:
            return None
        async with self.owner(ticket['owner']) as c:
            now = await self.now(c)
            epoch, task, row = await self.locked(c,ticket['owner'],ticket['task_id'],ticket['id'])
            if not row or row['state']!='claimed' or row['claim_token']!=ticket['claim_token'] or row['lease_until']<=now:
                return None
            if not self.valid(epoch,task,row):
                await self.terminal(c,row,'cancelled',now)
                return None
            if now-row['notify_at']>self.max_lateness:
                await self.terminal(c,row,'missed',now)
                return None
            config = json.loads(await c.fetchval('''SELECT v.config FROM scenario_state s
                JOIN scenario_versions v ON v.id=s.published_id WHERE s.id=1'''))
            bot = bot_name(config)
            if not bot:
                await self.terminal(c,row,'failed',now,'app_unavailable')
                return None
            message = {'text':'Напоминание о деле\n\n'+task['title'], 'attachments':[
                {'type':'inline_keyboard','payload':{'buttons':[
                    [{'type':'callback','text':'Готово','payload':f"p1d_{row['id'].hex}_{row['generation']}"}],
                    [{'type':'open_app','text':'Перенести','web_app':bot,'payload':'move_'+str(task['id'])}],
                    [{'type':'open_app','text':'Открыть задачу','web_app':bot,'payload':task_payload(task['id'])}]
                ]}}]}
            await c.execute('''UPDATE planner_notifications SET state='sending',sending_at=$3,
                lease_until=$4 WHERE owner=$1 AND id=$2''',row['owner'],row['id'],now,now+self.lease)
            return message

    async def finish(self,ticket,state,code=None,message_id=None):
        async with self.owner(ticket['owner']) as c:
            # Lock task before notification even when finishing a protected request.
            await c.fetchval('SELECT id FROM planner_tasks WHERE owner=$1 AND id=$2 FOR UPDATE',ticket['owner'],ticket['task_id'])
            await c.execute('''UPDATE planner_notifications SET state=$4,finished_at=$5,error_code=$6,
                message_id=$7,lease_until=NULL WHERE owner=$1 AND id=$2 AND claim_token=$3 AND state='sending' ''',
                ticket['owner'],ticket['id'],ticket['claim_token'],state,await self.now(c),code,message_id)

    async def deliver(self,ticket):
        if self.startup_ready is not None and not self.startup_ready.is_set():
            return
        body = await self.prepare(ticket)
        if body is None:
            return
        state, code, mid = 'unknown', None, None
        try:
            async with asyncio.timeout(self.send_timeout):
                result = await self.transport.send(int(ticket['owner']),body)
            value = ((result.get('message') or {}).get('body') or {}).get('mid') if isinstance(result,dict) else None
            if isinstance(value,str) and value:
                state,mid = 'delivered',value[:256]
            else:
                code = 'unconfirmed_response'
        except MaxError as error:
            # Explicit rejection never receives an automatic retry either.
            state = 'failed' if error.status in {400,401,403,404,429} else 'unknown'
            code = 'http_'+str(error.status) if error.status else error.code
        except asyncio.CancelledError:
            await asyncio.shield(self.finish(ticket,'unknown','interrupted_send'))
            raise
        except Exception:
            code = 'transport_uncertain'
        await self.finish(ticket,state,code,mid)

    async def set_stopped(self, owner, stopped):
        return await record_bot_availability(self.pool,owner,stopped)

    async def complete_callback(self, owner, payload):
        if owner not in self.allowed_owners:
            return 'unavailable'
        match = re.fullmatch(r'p1d_([0-9a-f]{32})_([1-9][0-9]{0,18})',payload) if isinstance(payload,str) else None
        if not match:
            return 'unavailable'
        identifier, generation = UUID(hex=match[1]), int(match[2])
        async with self.owner(owner) as c:
            # Read pointer without a row lock, then acquire task before notification.
            task_id = await c.fetchval('SELECT task_id FROM planner_notifications WHERE owner=$1 AND id=$2',owner,identifier)
            if not task_id:
                return 'unavailable'
            epoch,task,row = await self.locked(c,owner,task_id,identifier)
            if not row or not task or (epoch and epoch['planner_stopped']):
                return 'unavailable'
            if row['account_epoch']!=task['account_epoch'] or task['account_epoch']!=(epoch['epoch'] if epoch else 0):
                return 'stale'
            if generation!=row['generation'] or row['state'] not in {'sending','delivered','unknown'}:
                return 'stale'
            if task['status']=='done':
                return 'done'
            if task['schedule_generation']!=generation:
                return 'stale'
            await PlannerStore()._change_task(c,owner,task_id,{'expected_version':task['version'],'status':'done'},False)
            return 'done'

    def heartbeat(self,status,error=None):
        self.health = {'status':status,'updated_at':time.time(),**({'error_type':type(error).__name__} if error else {})}
        if self.heartbeat_path:
            try:
                self.heartbeat_path.parent.mkdir(parents=True,exist_ok=True)
                temporary=self.heartbeat_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.health));temporary.replace(self.heartbeat_path)
            except OSError as error:
                logger.warning('planner_heartbeat_failed type=%s',type(error).__name__)

    async def tick(self):
        for ticket in await self.claim():
            await self.deliver(ticket)

    async def run(self):
        await self.check_schema()
        try:
            while not self.stopping.is_set():
                if self.startup_ready is not None and not self.startup_ready.is_set():
                    self.heartbeat('waiting_for_updates')
                    await self.startup_ready.wait()
                try:
                    self.heartbeat('running');await self.tick();self.heartbeat('running')
                except Exception as error:
                    logger.warning('planner_worker_failed type=%s',type(error).__name__)
                    self.heartbeat('error',error)
                try:
                    await asyncio.wait_for(self.stopping.wait(),timeout=2)
                except TimeoutError:
                    pass
        finally:
            self.heartbeat('stopped')


def configured_worker(pool, transport, values, *, heartbeat_path=None):
    if values.get('PLANNER_NOTIFICATIONS_ENABLED')!='1':
        return None
    if values.get('PLANNER_ENABLED')!='1' or values.get('PLANNER_MAX_LATENESS_SECONDS')!='3600':
        raise ValueError('Planner flag and approved one-hour policy must be explicit')
    owners = values.get('PLANNER_NOTIFICATION_OWNERS','').split(',')
    return PlannerReminders(pool,transport,allowed_owners=owners,max_lateness_seconds=3600,heartbeat_path=heartbeat_path)
