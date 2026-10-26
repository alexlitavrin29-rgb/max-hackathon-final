/* Synthetic 320px form: explicit save, safe preview, CAS and saved-only PDF. */
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
 try{for(const theme of ['light','dark']){
  const page=await browser.newPage({viewport:{width:320,height:780},colorScheme:theme});
  await page.setContent('<main class="shell"><section id="resume" hidden></section></main>');
  await page.evaluate(theme=>document.documentElement.dataset.theme=theme,theme);
  for(const sheet of ['style.css','max-ui.css'])await page.addStyleTag({path:path.resolve(__dirname,'../static',sheet)});
  await page.addStyleTag({path:path.resolve(__dirname,'../static/resume.css')});
  await page.addScriptTag({path:path.resolve(__dirname,'../static/resume.js')});
  await page.evaluate(async()=>{
   window.writes=[];window.pdfs=0;window.saved=null;window.resume=window.createResume({
    request:async(path,options)=>{if(!options){await new Promise(resolve=>window.finishResumeLoad=resolve);return {resume:window.saved};}const body=JSON.parse(options.body);window.writes.push(body);if(window.conflict)throw new Error('Изменилось в другом окне');window.saved={version:8,state:body.state};return {resume:window.saved};},
    requestBlob:async()=>{window.pdfs++;return new Blob(['%PDF-fixture'],{type:'application/pdf'});}
   });window.resumeOpen=window.resume.open();
  });
  const initialName=page.locator('#resume-name');
  assert.equal(await initialName.isDisabled(),true);
  assert.equal(await page.getByText('Далее →',{exact:true}).isDisabled(),true);
  const immediateTyping=initialName.fill('Анна <script>window.injected=true</script>');
  await page.evaluate(()=>window.finishResumeLoad());
  await immediateTyping;await page.evaluate(()=>window.resumeOpen);
  assert.equal(await initialName.inputValue(),'Анна <script>window.injected=true</script>');
  const colors=await page.locator('#resume-name').evaluate(input=>{
   const style=getComputedStyle(input),channels=color=>color.match(/[\d.]+/g).slice(0,3).map(Number);
   const luminance=color=>channels(color).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
   const foreground=luminance(style.color),background=luminance(style.backgroundColor);
   return {background:style.backgroundColor,contrast:(Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05)};
  });
  if(theme==='dark')assert.notEqual(colors.background,'rgb(255, 255, 255)');
  assert.ok(colors.contrast>=4.5,'Resume input text must be readable: '+JSON.stringify(colors));
  assert.equal(await page.locator('#resume-education').isVisible(),false);
  assert.equal(await page.locator('.resume-preview').isVisible(),false);
  const name=page.locator('#resume-name');await name.fill('Анна <script>window.injected=true</script>');
  await page.getByText('Далее →',{exact:true}).click();
  assert.equal(await name.isVisible(),false);
  await page.locator('#resume-experience').fill('Реальная практика');
  await page.getByText('Пока нет опыта работы',{exact:true}).check();
  assert.equal(await page.locator('#resume-experience').isDisabled(),true);
  await page.getByText('Далее →',{exact:true}).click();
  assert.equal(await page.locator('.resume-preview').isVisible(),true);
  assert.equal(await page.evaluate(()=>window.writes.length),0);
  assert.equal(await page.getByText('Скачать сохранённое PDF',{exact:true}).isDisabled(),true);
  assert.equal(await page.evaluate(()=>!!window.injected),false);
  await page.getByText('Сохранить резюме',{exact:true}).click();
  await page.waitForFunction(()=>window.writes.length===1);
  assert.equal(await page.evaluate(()=>window.writes[0].expected_version),0);
  assert.equal(await page.getByText('Скачать сохранённое PDF',{exact:true}).isDisabled(),false);
  await page.getByText('Скачать сохранённое PDF',{exact:true}).click();
  await page.waitForFunction(()=>window.pdfs===1);
  await page.getByText('← Предыдущий шаг',{exact:true}).click();await page.getByText('← Предыдущий шаг',{exact:true}).click();
  await name.fill('Новое имя');
  assert.equal(await page.getByText('Скачать сохранённое PDF',{exact:true}).isDisabled(),true);
  await page.evaluate(async()=>{window.resume.hide();await window.resume.open();});
  assert.equal(await name.inputValue(),'Новое имя');
  await page.getByText('Далее →',{exact:true}).click();await page.getByText('Далее →',{exact:true}).click();
  await page.evaluate(()=>window.conflict=true);await page.getByText('Сохранить резюме',{exact:true}).click();
  await page.getByRole('status').getByText('Изменилось в другом окне').waitFor();
  assert.equal(await name.inputValue(),'Новое имя');
  assert.equal(await page.evaluate(()=>window.writes[1].expected_version),8);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=320),true);
  await page.evaluate(()=>window.resume.reset());assert.equal(await page.locator('#resume').isHidden(),true);
  assert.equal(await page.locator('#resume').textContent(),'');await page.close();
 }
 console.log('resume 320px light/dark explicit-save/CAS/PDF/safe-preview checks passed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
