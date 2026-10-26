const {test}=require('node:test');
const assert=require('node:assert/strict');
const M=require('../static/screen-model.js');
test('categories include children but exclude empty fixed categories',()=>{
 assert.deepEqual(M.categories({categories:[{id:'work'},{id:'housing'},{id:'health'}],entries:[{category:'work',parent:'a'},{category:'health'}]}).map(c=>c.id),['work','health']);
});
test('related handbook categories offer the appropriate existing search',()=>{
 assert.equal(M.searchTarget('housing'),'rental');
 assert.equal(M.searchTarget('work'),'work');
 assert.equal(M.searchTarget('benefits'),'work');
 assert.equal(M.searchTarget('health'),null);
});
test('question limits count Unicode points and never truncate',()=>{
 assert.equal(M.validQuestion('😀'.repeat(160)),true);
 assert.equal(M.validQuestion('😀'.repeat(161)),false);
 assert.equal(M.validQuestion('   '),false);
});
