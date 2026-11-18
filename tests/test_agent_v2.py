import json
from contextlib import ExitStack, closing, contextmanager
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from agent_v2 import Agent, Store, schedule_slots, ZONE
from providers import ProviderPool, Held

@contextmanager
def managed_store(path):
    """Release SQLite handles before a Windows temporary directory is removed."""
    store = Store(path)
    try:
        yield store
    finally:
        store.db.close()

class Tests(unittest.TestCase):
    def test_store_connection_closes_even_when_test_body_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'db.sqlite'
            store = None
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                with managed_store(path) as store:
                    store.db.execute('SELECT 1')
                    raise RuntimeError('test failure')
            with self.assertRaises(sqlite3.ProgrammingError):
                store.db.execute('SELECT 1')
            path.unlink()


    def test_slots_follow_cairo_dst(self):
        for month in (1,7,11):
            stamp=datetime(2026,month,5,0,tzinfo=timezone.utc)
            values=schedule_slots(stamp)
            self.assertEqual([x.astimezone(ZONE).hour for x in values],[10,20])
            self.assertTrue(all(x>stamp for x in values))
    def test_no_past_slot(self):
        stamp=datetime(2026,11,5,9,45,tzinfo=ZONE)
        self.assertEqual(schedule_slots(stamp)[0].astimezone(ZONE).hour,20)
    def test_free_budget_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            path=Path(d)/'db.sqlite'; s=resources.enter_context(managed_store(path))
            limit={'requests':1,'tokens':100,'images':1}
            s.reserve('cloudflare',0,1,limit)
            s.db.close(); s=resources.enter_context(managed_store(path))
            with self.assertRaises(Held): s.reserve('cloudflare',0,1,limit)
    def test_existing_v1_jobs_preserved(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            p=Path(d)/'db.sqlite'; conn=resources.enter_context(closing(sqlite3.connect(p)))
            conn.execute('CREATE TABLE jobs(slot TEXT PRIMARY KEY,source TEXT UNIQUE,slug TEXT UNIQUE,post INTEGER,media INTEGER,state TEXT)')
            conn.execute("INSERT INTO jobs VALUES('slot','url','slug',83702,NULL,'new')"); conn.commit(); conn.close()
            s=resources.enter_context(managed_store(p))
            self.assertEqual(s.db.execute('SELECT post FROM jobs').fetchone()[0],83702)
    def test_network_failure_spends_attempt_budget(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            s=resources.enter_context(managed_store(Path(d)/'db.sqlite')); cfg={'limits':{'groq':{'requests':2,'tokens':100,'images':0}}}
            pool=ProviderPool(s,cfg); pool.session.post=Mock(side_effect=requests.ConnectionError('reset'))
            with self.assertRaises(Held): pool.request('groq','https://example.com',tokens=30)
            self.assertEqual(s.db.execute('SELECT requests,tokens FROM usage').fetchone(),(1,30))
            self.assertTrue(s.cooldown('groq'))
    def test_429_honors_cooldown(self):
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            s=resources.enter_context(managed_store(Path(d)/'db.sqlite')); cfg={'limits':{'groq':{'requests':2,'tokens':100,'images':0}}}
            pool=ProviderPool(s,cfg)
            pool.session.post=Mock(return_value=Mock(status_code=429,headers={'retry-after':'120'}))
            with self.assertRaises(Held): pool.request('groq','https://example.com')
            with self.assertRaises(Held): pool.request('groq','https://example.com')
            self.assertEqual(pool.session.post.call_count,1)
    def test_unknown_evidence_id_rejected(self):
        a={'title':'عنوان عربي صالح للاختبار','excerpt':'وصف','image_prompt':'laptop','sections':[
            {'heading':'','text':'كلمة '*110,'source_ids':[99]} for _ in range(3)]}
        with self.assertRaisesRegex(ValueError,'Evidence'): Agent.validate(a,{'blocks':[{'id':1,'text':'source'}]})
    def test_unknown_number_rejected(self):
        a={'title':'عنوان عربي صالح للاختبار','excerpt':'وصف','image_prompt':'laptop','sections':[
            {'heading':'','text':'كلمة '*110+' 9999','source_ids':[1]} for _ in range(3)]}
        with self.assertRaisesRegex(ValueError,'number'): Agent.validate(a,{'blocks':[{'id':1,'text':'source'}]})
    def test_schema_and_arabic_length_pass(self):
        a={'title':'عنوان عربي صالح للاختبار','excerpt':'وصف','image_prompt':'laptop','sections':[
            {'heading':'عنوان','text':'كلمة '*110,'source_ids':[1]} for _ in range(3)]}
        self.assertTrue(Agent.validate(a,{'blocks':[{'id':1,'text':'source'}]}))
    def test_image_model_uses_multipart_no_paid_fallback(self):
        pool=ProviderPool(Mock(),{})
        pool.request=Mock(return_value=Mock(headers={'content-type':'image/jpeg'},content=b'image'))
        with patch.dict('os.environ',{'CF_ACCOUNT_ID':'account','CF_API_TOKEN':'secret'}):
            self.assertEqual(pool.image('laptop',7),b'image')
        args,kwargs=pool.request.call_args
        self.assertIn('flux-2-klein-4b',args[1]); self.assertIn('files',kwargs)
        self.assertEqual(kwargs['images'],1)
    def test_published_html_escapes_model_text(self):
        record={'article':{'sections':[{'heading':'<script>x</script>','text':'<img src=x>'}]},'evidence':{'url':'https://example.com'}}
        result=Agent.content(record)
        self.assertNotIn('<script>',result); self.assertNotIn('<img',result)
    def test_arabiatrend_83123_html_structure(self):
        record={'article':{'excerpt':'مقدمة المقال التقني',
            'sections':[{'heading':'عنوان فرعي أول','text':'فقرة أولى.\n\nفقرة ثانية.'}]},
            'evidence':{'url':'https://example.com'}}
        result=Agent.content(record)
        self.assertIn('<h2>عنوان فرعي أول</h2>',result)
        self.assertIn('<p>فقرة أولى.</p><p>فقرة ثانية.</p>',result)
        self.assertNotIn('الإعلان الأصلي',result)
    def test_small_evidence_blocks(self):
        blocks=Agent.blocks(('An official technology announcement. '*10+'\n')*15)
        self.assertLessEqual(sum(len(x['text']) for x in blocks),2400)
        self.assertEqual([x['id'] for x in blocks],list(range(1,len(blocks)+1)))


class WorkflowTests(unittest.TestCase):
    def test_saved_article_generates_image_and_schedules_without_duplicate_create(self):
        import io
        from PIL import Image
        from agent_v2 import now
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            root=Path(d); state=root/'state'; state.mkdir()
            a=Agent.__new__(Agent); a.cfg={'auto_schedule':True}; a.base='https://arabiatrend.com'
            a.store=resources.enter_context(managed_store(state/'jobs.sqlite')); a.pool=Mock()
            slot=(now()+timedelta(hours=3)).isoformat(); source={'url':'https://blogs.windows.com/test/','date':now().isoformat()}
            a.store.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(slot,source['url'],'ata-test',None,None,'new')); a.store.db.commit()
            record={'article':{'title':'خبر تقني عربي بعنوان واضح','excerpt':'ملخص','image_prompt':'a laptop',
                'sections':[{'heading':'','text':'نص عربي','source_ids':[1]}]},
                'evidence':{'url':source['url'],'blocks':[{'id':1,'text':'source'}]},'reviewed':True}
            a.store.put_meta(slot,{'source':source,'record':record})
            out=io.BytesIO(); Image.new('RGB',(1536,768),'navy').save(out,format='PNG')
            a.pool.image.return_value=out.getvalue(); a.pool.inspect_image.return_value={'pass':True,'issues':[],'alt_text':'حاسوب محمول'}
            calls=[]
            def api(path,body=None,raw=None,headers=None):
                calls.append((path,body,raw))
                if path.startswith('posts?') or path.startswith('media?'): return []
                if path=='posts': return {'id':83702}
                if path=='media': return {'id':83617}
                if '?context=edit' in path: return {'author':8,'status':'draft','slug':'ata-test'}
                return {'status':'future'}
            a.api=api
            with patch('agent_v2.ROOT',root),patch('agent_v2.STATE',state):
                a.process(a.store.db.execute('SELECT * FROM jobs').fetchone())
                self.assertEqual(a.store.db.execute('SELECT post,media,state FROM jobs').fetchone(),(83702,83617,'scheduled'))
                # Replay as after a recovered scheduler outcome: never create another post or image.
                a.process(a.store.db.execute('SELECT * FROM jobs').fetchone())
            self.assertEqual(sum(1 for x in calls if x[0]=='posts'),1)
            self.assertEqual(sum(1 for x in calls if x[0]=='media'),1)
            a.pool.image.assert_called_once()
            a.pool.text.assert_not_called()

    def test_continuous_content_and_image_editing_beyond_two_attempts_until_suitable(self):
        import io
        from PIL import Image
        from agent_v2 import now
        from datetime import timedelta
        with tempfile.TemporaryDirectory() as d, ExitStack() as resources:
            root=Path(d); state=root/'state'; state.mkdir()
            a=Agent.__new__(Agent); a.cfg={'auto_schedule':True}; a.base='https://arabiatrend.com'
            a.store=resources.enter_context(managed_store(state/'jobs.sqlite')); a.pool=Mock()
            slot=(now()+timedelta(hours=3)).isoformat(); source={'url':'https://blogs.windows.com/test/','date':now().isoformat()}
            a.store.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(slot,source['url'],'ata-test',83702,None,'held')); a.store.db.commit()
            short_article={'title':'خبر تقني عربي بعنوان واضح','excerpt':'ملخص','image_prompt':'a laptop',
                'sections':[{'heading':'عنوان','text':'كلمة قصيرة','source_ids':[1]} for _ in range(3)]}
            valid_article={'title':'خبر تقني عربي بعنوان واضح','excerpt':'ملخص','image_prompt':'a laptop',
                'sections':[{'heading':'عنوان','text':'كلمة '*115,'source_ids':[1]} for _ in range(3)]}
            record={'article':short_article,'evidence':{'url':source['url'],'date':source['date'],'blocks':[{'id':1,'text':'source'}]},
                    'revision':2,'reviewed':False}
            a.store.put_meta(slot,{'runtime_revision':3,'source':source,'record':record,'terminal':True})
            # 1st text call still too short (rev 3), 2nd text call produces valid article (rev 4), 3rd & 4th are reviewer passes
            a.pool.text.side_effect=[short_article, valid_article, {'pass':True,'issues':[]}, {'pass':True,'issues':[]}]
            out=io.BytesIO(); Image.new('RGB',(1536,768),'navy').save(out,format='PNG')
            a.pool.image.return_value=out.getvalue()
            # Reject image 3 times, pass on 4th
            a.pool.inspect_image.side_effect=[
                {'pass':False,'issues':['Text visible'],'alt_text':''},
                {'pass':False,'issues':['Blurry object'],'alt_text':''},
                {'pass':False,'issues':['Logo artifact'],'alt_text':''},
                {'pass':True,'issues':[],'alt_text':'حاسوب محمول حديث'}
            ]
            def api(path,body=None,raw=None,headers=None):
                if path.startswith('posts?') or path.startswith('media?'): return []
                if path=='media': return {'id':83617}
                if '?context=edit' in path: return {'author':8,'status':'draft','slug':'ata-test','featured_media':0}
                return {'status':'future'}
            a.api=api
            with patch('agent_v2.ROOT',root),patch('agent_v2.STATE',state):
                for _ in range(7):
                    meta=a.store.meta(slot)
                    meta['retry_at']=0; a.store.put_meta(slot,meta)
                    a.process(a.store.db.execute('SELECT * FROM jobs').fetchone())
                self.assertEqual(a.store.db.execute('SELECT post,media,state FROM jobs').fetchone(),(83702,83617,'scheduled'))
                final_meta=a.store.meta(slot)
                self.assertFalse(final_meta.get('terminal',False))
                self.assertEqual(final_meta['record']['revision'],4)
                self.assertEqual(final_meta['image_attempts'],4)

if __name__=='__main__': unittest.main()
