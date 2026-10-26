const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = '../static/planner-core.js';
const core = fs.existsSync(require('node:path').join(__dirname, path)) ? require(path) : null;
function ready() { assert.ok(core, 'Planner core is not implemented yet'); return core; }

test('wall time uses selected IANA zone independently of device timezone', () => {
  const c = ready();
  assert.deepEqual(c.wallCandidates('2099-02-01T00:30', 'Asia/Vladivostok'), ['2099-01-31T14:30:00.000Z']);
  assert.equal(c.localTime('2099-01-31T14:30:00Z', 'Asia/Vladivostok'), '2099-02-01T00:30');
  assert.throws(() => c.wallCandidates('2099-02-01T00:30', ''), /пояс/);
  assert.throws(() => c.wallCandidates('2099-02-30T00:30', 'UTC'), /дат/);
});
test('DST gaps have no instant and folds expose both choices', () => {
  const c = ready();
  assert.deepEqual(c.wallCandidates('2027-03-14T02:30', 'America/New_York'), []);
  assert.deepEqual(c.wallCandidates('2027-11-07T01:30', 'America/New_York'),
    ['2027-11-07T05:30:00.000Z', '2027-11-07T06:30:00.000Z']);
});
test('title-only PATCH does not reschedule elapsed reminders', () => {
  const c = ready();
  const before={title:'Старое', goal_id:null,status:'not_started',due_at:'2020-01-01T12:00:00+00:00',timezone:'UTC',timezone_confirmed:true,remind_before_minutes:60};
  assert.deepEqual(c.taskChanges(before,{...before,title:'Новое',due_at:'2020-01-01T12:00:00.000Z'}), {title:'Новое'});
  assert.deepEqual(c.taskChanges(before,{...before,due_at:null,timezone:null,remind_before_minutes:null}),
    {due_at:null,timezone:null,timezone_confirmed:false,remind_before_minutes:null});
});
test('mutation keeps exact wire body and key after lost response and fresh bearer', async () => {
  const c = ready(); let token='old'; const seen=[];
  const client = new c.Client(()=>token,async (url, options)=>{
    seen.push({url,...options});
    if(seen.length===1) throw new TypeError('lost response');
    return {ok:true,status:201,json:async()=>({goal:{id:'same'}})};
  });
  const body={goal:{id:'same',title:'Цель'},tasks:[],confirmed:true};
  const op=client.operation('POST','/commit',body);body.goal.title='Changed externally';
  await assert.rejects(client.mutate(op), e=>e.retryable===true);
  token='new'; assert.deepEqual(await client.mutate(op), {goal:{id:'same'}});
  assert.equal(seen[0].body,seen[1].body);
  assert.equal(JSON.parse(seen[1].body).goal.title,'Цель');
  assert.equal(seen[0].headers['Idempotency-Key'],seen[1].headers['Idempotency-Key']);
  assert.equal(seen[1].headers.Authorization,'Bearer new');
});
test('structured errors distinguish auth, stale version, validation and server uncertainty', async () => {
  const c=ready();
  for(const [status,code,retryable] of [[401,'auth',true],[409,'stale_version',false],[422,'notification_in_past',false],[503,'unavailable',true]]) {
    const client=new c.Client(()=> 'test',async()=>({ok:false,status,json:async()=>({detail:{code}})}));
    await assert.rejects(client.mutate(client.operation('POST','/tasks',{})),e=>e.status===status && e.retryable===retryable && e.message.length>0);
  }
});
test('pagination keeps as_of and reads every page; text limits count Unicode points', async () => {
  const c=ready(); const urls=[];
  const client=new c.Client(()=> 'test',async url=>{urls.push(url);return {ok:true,status:200,json:async()=>urls.length===1?{tasks:[{id:1}],next_after:'next',as_of:'2099-01-01T00:00:00Z'}:{tasks:[{id:2}],next_after:null,as_of:'2099-01-01T00:00:00Z'}};});
  assert.deepEqual(await client.all('/tasks','tasks'), [{id:1},{id:2}]);
  assert.equal(new URL(urls[1],'https://local').searchParams.get('as_of'),'2099-01-01T00:00:00Z');
  assert.equal(c.title('🙂'.repeat(160),160),'🙂'.repeat(160));
  assert.throws(()=>c.title('🙂'.repeat(161),160),/160/);
  assert.throws(()=>client.operation('POST','/commit',{text:'Я'.repeat(13000)}),/больш/);
});
