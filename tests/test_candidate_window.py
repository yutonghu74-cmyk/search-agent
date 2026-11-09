"""Execute the candidate gallery against a small DOM adapter in Node."""
import os
import shutil
import subprocess
import unittest

from dsh_retrieval.candidate_ui import CANDIDATE_JS


@unittest.skipUnless(shutil.which('node'), 'Node required for gallery behavior')
class CandidateWindowTest(unittest.TestCase):
    def test_gallery_pages_release_images_and_keep_navigation_and_resume(self):
        script = r'''
const assert=require('node:assert/strict');
let revoked=0,aborted=0,disconnected=0;
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.scrollTop=0;this.className='';this.classList={add(){},remove(){},toggle(){},contains(){return false}}}
 append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node)}}
 replaceChildren(...nodes){this.children=[];this.append(...nodes)}
 querySelectorAll(selector){return this.children.flatMap(n=>[...(n.tag===selector?[n]:[]),...n.querySelectorAll(selector)])}
 querySelector(selector){if(selector[0]==='.')return this.children.find(n=>n.className===selector.slice(1));return this.querySelectorAll(selector)[0]}
 remove(){this.parent.children=this.parent.children.filter(n=>n!==this)}
 setAttribute(){} addEventListener(){}
}
const elements=new Map(),$=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const document={createElement:tag=>new Element(tag)};
const URL={revokeObjectURL(){revoked++}};
const IntersectionObserver=class{observe(){}disconnect(){disconnected++}};
const sessionStorage={setItem(){}};
const state={task:{task_id:'t',project_id:'p',confirmed_revision:1,run_generation:1}};
const aggregateLocalCandidates=items=>items.map(item=>({item,previewItem:item.preview_status==='available'?item:null,sources:[],indexes:[]}));
eval(process.env.SOURCE+`\n
candidateDialog.open=true;
const view={scope:candidateScope(),taskId:'t',tab:'accepted',items:[],next:null,previewOnly:true};candidateActive=view;
for(let page=0;page<100;page++){
 const items=Array.from({length:60},(_,i)=>({candidate_id:'c'+(page*60+i),preview_status:'available'}));
 candidateRememberPage(view,items,page?'cursor'+page:null);view.items.push(...items);view.lastPageCursor=page?'cursor'+page:null;
 candidateRender(view);assert.equal($('candidate-grid').children.length,60);
}
assert.equal(candidateResumeCursor(view),null,'first page must not resume at last loaded page');
const old=$('candidate-grid').children[0].querySelector('img');old._url='blob:old';old._abort={abort(){aborted++}};
view.page=99;candidateRender(view);
assert.equal($('candidate-grid').children[0].dataset.key,'c5940');
assert.equal($('candidate-grid').children.length,60);assert.equal(revoked,1);assert.equal(aborted,1);assert(disconnected>=60);
assert.equal(candidateResumeCursor(view),'cursor99');
view.selected='c61';assert.equal(candidateResumeCursor(view),'cursor1');view.selected=null;
view.page=0;candidateRender(view);assert.equal($('candidate-grid').children[0].dataset.key,'c0');
assert.equal($('candidate-page-prev').disabled,true);assert.equal($('candidate-page-next').disabled,false);
view.page=10000;candidateRender(view);assert.equal(view.page,99);assert.equal($('candidate-page-next').disabled,true);
view.tab='candidates';view.items=[{candidate_id:'raw1',hit_sequence:1,preview_status:'unavailable'},{candidate_id:'raw2',hit_sequence:2,preview_status:'available'}];
view.itemCursors=new Map();candidateRememberPage(view,view.items,null);candidateRender(view);
assert.equal(view.page,0);assert.equal($('candidate-grid').children.length,1);
view.previewOnly=false;candidateRender(view);assert.equal($('candidate-grid').children.length,2);
view.tab='accepted';view.items=[];view.itemCursors=new Map();view.previousCursors=[];
for(let p=0;p<100;p++){const items=Array.from({length:60},(_,i)=>({candidate_id:'bounded'+(p*60+i)}));candidateRememberPage(view,items,p?'page'+p:null);view.items.push(...items);candidateTrimCache(view);assert(view.items.length<=180);assert(view.itemCursors.size<=180)}
assert.equal(view.items[0].candidate_id,'bounded5820');assert.equal(view.previousCursors[0],null);assert.equal(view.previousCursors.at(-1),'page96');
view.items=[];candidateRender(view);assert.equal($('candidate-grid').children.length,1);assert.equal($('candidate-grid').children[0].className,'candidate-empty');
`);
'''
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': CANDIDATE_JS}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
