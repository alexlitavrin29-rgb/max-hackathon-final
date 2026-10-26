/* Synthetic editor interaction, not owner acceptance or model evaluation. */
const {chromium}=require('playwright'),http=require('http'),fs=require('fs'),path=require('path');
(async()=>{
 const directory=process.argv[2];if(!directory)throw Error('Usage: node eval_stand_browser.cjs OUTPUT_DIRECTORY');
 const html=fs.readFileSync(path.join(directory,'review.html'));
 const server=http.createServer((req,res)=>{res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html)});
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 let browser;
 try{
  browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});
  for(const colorScheme of ['light','dark']){
   const context=await browser.newContext({viewport:{width:390,height:844},colorScheme,acceptDownloads:true});
   const p=await context.newPage();await p.goto('http://127.0.0.1:'+server.address().port);
   if(await p.locator('article').count()!==15)throw Error('missing cases');
   if(await p.locator('article').first().locator('.compare>div').count()!==2)throw Error('missing comparison');
   if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('overflow');
   await p.locator('article').first().locator('input[value=water]').check();
   await p.locator('article').first().locator('textarea').fill('СИНТЕТИЧЕСКАЯ ПРОВЕРКА СОХРАНЕНИЯ');await p.reload();
   if(await p.locator('article').first().locator('textarea').inputValue()!=='СИНТЕТИЧЕСКАЯ ПРОВЕРКА СОХРАНЕНИЯ')throw Error('review not persisted');
   const event=p.waitForEvent('download');await p.getByRole('button',{name:'Скачать оценки редактора'}).click();
   const download=await event;await download.saveAs(path.join(directory,'editor-test-'+colorScheme+'.json'));
   const review=JSON.parse(fs.readFileSync(path.join(directory,'editor-test-'+colorScheme+'.json')));
   if(!/^[a-f0-9]{64}$/.test(review.snapshot_sha256)||review.rows[0].rating!=='water')throw Error('export attribution failed');
   await p.evaluate(()=>scrollTo(0,0));await p.screenshot({path:path.join(directory,'review-'+colorScheme+'.png')});
   console.log(colorScheme+' PASS (15cases, comparison,390px,restore,attributed JSON export)');await context.close();
  }
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exit(1)});
