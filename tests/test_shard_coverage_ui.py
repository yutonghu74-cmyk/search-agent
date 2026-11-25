import json
import subprocess
import unittest
from dsh_retrieval.chat_ui import CHAT_JS


class ShardCoverageUiTest(unittest.TestCase):
    def test_unknown_partial_and_complete_are_distinguished(self):
        start = CHAT_JS.index('function shardCoverageSummary(')
        end = CHAT_JS.index('function retrievalSourceSummary(', start)
        script = CHAT_JS[start:end] + '''
const assert=require('node:assert/strict');
assert.equal(shardCoverageSummary(null),'');
assert.match(shardCoverageSummary({known:false,queries:[]}),/覆盖未知/);
const row={library_id:'synthetic',total:7,visited:4,pending:3,failed:1,exhausted:0,complete:false};
let text=shardCoverageSummary({known:true,queries:[row]});
assert.match(text,/总切片 7/);assert.match(text,/尚未查询 3/);
assert.match(text,/失败 1/);assert.match(text,/尚未搜尽/);
text=shardCoverageSummary({known:true,queries:[{...row,visited:7,pending:0,failed:0,exhausted:7,complete:true}]});
assert.match(text,/该查询范围已搜尽/);assert.match(text,/不代表全库搜尽/);
assert.match(shardCoverageSummary({known:true,queries:[]}),/覆盖未知/);
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('coverage.textContent=shardCoverageSummary(searchProgress.coverage)', CHAT_JS)
