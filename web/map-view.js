/* Pure map rendering shared by the page and focused regression checks. */
(function (root) {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function tree(nodes, card, collapsed = new Set(), forceOpen = false, parent = null, depth = 0) {
    if (depth > 40) return '<p class="muted">Глубокая ветка — уточните поиск.</p>';
    const ids = new Set(nodes.map(n => n.id));
    const children = nodes.filter(n => n.parent === parent || (!parent && !ids.has(n.parent)));
    return '<ul class="tree">' + children.map(n => {
      const count = nodes.filter(x => x.parent === n.id).length;
      const open = forceOpen || !collapsed.has(n.id);
      return `<li><div class="branch-head">${card(n)}${count ? `<button class="branch-toggle" data-branch="${escape(n.id)}" aria-expanded="${open}" aria-label="${open?'Свернуть':'Развернуть'} ветку: ${escape(n.title)}">${open?'−':'+'}<span>${count}</span></button>` : ''}</div>${count && open ? tree(nodes, card, collapsed, forceOpen, n.id, depth + 1) : ''}</li>`;
    }).join('') + '</ul>';
  }
  function connections(nodes, edges, boxes, layout = 'mindmap') {
    const result = [];
    const point=(x,y)=>`${Math.round(x*10)/10} ${Math.round(y*10)/10}`;
    for(const n of nodes) {
      const a=boxes[n.parent],b=boxes[n.id];
      if(!a||!b)continue;
      let path;
      if(layout==='outline') {
        const x=a.x+18,y=a.y+a.height,tx=b.x,ty=b.y+b.height/2;
        path=`M ${point(x,y)} C ${point(x,ty)} ${point(x,ty)} ${point(tx,ty)}`;
      } else {
        const x=a.x+a.width,y=a.y+a.height/2,tx=b.x,ty=b.y+b.height/2;
        const bend=Math.max(20,(tx-x)*.5);
        path=`M ${point(x,y)} C ${point(x+bend,y)} ${point(tx-bend,ty)} ${point(tx,ty)}`;
      }
      result.push({source:n.parent,target:n.id,type:'parent',path});
    }
    for(const e of edges) {
      // A dependency arrow points from the prerequisite to the dependent task.
      const a=boxes[e.target],b=boxes[e.source];
      if(!a||!b)continue;
      const x=a.x+a.width*.7,y=a.y+a.height,tx=b.x+b.width*.7,ty=b.y+b.height;
      const lane=Math.max(y,ty)+25+(result.filter(r=>r.type==='dependency').length%5)*9;
      const path=`M ${point(x,y)} C ${point(x,lane)} ${point(tx,lane)} ${point(tx,ty)}`;
      result.push({source:e.target,target:e.source,type:'dependency',path});
    }
    return result;
  }
  const api = { tree, connections };
  if (typeof module !== 'undefined') module.exports = api;
  else root.MapView = api;
})(typeof window !== 'undefined' ? window : globalThis);
