const {test}=require('node:test');
const assert=require('node:assert/strict');
const {attach}=require('../web/map-navigation.js');
function surface(){
 const canvas=new EventTarget();canvas.scrollLeft=100;canvas.scrollTop=80;
 let captured=null;const classes=new Set();
 canvas.classList={add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)};
 canvas.setPointerCapture=id=>captured=id;canvas.hasPointerCapture=id=>captured===id;canvas.releasePointerCapture=()=>captured=null;canvas.focus=()=>{};
 canvas.closest=()=>null;
 return canvas;
}
function pointer(canvas,type,props={}){
 const e=new Event(type,{cancelable:true});
 for(const [key,value] of Object.entries({pointerType:'mouse',pointerId:1,button:0,clientX:50,clientY:50,...props}))Object.defineProperty(e,key,{value});
 canvas.dispatchEvent(e);return e;
}
test('background drag scrolls both axes and ends on release',()=>{
 const c=surface();attach(c);pointer(c,'pointerdown');pointer(c,'pointermove',{clientX:70,clientY:30});
 assert.equal(c.scrollLeft,80);assert.equal(c.scrollTop,100);assert.ok(c.classList.contains('is-panning'));
 pointer(c,'pointerup');pointer(c,'pointermove',{clientX:90,clientY:90});
 assert.equal(c.scrollLeft,80);assert.ok(!c.classList.contains('is-panning'));assert.ok(!c.hasPointerCapture(1));
});
test('left clicking a card preserves click behavior without starting drag',()=>{
 const c=surface();c.closest=()=>({tagName:'BUTTON'});attach(c);
 const e=pointer(c,'pointerdown');pointer(c,'pointermove',{clientX:80});
 assert.equal(c.scrollLeft,100);assert.equal(e.defaultPrevented,false);
});
test('middle mouse can pan over cards; cancellation and cleanup release capture',()=>{
 const c=surface();c.closest=()=>({});const detach=attach(c);
 pointer(c,'pointerdown',{button:1});pointer(c,'pointermove',{clientX:40});assert.equal(c.scrollLeft,110);
 pointer(c,'pointercancel');assert.ok(!c.hasPointerCapture(1));
 pointer(c,'pointerdown',{button:1});detach();assert.ok(!c.classList.contains('is-panning'));
 pointer(c,'pointermove',{clientX:20});assert.equal(c.scrollLeft,110);
});
test('touch remains native and unrelated pointers do not move the map',()=>{
 const c=surface();attach(c);pointer(c,'pointerdown',{pointerType:'touch'});pointer(c,'pointermove',{clientX:90});assert.equal(c.scrollLeft,100);
 pointer(c,'pointerdown');pointer(c,'pointermove',{pointerId:2,clientX:90});assert.equal(c.scrollLeft,100);
});
