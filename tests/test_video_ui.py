import json
import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_HTML, CHAT_JS
from dsh_retrieval.proposals import normalize_proposal
from dsh_retrieval.video_ui import VIDEO_DRAFT_JS


@unittest.skipUnless(shutil.which('node'), 'Node required for browser behavior tests')
class VideoUITest(unittest.TestCase):
    def run_js(self, script):
        source = VIDEO_DRAFT_JS[:VIDEO_DRAFT_JS.index("$('open-video-draft')")]
        result = subprocess.run(['node', '-e', script], env={**os.environ, 'SOURCE': source},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_builder_is_accepted_by_backend_and_included_in_chat(self):
        output = self.run_js("const f=new Function(process.env.SOURCE+';return videoDraftBody;')();console.log(JSON.stringify(f('室外有人喝水，不要水印',12)));")
        spec = normalize_proposal(json.loads(output))['query_spec']
        self.assertEqual(spec['count_unit'], 'distinct_video')
        self.assertEqual(spec['video_conditions']['clauses'][0]['predicate'], '室外有人喝水，不要水印')
        self.assertIn('id="open-video-draft"', CHAT_HTML)
        self.assertIn('id="video-draft-dialog"', CHAT_HTML)
        self.assertIn('function saveVideoDraft()', CHAT_JS)

    def test_retry_reuses_key_no_dispatch_and_edits_preserve_policies(self):
        self.run_js(r"""
(async()=>{
 const assert=require('assert/strict');let calls=[],count=0,fail=true,release;
 const fields={};const $=id=>fields[id]??=( {value:'',textContent:'',disabled:false} );
 const api=async(path,options)=>{calls.push({path,options});if(fail){fail=false;throw Error('network')}
   if(release===undefined)await new Promise(resolve=>{release=resolve});
   const body=options.body;return {draft_id:'draft_'+ 'a'.repeat(32),draft_version:2,proposal:body,
    video_summary:{requires_scope_update:false},confirmed_task_id:null}};
 const f=new Function('$','api','key',process.env.SOURCE+';return {saveVideoDraft,newVideoDraft,showVideoDraft,videoDraftBody,state:videoDraftState}')($,api,()=>String(++count));
 $('video-draft-query').value='有人喝水';$('video-draft-count').value='5';
 await f.saveVideoDraft();assert.equal(calls.length,1);const key=calls[0].options.keyValue;
 const saving=f.saveVideoDraft();await Promise.resolve();await f.saveVideoDraft();assert.equal(calls.length,2);
 assert.equal(calls[1].options.keyValue,key);assert.equal($('video-draft-query').disabled,true);
 f.newVideoDraft();assert.equal($('video-draft-query').value,'有人喝水');
 release();await saving;assert.equal($('video-draft-query').disabled,false);
 assert.equal(f.state.draft.draft_id,'draft_'+ 'a'.repeat(32));
 const stored=f.state.draft;stored.proposal.query_spec.source_constraints=[{source:'authorized-source'}];
 stored.proposal.query_spec.business_domain='existing-business';
 $('video-draft-query').value='户外有人喝水';await f.saveVideoDraft();
 const edit=calls.at(-1);assert.equal(edit.options.body.expected_draft_version,2);
 assert.deepEqual(edit.options.body.query_spec.source_constraints,[{source:'authorized-source'}]);
 assert.equal(edit.options.body.query_spec.business_domain,'existing-business');
 assert.ok(calls.every(c=>c.path.includes('/drafts')));
 assert.match($('video-draft-status').textContent,/未创建正式任务/);
})().catch(error=>{console.error(error);process.exit(1)});
""")

    def test_legacy_or_complex_draft_is_not_silently_flattened(self):
        self.run_js(r"""
(async()=>{
 const assert=require('assert/strict');const fields={};const $=id=>fields[id]??={value:'',textContent:'',disabled:false};let calls=0;
 const f=new Function('$','api','key',process.env.SOURCE+';return {saveVideoDraft,showVideoDraft,videoDraftBody}')($,async()=>{calls++},()=> 'k');
 const proposal=f.videoDraftBody('有人喝水',5);
 const draft={draft_id:'draft_'+ 'b'.repeat(32),draft_version:1,proposal,video_summary:{requires_scope_update:true}};
 f.showVideoDraft(draft);await f.saveVideoDraft();assert.equal(calls,0);assert.equal($('video-draft-save').disabled,true);
 draft.video_summary.requires_scope_update=false;
 proposal.query_spec.video_conditions.clauses.push({id:'visual_2',channel:'visual',predicate:'有杯子'});
 f.showVideoDraft(draft);await f.saveVideoDraft();assert.equal(calls,0);
 assert.equal(proposal.query_spec.video_conditions.clauses.length,2);
})().catch(error=>{console.error(error);process.exit(1)});
""")
