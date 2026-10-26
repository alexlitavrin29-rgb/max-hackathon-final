/* Real Chromium/DOM recovery checks. Every HTTP request is fulfilled locally. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'../..');
const base='https://planner.test';
const html=fs.readFileSync(path.join(root,'project/miniapp/static/index.html'),'utf8')
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'');
(async()=>{
  const browser=await chromium.launch({headless:true});
  try{
    for(const mode of ['unavailable','busy','network','http503','category']){
      const context=await browser.newContext({viewport:{width:390,height:844}});
      const page=await context.newPage(),calls=[],errors=[];let draftVersion=0;
      page.on('pageerror',error=>errors.push(error.message));
      await page.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.origin!==base)return route.abort();
        const json=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
        if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
        if(url.pathname.startsWith('/static/')){
          const file=path.join(root,'project/miniapp',url.pathname);
          if(!fs.existsSync(file))return route.abort();
          return route.fulfill({path:file});
        }
        if(url.pathname==='/api/planner/goals')return json({goals:[],next_after:null});
        if(url.pathname==='/api/planner/tasks')return json({tasks:[],next_after:null});
        if(url.pathname==='/api/planner/timezones')return json({timezones:['Europe/Moscow']});
        if(url.pathname==='/api/planner/draft')return json(route.request().method()==='PUT'?{draft:{version:++draftVersion}}:{draft:null});
        if(url.pathname.startsWith('/api/planner/ai/')){
          calls.push({route:url.pathname,body:route.request().postDataJSON()});
          if(calls.length===1){
            if(mode==='category')return json({status:'clarify',question_id:'topic_choice',question:'С какой темы начнём?',choices:[{id:'benefits',title:'Выплаты и поддержка'},{id:'work',title:'Работа'},{id:'documents',title:'Документы'},{id:'housing',title:'Жильё'},{id:'help',title:'Социальная и правовая помощь'},{id:'study',title:'Обучение'}]});
            if(mode==='network')return route.abort();
            if(mode==='http503')return route.fulfill({status:503,contentType:'application/json',body:'{"detail":{"code":"unavailable"}}'});
            return json({status:'unsupported',reason:mode,message:'Временный отказ AI'});
          }
          return json({status:'draft',information:{text:mode==='category'?'Подготовленная справка о выплатах':'Подготовленная справка о паспорте',sources:[]},tasks:[],...(mode==='category'?{related_topics:[{id:'work',title:'Работа'}]}:{})});
        }
        throw Error('Unexpected local test route: '+url.pathname);
      });
      try{
        await page.goto(base);
        for(const script of ['planner-core.js','planner-draft-codec.js','material-link-text.js','plan.js'])
          await page.addScriptTag({path:path.join(root,'project/miniapp/static',script)});
        await page.evaluate(async()=>{window.testPlanner=window.createPlanner({getToken:()=> 'synthetic',onAuthExpired(){}});await window.testPlanner.show();});
        await page.evaluate(()=>window.testPlanner.startAnswer());
        const dialog=page.locator('#plan-dialog'),input=dialog.getByLabel('Опиши свою ситуацию',{exact:true});
        await input.fill(mode==='category'?'Как заработать деньги?':'Хочу восстановить паспорт');
        await dialog.getByRole('button',{name:'Получить справку',exact:true}).click();
        if(mode==='category'){
          const next=dialog.getByRole('button',{name:'Продолжить',exact:true});
          await dialog.getByText('С какой темы начнём?',{exact:true}).waitFor();
          assert.equal(await next.isDisabled(),true);
          assert.equal(await dialog.locator('textarea').count(),1);
          assert.equal(await dialog.evaluate(n=>n.scrollWidth<=n.clientWidth),true);
          assert.doesNotMatch(await dialog.innerText(),/AI сейчас недоступен|Получить предложение плана/);
          await dialog.getByRole('button',{name:'Выплаты и поддержка',exact:true}).click();
          assert.equal(await next.isEnabled(),true);
          await next.click();
          await dialog.getByText('Подготовленная справка о выплатах',{exact:true}).waitFor();
          assert.equal(calls[1].body.answer,'benefits');
          assert.equal(calls[1].body.question_id,'topic_choice');
          await dialog.getByRole('button',{name:'Также: Работа',exact:true}).waitFor();
          assert.equal(calls.length,2);
          await dialog.getByRole('button',{name:'Получить предложение плана',exact:true}).click();
          await dialog.getByRole('button',{name:'Продолжить с выбранными',exact:true}).waitFor();
          assert.equal(calls[2].route,'/api/planner/ai/draft-plan');
          assert.equal(calls[2].body.answer,'benefits');
          assert.deepEqual(errors,[]);
          console.log('PASS real DOM: category choice gates the plan and adjacent themes stay optional');
          continue;
        }
        await dialog.locator('.plan-error').waitFor({state:'visible'});
        assert.equal(await input.isEnabled(),true);
        assert.equal(await input.inputValue(),'Хочу восстановить паспорт');
        assert.doesNotMatch(await dialog.innerText(),/Результат сохранения|Ищем справку…/);
        await dialog.getByRole('button',{name:'Получить справку',exact:true}).click();
        await dialog.getByText('Подготовленная справка о паспорте',{exact:true}).waitFor();
        assert.equal(calls.length,2);assert.deepEqual(calls[1],calls[0]);
        assert.deepEqual(errors,[]);
        console.log('PASS real DOM: '+mode+' preserves question, clears wait, repeats the same request');
      }finally{await context.close();}
    }
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
