const test=require('node:test'),assert=require('node:assert/strict');
const codec=require('../static/planner-draft-codec.js');
test('rich repeated answer snapshots round-trip exactly without duplicating sources',()=>{
 const source={id:'housing',title:'Условия',url:'https://example.org/law',description:'Проверенные условия'};
 const points=[{text:'Первое целое предложение.',sources:[source]},{text:'Второе целое предложение.',sources:[source]}];
 const info={intro:'Введение.',points,details:[{title:'Материал',text:'Полный исходный текст. '.repeat(200),sources:[source]}],sources:[source],priority:'normal',note:'Все условия ниже.'};
 info.text=info.intro+'\n\n'+points.map(p=>'• '+p.text).join('\n')+'\n\n'+info.note;
 const state={kind:'consultation',information:info,proposal:{information:info,tasks:[{title:'Дело'}]},selection:[0],original:'Вопрос'};
 const packed=codec.encode(state);assert.equal(packed.information_pool_v1.length,1);
 assert.deepEqual(codec.decode(JSON.parse(JSON.stringify(packed))),state);
 assert.ok(JSON.stringify(packed).length<JSON.stringify(state).length*.6);
 assert.deepEqual(codec.encode(codec.decode(packed)),packed);
});
test('old drafts and urgent full source text remain unchanged after restoration',()=>{
 const legacy={kind:'editor',proposal:{information:{text:'Старый ответ.',sources:[]}}};assert.deepEqual(codec.decode(codec.encode(legacy)),legacy);
 const urgent={kind:'consultation',information:{text:'Полная проверенная памятка.',sources:[],priority:'urgent'}};assert.deepEqual(codec.decode(codec.encode(urgent)),urgent);
});
test('invalid source references fail explicitly',()=>{
 assert.throws(()=>codec.decode({kind:'editor',information:{planner_information_ref_v1:4},information_pool_v1:[]}),/восстановить/);
});
