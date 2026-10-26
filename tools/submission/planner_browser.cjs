/* Real browser + frozen K1 API + isolated PostgreSQL. All identities/text synthetic. */
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const {createHmac,randomUUID}=require('node:crypto');
const fs=require('node:fs');
const http=require('node:http');
const base=process.env.K2_BROWSER_URL||'http://127.0.0.1:18869';
const evidence=process.env.K2_EVIDENCE_DIR;
function signed(owner,start='', timezone='Asia/Tokyo', prepare=null) {
  const values={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:owner})};
  if(start)values.start_param=start;
  const key=createHmac('sha256','WebAppData').update('planner-k0-synthetic-only').digest();
  const text=Object.keys(values).sort().map(k=>`${k}=${values[k]}`).join('\n');
  return new URLSearchParams({...values,hash:createHmac('sha256',key).update(text).digest('hex')}).toString();
}
async function data(page,path,method='GET',body) {
  return page.evaluate(async ({path,method,body})=>{
    const r=await fetch('/api/planner'+path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()},...(body?{body:JSON.stringify(body)}:{})});
    return {status:r.status,body:await r.json()};
  },{path,method,body});
}
async function fillTask(page,title) {await page.getByLabel('Название дела',{exact:true}).fill(title);}
async function save(page) {await page.getByRole('button',{name:'Сохранить',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});}
async function newTask(page,title) {await page.getByRole('button',{name:'Добавить дело',exact:true}).click();await fillTask(page,title);await save(page);}
async function run() {
  // Managed Docker does not publish TCP ports to the executor. Forward only this
  // local test app's shared Unix socket; never route external destinations.
  const proxy=http.createServer((req,res)=>{
    const upstream=http.request({socketPath:process.env.K2_BROWSER_SOCKET||'/workspace/private/k2-runtime/server.sock',path:req.url,method:req.method,headers:req.headers},reply=>{
      res.writeHead(reply.statusCode,reply.headers);reply.pipe(res);
    });
    upstream.on('error',()=>{res.writeHead(502);res.end('Local test server unavailable');});req.pipe(upstream);
  });
  await new Promise(resolve=>proxy.listen(18869,'127.0.0.1',resolve));
  const browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  let owner=920000000000+Math.floor(Math.random()*100000);
  const checks=[];
  async function scenario(name, fn, start='', timezone='Asia/Tokyo', prepare=null) {
    if(process.env.K2_SCENARIO && !name.includes(process.env.K2_SCENARIO))return;
    const context=await browser.newContext({viewport:{width:390,height:844},timezoneId:timezone,colorScheme:process.env.K2_COLOR_SCHEME||'light',locale:'ru-RU'});
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    page.setDefaultTimeout(12000);
    page.on('requestfailed',r=>{if(!r.url().startsWith('https://st.max.ru/'))console.error('request failed',new URL(r.url()).pathname,r.failure()?.errorText);});
    await context.addInitScript(init=>{window.WebApp={initData:init,BackButton:{show(){},hide(){},onClick(fn){window.testBack=fn;}},shareMaxContent(payload){window.testShared=payload;return Promise.resolve();}};},signed(++owner,start));
    await page.route('https://st.max.ru/**',r=>r.abort());
    try {
      if(prepare)await prepare(page,context);
      await page.goto(base);await page.getByRole('button',{name:'Главная',exact:true}).waitFor();
      await page.waitForFunction(()=>!document.getElementById('tab-home').disabled);
      if(!start){await page.getByRole('button',{name:'Мой план',exact:true}).click();
      await page.getByText('Пока нет целей и дел.',{exact:true}).waitFor();}
      await fn(page,context);
      assert.deepEqual(errors,[]);checks.push(name);console.log('PASS '+name);
    } catch(e) {
      if(evidence){await page.screenshot({path:evidence+'/failure.png',fullPage:true});fs.writeFileSync(evidence+'/failure.txt',await page.locator('body').innerText());}
      if(errors.length)console.error('Browser script errors:',errors);
      throw e;
    } finally {await context.close();}
  }
  try {
    const movePointer=randomUUID();
    await scenario('K4 signed move pointer opens exact authorized move form',async page=>{
      await page.getByRole('heading',{name:'Перенести дело',exact:true}).waitFor();
      await page.locator('#plan-dialog').getByText('Дело для переноса',{exact:true}).waitFor();
      const before=(await data(page,'/tasks/'+movePointer)).body.task;
      await page.getByLabel('Что перенести',{exact:true}).selectOption('notification');
      await page.getByLabel('Дата и время',{exact:true}).fill('2099-03-02T12:00');await save(page);
      const after=(await data(page,'/tasks/'+movePointer)).body.task;
      assert.equal(after.due_at,before.due_at);assert.equal(Date.parse(after.notification_override_at),Date.parse('2099-03-02T03:00:00Z'));
    },'move_'+movePointer,'Asia/Tokyo',async (page,context)=>{
      await page.route('**/api/session',async route=>{
        const response=await route.fetch();const launch=await response.json();
        const seeded=await context.request.post(base+'/api/planner/tasks',{headers:{Authorization:'Bearer '+launch.session,'Idempotency-Key':randomUUID()},data:{id:movePointer,title:'Дело для переноса',due_at:'2099-03-01T10:00:00Z',timezone:'UTC',timezone_confirmed:true,remind_before_minutes:0}});
        assert.equal(seeded.status(),201);await route.fulfill({response});
      });
    });
    await scenario('K4 foreign move pointer reveals no task or form',async page=>{
      await page.locator('#plan-error').waitFor({state:'visible'});
      assert.equal(await page.locator('#plan-dialog').isVisible(),false);
      assert.equal(await page.getByText('Дело для переноса',{exact:true}).count(),0);
    },'move_'+movePointer);
    await scenario('K3 manual reference catalog and server-owned task return link',async page=>{
      await newTask(page,'Подготовить документы');
      const task=(await data(page,'/tasks')).body.tasks[0];
      await page.getByRole('button',{name:'Справка по делу «Подготовить документы»',exact:true}).click();
      const links=page.locator('#plan-dialog a');await page.getByRole('link',{name:'Ссылка на это дело',exact:true}).waitFor();
      assert.equal(await links.count(),10);
      assert.match(await page.getByRole('link',{name:'Документы',exact:true}).getAttribute('href'),/\?start=r1_documents$/);
      assert.match(await page.getByRole('link',{name:'Ссылка на это дело',exact:true}).getAttribute('href'),new RegExp('\\?startapp=task_'+task.id+'$'));
      assert.equal(await page.getByRole('button',{name:'Сохранить',exact:true}).count(),0);
      assert.equal((await data(page,'/tasks/'+task.id)).body.task.version,1);
      if(evidence)await page.screenshot({path:evidence+'/references.png',fullPage:true});
      await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();
    });
    const pointed=randomUUID();
    await scenario('K3 signed task pointer opens own task without writes',async page=>{
      await page.getByLabel('Название дела',{exact:true}).waitFor();
      assert.equal(await page.getByLabel('Название дела',{exact:true}).inputValue(),'Своя задача по ссылке');
      const task=(await data(page,'/tasks/'+pointed)).body.task;assert.equal(task.version,1);
      await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();
      await page.getByRole('heading',{name:'Своя задача по ссылке',exact:true}).waitFor();
    },'task_'+pointed,'Europe/Moscow',async (page,context)=>{
      await page.route('**/api/session',async route=>{
        const response=await route.fetch();const launch=await response.json();
        const seeded=await context.request.post(base+'/api/planner/tasks',{headers:{Authorization:'Bearer '+launch.session,'Idempotency-Key':randomUUID()},data:{id:pointed,title:'Своя задача по ссылке'}});
        assert.equal(seeded.status(),201);await route.fulfill({response});
      });
    });
    await scenario('K3 foreign or missing task pointer gives no private data',async page=>{
      await page.locator('#plan-error').waitFor({state:'visible'});
      assert.equal(await page.locator('#plan-dialog').isVisible(),false);
      assert.equal((await data(page,'/tasks')).body.tasks.length,0);
      assert.equal(await page.getByText('Своя задача по ссылке',{exact:true}).count(),0);
    },'task_'+pointed);
    await scenario('K3 generic signed plan pointer opens planner',async page=>{
      await page.getByText('Пока нет целей и дел.',{exact:true}).waitFor();
      assert.equal(await page.locator('#tab-plan').getAttribute('aria-current'),'page');
    },'plan');
    await scenario('manual CRUD, goals, four groups, progress and nonempty delete',async page=>{
      await newTask(page,'Самостоятельное дело');
      let rows=(await data(page,'/tasks')).body.tasks;assert.equal(rows.length,1);assert.equal(rows[0].goal_id,null);
      await page.getByRole('button',{name:'Создать цель',exact:true}).click();
      await page.getByLabel('Название цели',{exact:true}).fill('Первая цель');
      await page.getByRole('button',{name:'Подтвердить план',exact:true}).click();
      await page.locator('#plan-dialog').waitFor({state:'hidden'});
      await page.getByRole('button',{name:'Изменить дело «Самостоятельное дело»',exact:true}).click();
      await fillTask(page,'Прикреплённое дело');await page.getByLabel('Цель дела',{exact:true}).selectOption({label:'Первая цель'});
      await page.getByLabel('Статус дела',{exact:true}).selectOption('in_progress');await save(page);
      await page.getByRole('button',{name:'Изменить дело «Прикреплённое дело»',exact:true}).click();
      await page.getByLabel('Статус дела',{exact:true}).selectOption('done');await save(page);
      await page.getByText('1 из 1 дел выполнено',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Удалить цель «Первая цель»',exact:true}).click();
      page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Удалить',exact:true}).click();
      await page.getByText('В цели ещё есть дела.',{exact:false}).waitFor();
      await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();
      await page.getByRole('button',{name:'Изменить дело «Прикреплённое дело»',exact:true}).click();
      await page.getByLabel('Цель дела',{exact:true}).selectOption('');await page.getByLabel('Статус дела',{exact:true}).selectOption('not_started');await save(page);
      rows=(await data(page,'/tasks')).body.tasks;assert.equal(rows[0].goal_id,null);assert.equal(rows[0].remind_before_minutes,null);
      await page.getByRole('button',{name:'Изменить цель «Первая цель»',exact:true}).click();
      await page.getByLabel('Название цели',{exact:true}).fill('Обновлённая цель');await save(page);
      await page.getByRole('button',{name:'Удалить цель «Обновлённая цель»',exact:true}).click();
      page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Удалить',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      await page.getByRole('button',{name:'Удалить дело «Прикреплённое дело»',exact:true}).click();
      page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Удалить',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      assert.equal((await data(page,'/tasks')).body.tasks.length,0);
    });
    await scenario('phone zone at midnight, move due vs only notification',async page=>{
      await page.getByRole('button',{name:'Добавить дело',exact:true}).click();await fillTask(page,'Дело со сроком');
      await page.getByLabel('Указать срок',{exact:true}).check();
      await page.getByLabel('Дата и время',{exact:true}).fill('2099-02-01T00:30');
      await page.getByLabel('Напоминание',{exact:true}).selectOption('60');await save(page);
      let task=(await data(page,'/tasks')).body.tasks[0];assert.equal(Date.parse(task.due_at),Date.parse('2099-01-31T14:30:00Z'));
      await page.getByRole('button',{name:'Перенести дело «Дело со сроком»',exact:true}).click();
      await page.getByLabel('Что перенести',{exact:true}).selectOption('notification');
      await page.getByLabel('Дата и время',{exact:true}).fill('2099-02-02T10:00');await save(page);
      let moved=(await data(page,'/tasks')).body.tasks[0];assert.equal(moved.due_at,task.due_at);assert.equal(Date.parse(moved.notification_override_at),Date.parse('2099-02-02T00:00:00Z'));
      await page.getByRole('button',{name:'Перенести дело «Дело со сроком»',exact:true}).click();
      await page.getByLabel('Дата и время',{exact:true}).fill('2099-02-03T12:00');await save(page);
      moved=(await data(page,'/tasks')).body.tasks[0];assert.equal(moved.notification_override_at,null);assert.equal(Date.parse(moved.due_at),Date.parse('2099-02-03T02:00:00Z'));
    },'', 'Asia/Vladivostok');
    await scenario('phone DST gap and repeated hour use device rules',async page=>{
      await page.getByRole('button',{name:'Добавить дело',exact:true}).click();await fillTask(page,'DST');
      await page.getByLabel('Указать срок',{exact:true}).check();
      assert.equal(await page.getByLabel('Часовой пояс',{exact:true}).count(),0);
      await page.getByLabel('Дата и время',{exact:true}).fill('2027-03-14T02:30');
      await page.getByRole('button',{name:'Сохранить',exact:true}).click();await page.getByText('Такого местного времени нет',{exact:false}).waitFor();
      await page.getByLabel('Дата и время',{exact:true}).fill('2027-11-07T01:30');await save(page);
      const task=(await data(page,'/tasks')).body.tasks[0];assert.equal(Date.parse(task.due_at),Date.parse('2027-11-07T05:30:00Z'));
      assert.equal(task.timezone,'America/New_York');
      await page.getByText('Сохранено на сервере.',{exact:true}).waitFor();
      await page.waitForTimeout(5100); // Real session launch cooldown; do not bypass authentication.
      await page.reload();await page.waitForFunction(()=>!document.getElementById('tab-home').disabled);
      await page.getByRole('button',{name:'Мой план',exact:true}).click();await page.getByRole('heading',{name:'DST',exact:true}).waitFor();
      assert.equal((await data(page,'/tasks/'+task.id)).body.task.due_at,task.due_at);
    },'', 'America/New_York');
    await scenario('draft cancel, dirty warning, edited fixture and double confirm',async page=>{
      await page.getByRole('button',{name:'Пример плана',exact:true}).click();
      await page.getByText('Тестовый пример, не ответ AI.',{exact:false}).waitFor();
      assert.equal((await data(page,'/goals')).body.goals.length,0);
      await page.getByLabel('Название цели',{exact:true}).fill('Не сохранять');
      page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();assert.equal(await page.locator('#plan-dialog').isVisible(),true);
      page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();
      assert.equal((await data(page,'/tasks')).body.tasks.length,0);
      await page.getByRole('button',{name:'Пример плана',exact:true}).click();
      await page.getByLabel('Название цели',{exact:true}).fill('Мой изменённый план');
      await page.getByRole('button',{name:'Убрать дело 3',exact:true}).click();
      await page.getByRole('button',{name:'Добавить строку дела',exact:true}).click();
      await page.getByLabel('Название дела 3',{exact:true}).fill('Мой третий шаг');
      await page.getByLabel('Указать срок дела 1',{exact:true}).check();
      await page.getByLabel('Дата и время дела 1',{exact:true}).fill('2099-04-01T12:30');
      await page.getByLabel('Напоминание дела 1',{exact:true}).selectOption('60');
      if(evidence)await page.screenshot({path:evidence+'/draft.png',fullPage:true});
      await page.getByRole('button',{name:'Подтвердить план',exact:true}).dblclick();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      assert.equal((await data(page,'/goals')).body.goals.length,1);
      const tasks=(await data(page,'/tasks')).body.tasks;assert.equal(tasks.length,3);assert.ok(tasks.some(t=>t.title==='Мой третий шаг'));
      assert.equal(tasks.find(t=>t.title==='Уточнить список документов').remind_before_minutes,60);
    });
    await scenario('lost committed response retries exact operation without duplicates',async page=>{
      const seen=[];let first=true;
      await page.route('**/api/planner/commit',async route=>{
        seen.push({body:route.request().postData(),key:route.request().headers()['idempotency-key']});
        if(first){first=false;const response=await route.fetch();assert.equal(response.status(),201);await route.abort('failed');}else await route.continue();
      });
      await page.getByRole('button',{name:'Пример плана',exact:true}).click();await page.getByRole('button',{name:'Подтвердить план',exact:true}).click();
      await page.getByRole('button',{name:'Повторить тот же запрос',exact:true}).waitFor();
      assert.equal((await data(page,'/goals')).body.goals.length,1);
      assert.equal(await page.getByLabel('Название цели',{exact:true}).isDisabled(),true);
      await page.getByRole('button',{name:'Повторить тот же запрос',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      assert.equal((await data(page,'/tasks')).body.tasks.length,3);assert.deepEqual(seen[0],seen[1]);
    });
    await scenario('validation, stale version, offline list, auth renewal and keyboard cancel',async page=>{
      await newTask(page,'Проверка ошибок');
      await page.getByRole('button',{name:'Изменить дело «Проверка ошибок»',exact:true}).click();
      const task=(await data(page,'/tasks')).body.tasks[0];
      assert.equal((await data(page,'/tasks/'+task.id,'PATCH',{expected_version:task.version,title:'Изменено в другом окне'})).status,200);
      await fillTask(page,'Устаревшая правка');await page.getByRole('button',{name:'Сохранить',exact:true}).click();
      await page.getByText('Дело или цель изменились в другом окне.',{exact:false}).waitFor();
      page.once('dialog',d=>d.accept());await page.keyboard.press('Escape');
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();
      await page.getByRole('button',{name:'Изменить дело «Изменено в другом окне»',exact:true}).waitFor();
      await page.route('**/api/planner/tasks?**',r=>r.abort('internetdisconnected'));
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();await page.locator('#plan-error').filter({hasText:'Не удалось'}).waitFor();
      await page.unroute('**/api/planner/tasks?**');await page.getByRole('button',{name:'Обновить список',exact:true}).click();
      await page.getByRole('button',{name:'Добавить дело',exact:true}).click();await fillTask(page,'После нового сеанса');
      let auth=true;const calls=[];
      await page.route('**/api/planner/tasks',async route=>{
        calls.push({body:route.request().postData(),key:route.request().headers()['idempotency-key'],auth:route.request().headers().authorization});
        if(auth){auth=false;await route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Synthetic expired session'})});}else await route.continue();
      });
      await page.getByRole('button',{name:'Сохранить',exact:true}).click();await page.getByRole('button',{name:'Начать новый сеанс',exact:true}).waitFor();
      await page.waitForTimeout(5100);await page.getByRole('button',{name:'Начать новый сеанс',exact:true}).click();
      await page.waitForFunction(()=>document.getElementById('reopen').hidden);
      await page.getByRole('button',{name:'Повторить тот же запрос',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      assert.equal(calls.length,2);assert.equal(calls[0].key,calls[1].key);assert.equal(calls[0].body,calls[1].body);assert.notEqual(calls[0].auth,calls[1].auth);
      assert.equal((await data(page,'/tasks')).body.tasks.length,2);
      await page.getByRole('button',{name:'Добавить дело',exact:true}).click();assert.equal(await page.getByLabel('Название дела',{exact:true}).evaluate(el=>el===document.activeElement),true);
      await page.keyboard.press('Escape');await page.locator('#plan-dialog').waitFor({state:'hidden'});
      assert.equal(await page.getByRole('button',{name:'Добавить дело',exact:true}).evaluate(el=>el===document.activeElement),true);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    });
    await scenario('four groups, paginated task list, literal titles and past reminder rejection',async page=>{
      for(const task of [
        {title:'Будущее',due_at:'2099-01-01T12:00:00Z',timezone:'UTC',timezone_confirmed:true},
        {title:'Прошлое',due_at:'2020-01-01T12:00:00Z',timezone:'UTC',timezone_confirmed:true},
        {title:'Без даты'}, {title:'Готовое',status:'done'}
      ])assert.equal((await data(page,'/tasks','POST',{id:randomUUID(),...task})).status,201);
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();
      for(const title of ['Предстоит · 1','Просрочено · 1','Без даты · 1','Готово · 1'])await page.getByRole('heading',{name:title,exact:true}).waitFor();
      await page.getByRole('button',{name:'Добавить дело',exact:true}).click();await fillTask(page,'Не сохранять прошлое напоминание');
      await page.getByLabel('Указать срок',{exact:true}).check();await page.getByLabel('Дата и время',{exact:true}).fill('2020-01-01T12:00');
      await page.getByLabel('Напоминание',{exact:true}).selectOption('60');await page.getByRole('button',{name:'Сохранить',exact:true}).click();
      await page.getByText('Время напоминания уже прошло.',{exact:false}).waitFor();
      assert.equal((await data(page,'/tasks')).body.tasks.length,4);
      page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();
      for(const count of [50,50,1]) {
        const result=await data(page,'/commit','POST',{goal:{id:randomUUID(),title:'Много дел'},tasks:Array.from({length:count},()=>({id:randomUUID(),title:'<img src=x onerror=alert(1)>'})),confirmed:true});
        assert.equal(result.status,201);
      }
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();
      await page.getByRole('heading',{name:'Без даты · 102',exact:true}).waitFor();
      assert.equal(await page.locator('#plan-list img').count(),0);
      assert.equal(await page.getByRole('heading',{name:'<img src=x onerror=alert(1)>',exact:true}).count(),101);
    });
    await scenario('phone edit preserves later repeated hour on unrelated change',async page=>{
      const id=randomUUID();
      assert.equal((await data(page,'/tasks','POST',{id,title:'Повторяющееся время',due_at:'2027-11-07T01:30:00-05:00',timezone:'America/New_York',timezone_confirmed:true})).status,201);
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();
      await page.getByRole('button',{name:'Изменить дело «Повторяющееся время»',exact:true}).click();
      await fillTask(page,'Сохранённое время');await save(page);
      const task=(await data(page,'/tasks/'+id)).body.task;
      assert.equal(Date.parse(task.due_at),Date.parse('2027-11-07T06:30:00Z'));
      assert.equal(task.timezone,'America/New_York');
    });
    await scenario('review draft renumbering keeps every schedule field unique and bound to its row',async page=>{
      await page.getByRole('button',{name:'Пример плана',exact:true}).click();
      await page.getByRole('button',{name:'Убрать дело 1',exact:true}).click();
      await page.getByRole('button',{name:'Добавить строку дела',exact:true}).click();
      for(let i=1;i<=3;i++) {
        for(const label of ['Название дела ','Указать срок дела ','Дата и время дела ','Напоминание дела '])assert.equal(await page.getByLabel(label+i,{exact:true}).count(),1,label+i);
        await page.getByLabel('Название дела '+i,{exact:true}).fill('Строка '+i);
        await page.getByLabel('Указать срок дела '+i,{exact:true}).check();
        await page.getByLabel('Дата и время дела '+i,{exact:true}).fill(`2099-04-0${i}T12:30`);
      }
      await page.getByRole('button',{name:'Подтвердить план',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
      const tasks=(await data(page,'/tasks')).body.tasks;
      for(let i=1;i<=3;i++)assert.equal(Date.parse(tasks.find(t=>t.title==='Строка '+i).due_at),Date.parse(`2099-04-0${i}T03:30:00Z`));
    });
    await scenario('review fold title-only edit preserves exact stored seconds',async page=>{
      const id=randomUUID();
      assert.equal((await data(page,'/tasks','POST',{id,title:'Точное время',due_at:'2027-11-07T01:30:37-04:00',timezone:'America/New_York',timezone_confirmed:true})).status,201);
      await page.getByRole('button',{name:'Обновить список',exact:true}).click();await page.getByRole('button',{name:'Изменить дело «Точное время»',exact:true}).click();
      await fillTask(page,'Название изменено');await save(page);
      const task=(await data(page,'/tasks/'+id)).body.task;
      assert.equal(task.title,'Название изменено');assert.equal(Date.parse(task.due_at),Date.parse('2027-11-07T05:30:37Z'));
    });
    await scenario('legacy signed save and view material retains actual favorite and share link',async page=>{
      await page.locator('#saved .saved-card').first().waitFor();
      assert.match(await page.locator('#saved-confirmation').innerText(),/Сохранено/);
      const title=await page.locator('#saved .saved-card h2').first().innerText();assert.ok(title);
      await page.getByRole('button',{name:'Поделиться ответом ↗',exact:true}).click();
      await page.waitForFunction(()=>Boolean(window.testShared));
      assert.match(await page.evaluate(()=>window.testShared.link),/startapp=view_doc_passport_q3$/);
      await page.evaluate(()=>openMaterial('doc_passport_q3',false));
      await page.locator('#material-card h2').filter({hasText:title}).waitFor();
      await page.getByRole('button',{name:'← К Моему',exact:true}).click();await page.locator('#saved .saved-card').first().waitFor();
      await page.getByRole('button',{name:'Мой план',exact:true}).click();
      await page.getByText('Пока нет целей и дел.',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Моё',exact:true}).click();await page.locator('#saved .saved-card h2').filter({hasText:title}).waitFor();
    },'save_doc_passport_q3');
    await scenario('old searches, saved cards, material and share render paths retained',async page=>{
      await page.getByRole('button',{name:'Главная',exact:true}).click();
      const card={title:'Синтетическая карточка',text:'Тестовый текст',section:'work',status:'information',actions:[],favorite_id:'a'.repeat(64)};
      await page.route('**/api/action',async route=>{
        const section=JSON.parse(route.request().postData()).section;
        await route.fulfill({json:{section,summary:'',notices:[],controls:[],cards:[{...card,section}]}});
      });
      for(const [section,title] of [['work','Ищу работу'],['rental','Ищу жильё'],['help','Вам тут помогут']]) {
        await page.locator(`[data-section="${section}"]`).click();await page.locator('#section-title').filter({hasText:title}).waitFor();
        await page.getByRole('button',{name:'Мой план',exact:true}).click();await page.getByRole('button',{name:'Главная',exact:true}).click();
        await page.locator('#card').filter({hasText:'Синтетическая карточка'}).waitFor();
        await page.locator('#home').click();await page.locator('#landing').waitFor({state:'visible'});
      }
      await page.getByRole('button',{name:'Моё',exact:true}).click();await page.getByText('Оставь здесь то, что понравилось',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Поделиться ↗',exact:true}).click();await page.locator('#share-panel').waitFor({state:'visible'});
      assert.equal(await page.locator('#share-section').isVisible(),false);
    });
    await scenario('appearance uses MAX palette, local Roboto and explicit persistent theme choice',async page=>{
      await page.getByRole('button',{name:'Оформление',exact:true}).click();
      await page.getByLabel('Тема',{exact:true}).selectOption('dark');
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
      assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()),'#0f0f12');
      await page.getByRole('button',{name:'Готово',exact:true}).click();
      await page.emulateMedia({colorScheme:'light'});
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
      await page.waitForTimeout(5100);await page.reload();
      await page.waitForFunction(()=>!document.getElementById('tab-home').disabled);
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'dark');
      await page.getByRole('button',{name:'Оформление',exact:true}).click();
      await page.getByLabel('Тема',{exact:true}).selectOption('light');
      assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--surface').trim()),'#edeef2');
      await page.getByLabel('Тема',{exact:true}).selectOption('system');
      await page.emulateMedia({colorScheme:'dark'});
      await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('button',{name:'Оформление',exact:true}).evaluate(e=>e===document.activeElement),true);
      await page.evaluate(()=>document.fonts.ready);
      assert.equal(await page.evaluate(()=>document.fonts.check('500 16px Roboto','План')),true);
      assert.equal(await page.locator('.tabs svg').count(),3);
      const contrast=await page.evaluate(()=>{
        const rgb=s=>s.match(/[\d.]+/g).map(Number);
        const bg=rgb(getComputedStyle(document.documentElement).backgroundColor);
        const fg=rgb(getComputedStyle(document.querySelector('.plan-intro')).color);
        const composite=fg.slice(0,3).map((v,i)=>v*(fg[3]??1)+bg[i]*(1-(fg[3]??1)));
        const luminance=values=>values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
        const a=luminance(bg),b=luminance(composite);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
      });
      assert.ok(contrast>=4.5,'Secondary body text contrast: '+contrast);
      await page.evaluate(()=>{
        Object.defineProperty(window,'localStorage',{configurable:true,get(){throw new DOMException('Storage unavailable','SecurityError');}});
        window.TochkaTheme.setPreference('light');
      });
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'light');
      await page.getByRole('button',{name:'Мой план',exact:true}).click();
      await page.getByText('Пока нет целей и дел.',{exact:true}).waitFor();
      await page.getByRole('button',{name:'Пример плана',exact:true}).click();
      const checkbox=page.getByLabel('Указать срок дела 1',{exact:true});
      for(const scheme of ['light','dark']) {
      await page.evaluate(value=>window.TochkaTheme.setPreference(value),scheme);
      assert.equal(await checkbox.evaluate(e=>getComputedStyle(e).appearance),'none');
      assert.equal(await checkbox.evaluate(e=>getComputedStyle(e).borderRadius),'6px');
      await checkbox.focus();await page.keyboard.press('Space');
      assert.equal(await checkbox.isChecked(),true);
      assert.equal(await checkbox.evaluate(e=>getComputedStyle(e,'::after').opacity),'1');
      await page.getByText('Указать срок дела 1',{exact:true}).click();
      assert.equal(await checkbox.isChecked(),false);
      }
    });
    console.log(JSON.stringify({browser:'Chromium',checks:checks.length,passed:checks}));
  } finally {await browser.close();await new Promise(resolve=>proxy.close(resolve));}
}
run().catch(e=>{console.error(e);process.exitCode=1;});
