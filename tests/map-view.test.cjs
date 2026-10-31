const {test}=require('node:test');
const assert=require('node:assert/strict');
const {tree,connections}=require('../web/map-view.js');
const nodes=[{id:'root',title:'Root',parent:null},{id:'child',title:'Child',parent:'root'},{id:'leaf',title:'Leaf',parent:'child'}];
const card=n=>`<button data-node="${n.id}">${n.title}</button>`;
test('collapsed branch hides descendants and retains its disclosure control',()=>{
 const html=tree(nodes,card,new Set(['child']));
 assert.ok(html.includes('data-node="child"'));
 assert.ok(!html.includes('data-node="leaf"'));
 assert.match(html,/data-branch="child" aria-expanded="false"/);
 assert.ok(!/<button[^>]*>[^<]*<button/.test(html));
});
test('search can expose a result inside a collapsed branch',()=>{
 const html=tree(nodes,card,new Set(['root']),true);
 assert.ok(html.includes('data-node="leaf"'));
 assert.match(html,/aria-expanded="true"/);
});
test('filtered orphan is rendered and disclosure labels escape untrusted titles',()=>{
 const html=tree([{id:'child',title:'<img onerror="bad">',parent:'missing'},{id:'leaf',title:'Leaf',parent:'child'}],card);
 assert.ok(html.includes('data-node="child"'));
 assert.ok(html.includes('&lt;img onerror=&quot;bad&quot;&gt;'));
});
const boxes={root:{x:0,y:50,width:200,height:100},child:{x:300,y:0,width:200,height:100},leaf:{x:600,y:120,width:200,height:80}};
test('parent curves connect actual card edges and omit collapsed descendants',()=>{
 const paths=connections(nodes,[],boxes);
 assert.equal(paths.length,2);
 assert.match(paths[0].path,/^M 200 100 C .*300 50$/);
 const visible={root:boxes.root,child:boxes.child};
 assert.equal(connections(nodes,[],visible).length,1);
 assert.equal(connections(nodes,[],{}).length,0);
});
test('dependency arrow starts at prerequisite and ends at dependent',()=>{
 const paths=connections(nodes,[{source:'child',target:'leaf'}],boxes);
 const link=paths.find(p=>p.type==='dependency');
 assert.equal(link.source,'leaf');assert.equal(link.target,'child');
 assert.match(link.path,/^M 740 200 C .*440 100$/);
 assert.ok(paths.every(p=>!p.path.includes('NaN')));
});
test('outline connectors start below parent instead of its right edge',()=>{
 const path=connections(nodes,[],boxes,'outline')[0].path;
 assert.match(path,/^M 18 150 C /);
});
