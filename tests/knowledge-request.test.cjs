const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');

test('knowledge request preserves retry state and rejects responses for a departed project',async()=>{
  const controls=[{disabled:false},{disabled:false}],attrs=new Map();
  const form={isConnected:true,querySelectorAll:()=>controls,getAttribute:k=>attrs.get(k),setAttribute:(k,v)=>attrs.set(k,v),removeAttribute:k=>attrs.delete(k)};
  const target={textContent:'',innerHTML:'',setAttribute:()=>{}};
  let finish,fail,calls=0;
  const context=vm.createContext({selected:'first',$:s=>s==='#knowledge-form'?form:target,notice:()=>{},esc:x=>String(x),call:()=>{calls++;return new Promise((resolve,reject)=>{finish=resolve;fail=reject;});}});
  const source=fs.readFileSync(require.resolve('../web/app.js'),'utf8');
  vm.runInContext(source.slice(source.indexOf('async function loadKnowledge('),source.indexOf("document.addEventListener('submit',async e=>{if(e.target.id!=='knowledge-form')")),context);
  const run=()=>context.loadKnowledge('map_retrieve','query');
  let pending=run();
  assert.ok(controls.every(x=>x.disabled));assert.match(target.textContent,/Ищем/);
  await run();assert.equal(calls,1,'duplicate submission must not start a second request');
  fail(new Error('Service offline'));await pending;
  assert.match(target.textContent,/Service offline.*повторить/);assert.ok(controls.every(x=>!x.disabled));
  pending=run();finish({method:'lexical',results:[]});await pending;
  assert.match(target.innerHTML,/Совпадений не найдено/);
  pending=run();context.selected='second';target.innerHTML='New project view';
  finish({method:'old',results:[{path:'private-first-project.txt',line:1,text:'old'}]});await pending;
  assert.equal(target.innerHTML,'New project view');
});
