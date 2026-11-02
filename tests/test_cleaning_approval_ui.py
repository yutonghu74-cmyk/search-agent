import os
import shutil
import subprocess
import unittest

from dsh_retrieval.chat_ui import CHAT_JS


class CleaningApprovalUiTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required')
    def test_cards_bind_the_displayed_proposal_and_cancel_without_execution(self):
        source = CHAT_JS[CHAT_JS.index('function showCleaningApproval('):
                         CHAT_JS.index('async function confirmVlmCleaning(')]
        script = r'''
const assert=require('node:assert/strict');
const state={cleaningProposal:null},cards=[],calls=[],elements={};
const $=id=>elements[id]||(elements[id]={value:'',focus(){}});
const card=(title,fields,actions)=>cards.push({title,fields,actions});
const confirmVlmCleaning=(proposal,taskId,key)=>calls.push([proposal,taskId,key]);
const render=new Function('state','card','confirmVlmCleaning','$','message','key',process.env.SOURCE+';return showCleaningApproval;')(state,card,confirmVlmCleaning,$,()=>{},()=> 'stable-key');
const proposal={task_id:'task',revision:1,generation:1,rules:[],steps:[],execution:{model:{id:'m',provider:'p',name:'frozen'},instruction:'complete instruction',prompt_version:'v1',config_digest:'cfg'}};
render('task',proposal);
state.cleaningProposal={...proposal,proposal_digest:'different'};
cards[0].actions[0][1]();assert.equal(calls[0][0],proposal);assert.equal(calls[0][1],'task');
assert.match(JSON.stringify(cards[0].fields),/complete instruction/);
assert.match(JSON.stringify(cards[0].fields),/frozen/);
assert.equal(calls[0][2],'stable-key');
cards[0].actions[1][1]();cards[0].actions[0][1]();assert.equal(calls.length,1);assert.match($('status').textContent,/已取消/);
render('task',{...proposal,execution:null});assert.equal(cards[1].actions[0][2],true);
render('task',proposal);cards[2].actions[2][1]();assert.equal($('request-text').value,'修改清洗条件：');assert.equal(calls.length,1);
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                                env={**os.environ, 'SOURCE': source})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cross_task_confirmation_is_rejected(self):
        self.assertIn('proposal.task_id!==taskId', CHAT_JS)
        self.assertIn('state.task.task_id!==taskId', CHAT_JS)
