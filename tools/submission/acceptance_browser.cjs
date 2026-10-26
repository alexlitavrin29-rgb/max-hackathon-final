const {chromium,webkit}=require('playwright');
const assert=require('node:assert/strict');
const {createHmac}=require('node:crypto');
const fs=require('node:fs');
const path=require('node:path');
const BASE=process.env.ACCEPTANCE_URL||'http://127.0.0.1:18976';
const OUT=path.resolve(process.env.ACCEPTANCE_EVIDENCE_DIR||'browser-evidence');
fs.mkdirSync(OUT,{recursive:true});
assert.ok(['127.0.0.1','localhost'].includes(new URL(BASE).hostname));
function signed(owner){const v={auth_date:String(Math.floor(Date.now()/1000)),user:JSON.stringify({id:owner})};const secret=createHmac('sha256','WebAppData').update('local-preview-no-max-access').digest();return new URLSearchParams({...v,hash:createHmac('sha256',secret).update(Object.keys(v).sort().map(k=>k+'='+v[k]).join('\n')).digest('hex')}).toString();}
(async()=>{
  const results=[];let owner=881000000000+Math.floor(Math.random()*1000000);
  for(const [engine,type] of Object.entries({chromium,webkit})){
    const browser=await type.launch({headless:true});
    try{for(const width of [320,393])for(const colorScheme of ['light','dark']){
      const label=`${engine}-${width}-${colorScheme}`;
      const context=await browser.newContext({viewport:{width,height:852},colorScheme,locale:'ru-RU'});
      const page=await context.newPage();page.setDefaultTimeout(12000);const errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await context.route('https://st.max.ru/**',r=>r.abort());
      // This synthetic test server uses its own token and expected origin.
      // Runtime authorization is unchanged; no production destination is routed.
      await context.route(BASE+'/api/**',async r=>{const response=await r.fetch({headers:{...r.request().headers(),origin:'https://acceptance.test'}});await r.fulfill({response});});
      await context.addInitScript(init=>{window.WebApp={initData:init,platform:'web',ready(){},expand(){},BackButton:{show(){},hide(){},onClick(fn){window.testMAXBack=fn;}},shareMaxContent:()=>Promise.resolve()};},signed(++owner));
      try{
        await page.goto(BASE);const launched=Date.now();await page.waitForFunction(()=>!document.getElementById('tab-plan').disabled);
        await page.locator('#home-answer').waitFor();
        await page.screenshot({path:path.join(OUT,label+'-home.png'),fullPage:true});
        await page.locator('.theme-housing').click();await page.locator('.housing-permanent').waitFor();
        await page.getByRole('button',{name:'Подробнее об этапах',exact:true}).click();
        const route=page.getByRole('dialog',{name:'Получение постоянного жилья'});await route.waitFor();
        assert.equal(await route.locator('a[href="https://www.gosuslugi.ru/676953/1"]').count(),1);
        await route.getByRole('button',{name:'Изменить статус',exact:true}).click();
        await route.getByLabel('Мой статус',{exact:true}).selectOption('in_list');
        await route.getByRole('button',{name:'Сохранить статус',exact:true}).click();
        await route.getByText('Статус сохранён',{exact:true}).waitFor();
        await page.screenshot({path:path.join(OUT,label+'-housing.png'),fullPage:true});
        await route.getByRole('button',{name:'Закрыть маршрут жилья',exact:true}).click();
        assert.equal(await page.locator('#topic .topic-back').isVisible(),false);await page.evaluate(()=>window.testMAXBack());
        await page.locator('#tab-plan').click();await page.locator('#plan-new-task').click();
        const dialog=page.locator('#plan-dialog');
        await dialog.getByLabel('Название дела',{exact:true}).fill('Список документов для приёмки');
        await dialog.getByLabel('Указать срок',{exact:true}).check();
        await dialog.getByLabel('Дата',{exact:true}).fill('2026-10-28');
        await dialog.getByRole('button',{name:'Сохранить',exact:true}).click();await dialog.waitFor({state:'hidden'});
        await page.locator('#plan [data-plan-mode="tasks"]').click();
        await page.locator('#tasks-list').getByText('Список документов для приёмки',{exact:true}).waitFor();
        await page.locator('#tasks-list').getByRole('button',{name:'Готово',exact:true}).first().click();
        await dialog.getByRole('button',{name:'Подтвердить',exact:true}).click();await dialog.waitFor({state:'hidden'});
        const doc=await page.evaluate(async()=>{const r=await fetch('/api/planner/tasks',{headers:{Authorization:'Bearer '+token}});return r.json();});
        assert.equal(doc.tasks.length,1);assert.equal(doc.tasks[0].due_date,'2026-10-28');assert.equal(doc.tasks[0].status,'done');
        await page.locator('#tasks-list details.plan-group summary').click();
        await page.screenshot({path:path.join(OUT,label+'-tasks.png'),fullPage:true});
        const remaining=5200-(Date.now()-launched);if(remaining>0)await new Promise(r=>setTimeout(r,remaining));
        await page.reload();await page.waitForFunction(()=>!document.getElementById('tab-plan').disabled);
        await page.locator('#tab-plan').click();if(await page.locator('#plan').isVisible())await page.locator('#plan [data-plan-mode="tasks"]').click();
        await page.locator('#tasks-list details.plan-group summary').click();
        await page.locator('#tasks-list').getByText('Список документов для приёмки',{exact:true}).waitFor();
        await page.locator('#tab-handbook').click();await page.getByLabel('Поиск в справочнике').fill('выплаты');
        await page.locator('#handbook').getByRole('button',{name:'Найти',exact:true}).click();
        await page.locator('#handbook .handbook-entry').first().waitFor().catch(async()=>{
          await page.waitForFunction(()=>document.querySelectorAll('#handbook button').length>3);
        });
        assert.equal(await page.locator('#handbook button.back').isVisible(),false);await page.evaluate(()=>window.testMAXBack());
        await page.locator('#tab-home').click();await page.locator('#home-answer').waitFor();
        await page.locator('#home-answer').click();await dialog.waitFor();
        assert.equal(await page.locator('.tabs').isVisible(),true);
        assert.equal(await dialog.locator('.plan-close').isVisible(),false);
        assert.equal(await dialog.evaluate(n=>n.matches(':modal')),false);
        await dialog.getByLabel('Опиши свою ситуацию',{exact:true}).fill('Хочу сохранить заработанные деньги');
        await page.screenshot({path:path.join(OUT,label+'-answer-entry.png'),fullPage:true});
        await page.locator('#tab-plan').click();await dialog.waitFor({state:'hidden'});await page.locator('#plan').waitFor();
        await page.locator('#plan-new-task').click();await dialog.getByRole('heading',{name:'Новое дело',exact:true}).waitFor();
        await dialog.locator('.plan-close').click();await dialog.waitFor({state:'hidden'});
        await page.locator('#plan-resume').click();await dialog.waitFor();
        assert.equal(await dialog.evaluate(n=>n.matches(':modal')),false);assert.equal(await page.locator('.tabs').isVisible(),true);
        assert.equal(await dialog.getByLabel('Опиши свою ситуацию',{exact:true}).inputValue(),'Хочу сохранить заработанные деньги');
        await dialog.getByRole('button',{name:'Не знаю, с чего начать',exact:true}).click();
        assert.equal(await page.locator('.tabs').isVisible(),false);
        assert.equal(await dialog.evaluate(n=>n.matches(':modal')),true);
        await page.evaluate(()=>window.testMAXBack());await dialog.waitFor({state:'hidden'});
        assert.equal(await page.locator('main').evaluate(n=>n.inert),false);
        await page.locator('#tab-plan').click();await page.locator('#plan-resume').click();await dialog.waitFor();
        page.once('dialog',d=>d.accept());await dialog.getByRole('button',{name:'Удалить черновик',exact:true}).click();await dialog.waitFor({state:'hidden'});
        await page.locator('#plan-ai').click();await dialog.waitFor();
        assert.equal(await page.locator('.tabs').isVisible(),true);
        await dialog.getByLabel('Что хотите сделать?',{exact:true}).fill('Подготовить документы');
        await page.screenshot({path:path.join(OUT,label+'-plan-entry.png'),fullPage:true});
        page.once('dialog',d=>d.accept());await dialog.getByRole('button',{name:'Удалить черновик',exact:true}).click();await dialog.waitFor({state:'hidden'});
        await page.locator('#tab-home').click();await page.locator('#home-answer').waitFor();
        assert.deepEqual(errors,[]);
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        const cleanup=await page.evaluate(async()=>{const r=await fetch('/api/account',{method:'DELETE',headers:{Authorization:'Bearer '+token}});return r.status;});assert.equal(cleanup,200);
        results.push({label,ok:true,ui:['home','housing-status','dated-task-create','task-complete','reload','handbook','MAX-back-callback','answer-entry-tabs','plan-entry-tabs','draft-preservation','auxiliary-form-preserves-entry-tabs','detail-tabs-hidden'],synthetic:true});
        console.log('PASS',label);
      }catch(e){await page.screenshot({path:path.join(OUT,label+'-FAIL.png'),fullPage:true});fs.writeFileSync(path.join(OUT,label+'-FAIL.txt'),await page.locator('body').innerText());throw e;}
      finally{await context.close();}
    }}finally{await browser.close();}
  }
  fs.writeFileSync(path.join(OUT,'BROWSER.json'),JSON.stringify({ok:true,results,nativeMAX:false,providerCalls:0},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
