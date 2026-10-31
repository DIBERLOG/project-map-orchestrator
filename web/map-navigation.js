(function(root){
  'use strict';
  function attach(canvas){
    let gesture=null;
    const interactive='button,a,input,select,textarea,[contenteditable="true"]';
    function down(e){
      if(e.pointerType!=='mouse'||![0,1].includes(e.button))return;
      if(e.button===0&&e.target.closest(interactive))return;
      gesture={id:e.pointerId,x:e.clientX,y:e.clientY,left:canvas.scrollLeft,top:canvas.scrollTop};
      canvas.setPointerCapture(e.pointerId);
      canvas.focus({preventScroll:true});
      canvas.classList.add('is-panning');
      e.preventDefault();
    }
    function move(e){
      if(!gesture||gesture.id!==e.pointerId)return;
      canvas.scrollLeft=gesture.left-(e.clientX-gesture.x);
      canvas.scrollTop=gesture.top-(e.clientY-gesture.y);
      e.preventDefault();
    }
    function end(e){
      if(!gesture||gesture.id!==e.pointerId)return;
      const id=gesture.id;gesture=null;
      canvas.classList.remove('is-panning');
      if(canvas.hasPointerCapture(id))canvas.releasePointerCapture(id);
    }
    const handlers={pointerdown:down,pointermove:move,pointerup:end,pointercancel:end,lostpointercapture:end};
    for(const [name,fn] of Object.entries(handlers))canvas.addEventListener(name,fn);
    return ()=>{
      if(gesture)end({pointerId:gesture.id});
      for(const [name,fn] of Object.entries(handlers))canvas.removeEventListener(name,fn);
    };
  }
  if(typeof module!=='undefined')module.exports={attach};else root.MapNavigation={attach};
})(typeof window!=='undefined'?window:globalThis);
