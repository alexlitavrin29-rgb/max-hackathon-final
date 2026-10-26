/* Synthetic fixtures only; no application server or AI provider is used.
 * Run: node tools/submission/planner_material_ui.cjs
 * Optional screenshots: PLANNER_UI_SCREENSHOTS=/tmp/planner-ui node tools/submission/planner_material_ui.cjs
 * Optional Chromium override: PLANNER_UI_CHROMIUM=/path/to/chromium
 */
const fs=require('node:fs');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../../project/miniapp/static')+path.sep;
const screenshotDir=process.env.PLANNER_UI_SCREENSHOTS;
if(screenshotDir)fs.mkdirSync(screenshotDir,{recursive:true});
(async()=>{
const browser=await chromium.launch({executablePath:process.env.PLANNER_UI_CHROMIUM || (fs.existsSync('/usr/bin/chromium')?'/usr/bin/chromium':undefined),headless:true,args:['--no-sandbox']});
try{
for(const theme of ['light','dark']){
const page=await browser.newPage({viewport:{width:320,height:700}});const errors=[];page.on('pageerror',e=>errors.push(e.message));
let commit=null;let saved=false;let latestCalls=0;let latestAvailable=true;let aiCalls=0;const info={text:'Краткая подготовленная справка по документам.',sources:[{id:'docs',title:'Документы: официальный источник',url:'https://example.org/docs'}]};
const proposal={status:'draft',title:'Подготовить документы',information:info,context:{question:'Какие документы нужны?',material_ids:['docs'],revision:'r1'},tasks:[{title:'Изучить информацию '+ '🙂'.repeat(100),description:'Описание '.repeat(40),material_ids:['docs'],links:info.sources,kind:'information'},{title:'Подготовить заявление',description:'Проверьте перечень документов',material_ids:['docs'],links:info.sources,kind:'action'},{title:'Проверить комплект',description:'Сверьте документы',material_ids:['docs'],links:[],kind:'action'}]};
await page.route('https://planner.test/**',async r=>{
const url=new URL(r.request().url());let body;
if(url.pathname==='/')return r.fulfill({contentType:'text/html',body:`<html data-theme="${theme}"><head><meta charset="UTF-8"><style>${fs.readFileSync(root+'style.css','utf8')}\n${fs.readFileSync(root+'max-ui.css','utf8')}</style></head><body><aside style="position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:2px 4px;background:#faf0bf;color:#302800;font:11px/14px sans-serif;pointer-events:none">Синтетический пример — проверка интерфейса</aside><section id="plan"><div id="plan-list"></div>${['plan-new-task','plan-new-goal','plan-ai','plan-ai-help','plan-example','plan-refresh','plan-resume'].map(id=>`<button id="${id}">${id}</button>`).join('')}<p id="plan-status"></p><p id="plan-error"></p></section><dialog id="plan-dialog"></dialog><script>${fs.readFileSync(root+'planner-core.js','utf8')}</script><script>${fs.readFileSync(root+'plan.js','utf8')}</script><script>window.planner=createPlanner({getToken:()=> 'test',onAuthExpired(){},onRenew:async()=>{}});planner.show();</script></body></html>`});
if(url.pathname.endsWith('/ai/latest')){latestCalls++;body={proposal:latestAvailable?proposal:null};}
else if(url.pathname.endsWith('/ai/draft-plan')){aiCalls++;body=proposal;}
else if(url.pathname.endsWith('/commit')){commit=r.request().postDataJSON();saved=true;body={goal:commit.goal};}
else if(url.pathname.endsWith('/goals'))body={goals:saved?[{...commit.goal,context:{...commit.context,information:info}}]:[],next_after:null};
else if(url.pathname.endsWith('/tasks'))body={tasks:saved?commit.tasks.map(t=>({...t,goal_id:commit.goal.id,links:info.sources})):[],next_after:null};
else if(url.pathname.endsWith('/timezones'))body={timezones:['UTC']};
else return r.abort();
return r.fulfill({contentType:'application/json',body:JSON.stringify(body)});
});
await page.goto('https://planner.test/');await page.locator('#plan-ai').click();await page.getByLabel('Что хотите сделать?',{exact:true}).fill('Какие документы нужны?');
await page.getByRole('button',{name:'Получить предложение',exact:true}).click();await page.locator('.plan-suggestion').first().waitFor();
assert.equal(await page.locator('.plan-suggestion').count(),3);assert.equal(await page.locator('[aria-pressed="true"]').count(),0);assert.equal(latestCalls,0);
assert.equal(await page.getByRole('button',{name:'Продолжить с выбранными'}).isDisabled(),true);assert.equal(commit,null);
await page.locator('.plan-suggestion').nth(1).getByRole('button').click();
await page.locator('.plan-suggestion').nth(1).getByRole('button').click();assert.equal(await page.getByRole('button',{name:'Продолжить с выбранными'}).isDisabled(),true);
await page.locator('.plan-suggestion').nth(1).getByRole('button').click();
await page.evaluate(()=>planner.show(undefined,'review'));assert.equal(latestCalls,0);assert.equal(await page.locator('[aria-pressed="true"]').count(),1);
assert.equal(await page.locator('#plan-dialog').evaluate(n=>n.scrollWidth<=n.clientWidth),true);
assert.equal(await page.locator('body').evaluate(n=>n.scrollWidth<=320),true);
assert.equal(await page.locator('.plan-suggestion h3').first().evaluate(n=>n.clientHeight<=44),true);
if(screenshotDir){
  await page.setViewportSize({width:320,height:1100});
  await page.locator('#plan-dialog').evaluate(n=>{n.scrollTop=0;});
  await page.screenshot({path:path.join(screenshotDir,theme+'.png'),fullPage:true});
  await page.setViewportSize({width:320,height:700});
}
await page.getByRole('button',{name:'Продолжить с выбранными'}).click();
assert.equal(await page.locator('.plan-draft-row').count(),1);assert.equal(await page.getByLabel('Название дела 1',{exact:true}).inputValue(),'Подготовить заявление');
await page.locator('.plan-information summary').click();await page.getByText(info.text,{exact:true}).waitFor();
await page.getByLabel('Название цели',{exact:true}).fill('Цель\nиз нескольких строк');
assert.equal(await page.getByLabel('Название цели',{exact:true}).evaluate(n=>n.scrollHeight<=n.clientHeight+2),true);
await page.getByRole('button',{name:'Подтвердить план',exact:true}).click();await page.locator('#plan-dialog').waitFor({state:'hidden'});
assert.equal(commit.tasks.length,1);assert.deepEqual(commit.tasks[0].material_ids,['docs']);assert.deepEqual(commit.context,proposal.context);assert.equal('links' in commit.tasks[0],false);assert.equal('description' in commit.tasks[0],false);
await page.locator('.plan-goal .plan-information').waitFor();await page.locator('.plan-task .plan-source-links').waitFor();assert.deepEqual(errors,[]);
await page.reload();await page.locator('#plan-list .plan-goal').waitFor();assert.equal(latestCalls,0);
await page.evaluate(()=>planner.show(undefined,'review'));await page.locator('.plan-suggestion').first().waitFor();assert.equal(latestCalls,1);assert.equal(await page.locator('[aria-pressed="true"]').count(),0);assert.equal(aiCalls,1);
await page.getByRole('button',{name:'Закрыть форму',exact:true}).click();latestAvailable=false;await page.evaluate(()=>planner.show(undefined,'review'));assert.equal(latestCalls,2);assert.equal(await page.locator('#plan-status').innerText(),'Откройте сохранённый план или получите новое предложение.');assert.equal(await page.locator('#plan-dialog').evaluate(n=>n.open),false);assert.equal(aiCalls,1);
console.log('PASS compact planner at 320px '+theme+': unselected cards, toggle, selected-only commit, retained information, sources, textarea, no overflow, live/recreated return, empty-cache fallback');await page.close();
}
}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
