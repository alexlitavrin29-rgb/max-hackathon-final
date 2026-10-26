const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const file=path.join(__dirname,'../static/material-link-text.js');
test('plain words get specific links without changing visible factual text',()=>{
 assert.ok(fs.existsSync(file),'inline-link helper missing');
 const {segments}=require(file),text='Подготовь паспорт. Если СНИЛС уже есть — возьми его.';
 const parts=segments(text,[{url:'/?handbook=doc_passport',anchors:['паспорт']},{url:'/?handbook=doc_snils_proof',anchors:['СНИЛС']}],'https://app.test');
 assert.equal(parts.map(p=>p.text).join(''),text);
 assert.deepEqual(parts.filter(p=>p.url).map(p=>p.url),['https://app.test/?handbook=doc_passport','https://app.test/?handbook=doc_snils_proof']);
});
test('existing markdown is not nested, longest anchor wins, unrelated word forms stay intact',()=>{
 const {segments}=require(file);
 const s=segments('[Паспорт](https://official.test/doc) и паспортные данные; трудовой договор.',[
 {url:'/?handbook=a',anchors:['паспорт','договор']},{url:'/?handbook=b',anchors:['трудовой договор']}],'https://app.test');
 assert.equal(s.filter(p=>p.url).length,2);assert.equal(s.filter(p=>p.url)[1].url,'https://app.test/?handbook=b');
 assert.ok(s.map(p=>p.text).join('').includes('паспортные данные'));
});
test('unsafe URLs rejected and external query cannot disguise an internal route',()=>{
 const {segments}=require(file);
 for(const url of ['javascript:alert(1)','https://secret:pass@bad.test/','https://bad.test/\nx'])assert.equal(segments('паспорт',[{url,anchors:['паспорт']}],'https://app.test').filter(p=>p.url).length,0);
 const x=segments('паспорт',[{url:'https://external.test/?handbook=x',anchors:['паспорт']}],'https://app.test').find(p=>p.url);assert.equal(x.external,true);
});
