import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from agent_v2 import Agent, Store, now
from providers import ProviderPool, Deferred


def large_record():
    return {'article':{'title':'إعلان رسمي عن تحديث تقني جديد','excerpt':'تفاصيل تحديث تقني وفق الإعلان الرسمي.',
        'image_prompt':'laptop','sections':[{'heading':'تفاصيل تقنية',
        'text':' '.join(['تقنيات الذكاء الاصطناعي تساعد المستخدمين في العمل اليومي دون مبالغة']*6),
        'source_ids':[1,2]} for _ in range(6)]},
        'evidence':{'url':'https://blogs.windows.com/test/','date':now().isoformat(),
        'blocks':[{'id':1,'text':'Official technology source facts. '*34},
                  {'id':2,'text':'Additional official source facts. '*34}]},'reviewed':False}

class PromptRecoveryTests(unittest.TestCase):
    def test_large_review_is_split_without_discarding_text(self):
        record=large_record()
        self.assertGreater(len(json.dumps(record,ensure_ascii=False).encode()),7600)
        packets=Agent.review_prompts(record)
        self.assertGreater(len(packets),1)
        self.assertTrue(all(len(p.encode())+800+400<=7600 for p in packets))
        recovered=[]
        for packet in packets[1:]:
            obj=json.loads(packet[packet.index('{"title":'):])
            recovered.extend(s['text'] for s in obj['sections'])
        expected=' '.join(s['text'] for s in record['article']['sections']).split()
        self.assertEqual(' '.join(recovered).split(),expected)

    def test_correction_prompt_does_not_embed_prior_article(self):
        record=large_record()
        prompt=Agent.writer_prompt(record['evidence'],['Use shorter paragraphs'])
        self.assertNotIn(record['article']['sections'][0]['text'],prompt)
        self.assertLessEqual(len(prompt.encode())+3200+400,7600)

    def test_gemini_503_falls_back_to_fitting_groq_review(self):
        limits={k:{'requests':16,'tokens':100000,'images':0} for k in ('groq','gemini')}
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite')
            with closing(store.db):
                pool=ProviderPool(store,{'limits':limits})
                gemini=Mock(status_code=503,headers={})
                groq=Mock(status_code=200,ok=True,headers={})
                groq.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{"pass":true,"issues":[]}'}}]}
                pool.session.post=Mock(side_effect=[gemini,groq])
                prompt=Agent.review_prompts(large_record())[0]
                with patch.dict('os.environ',{'GROQ_API_KEY':'test','GEMINI_API_KEY':'test'}):
                    self.assertEqual(pool.text(prompt,reviewer=True),{'pass':True,'issues':[]})
                self.assertEqual(pool.session.post.call_count,2)
                payload=pool.session.post.call_args.kwargs['json']
                self.assertEqual(payload['max_completion_tokens'],800)
                self.assertTrue(store.cooldown('gemini'))

    def test_passed_packets_resume_after_provider_failure(self):
        record=large_record(); agent=Agent.__new__(Agent); agent.pool=Mock()
        agent.pool.text.side_effect=[{'pass':True,'issues':[]},Deferred('provider busy',65)]
        with self.assertRaises(Deferred): agent.review(record)
        self.assertEqual(len(record['review_packets']),1)
        record=json.loads(json.dumps(record))
        agent.pool=Mock(); agent.pool.text.return_value={'pass':True,'issues':[]}
        expected=len(Agent.review_prompts(record))-1
        self.assertEqual(agent.review(record),{'pass':True,'issues':[]})
        self.assertEqual(agent.pool.text.call_count,expected)

    def test_cooldown_does_not_consume_quality_failure_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=Agent.__new__(Agent); agent.store=Store(Path(directory)/'db.sqlite')
            with closing(agent.store.db):
                stamp=(now()+timedelta(hours=3)).isoformat()
                agent.store.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(stamp,'url','slug',83702,None,'new')); agent.store.db.commit()
                meta={'runtime_revision':3,'attempt_day':now().date().isoformat(),'attempts':2,
                      'source':{'url':'url','date':now().isoformat()},'record':large_record()}
                agent.store.put_meta(stamp,meta)
                agent.review=Mock(side_effect=Deferred('provider cooling down',90))
                agent.api=Mock()
                with self.assertLogs(level='WARNING'):
                    agent.process(agent.store.db.execute('SELECT * FROM jobs').fetchone())
                result=agent.store.meta(stamp)
                self.assertEqual(result['attempts'],2)
                self.assertGreater(result['retry_at'],now().timestamp())
                self.assertFalse(result.get('terminal',False)); agent.api.assert_not_called()

    def test_both_cooldowns_do_not_make_api_calls(self):
        limits={k:{'requests':16,'tokens':100000,'images':0} for k in ('groq','gemini')}
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'db.sqlite')
            with closing(store.db):
                store.pause('groq',65); store.pause('gemini',300)
                pool=ProviderPool(store,{'limits':limits}); pool.session.post=Mock()
                with patch.dict('os.environ',{'GROQ_API_KEY':'test','GEMINI_API_KEY':'test'}):
                    with self.assertRaises(Deferred): pool.text('Return JSON')
                pool.session.post.assert_not_called()
                self.assertEqual(store.db.execute('SELECT count(*) FROM usage').fetchone()[0],0)

if __name__=='__main__': unittest.main()
