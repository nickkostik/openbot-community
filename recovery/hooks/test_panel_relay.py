import base64
import importlib.util
import io
import json
import pathlib
import threading
import types
import unittest
import urllib.error
from unittest import mock

spec = importlib.util.spec_from_file_location('panel_relay', pathlib.Path(__file__).with_name('panel-relay.py'))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class PanelRelayTests(unittest.TestCase):
    def driver(self):
        sources = []
        def run(_command, args, **_options):
            sources.append(args[1])
            return {'exitCode':0,'stdout':json.dumps({'status':400,'headers':{
                'Content-Type':'application/json','Content-Length':'2','Connection':'secret',
                'secret':'discard','Set-Cookie':'same=1; Secure'},'body':base64.b64encode(b'{}').decode()})}
        return types.SimpleNamespace(renew=mock.Mock(), run=run), sources

    def test_path_and_method_cannot_change_the_loopback_execution_target(self):
        driver, sources = self.driver(); transport = m.PanelTransport(driver)
        for path in ['https://external.invalid','//external.invalid','/bad\nheader','/bad\0suffix']:
            with self.assertRaises(ValueError):transport.request('GET',path,{},b'')
        with self.assertRaises(ValueError):transport.request('CONNECT','/',{},b'')
        self.assertEqual(sources,[]);driver.renew.assert_not_called()

    def test_headers_status_and_binary_body_are_preserved_without_hop_headers(self):
        driver, sources = self.driver(); transport = m.PanelTransport(driver)
        status, headers, body = transport.request('POST','/api/save',{
            'connection':'x-secret','x-secret':'discard','Authorization':'Bearer caller',
            'User-Agent':'Browser','Content-Length':'2'},b'{}')
        self.assertEqual(status,400);self.assertEqual(body,b'{}')
        self.assertNotIn('secret',headers);self.assertNotIn('Content-Length',headers)
        self.assertEqual(headers['Set-Cookie'],'same=1; Secure')
        self.assertNotIn("'x-secret'",sources[0]);self.assertIn('Bearer caller',sources[0])
        self.assertIn('http://127.0.0.1:9280',sources[0])
        self.assertIn('NoRedirect',sources[0])

    def test_literal_template_names_and_quotes_are_not_replaced_inside_input(self):
        driver, sources = self.driver(); transport = m.PanelTransport(driver)
        path="/api/state?x=METHOD'HEADERS'BODY'LIMIT"
        transport.request('GET',path,{'User-Agent':"PATH'BODY'HEADERS"},b'METHOD')
        compile(sources[0],'<native-panel-request>','exec')
        self.assertIn(repr(path),sources[0]);self.assertIn(repr("PATH'BODY'HEADERS"),sources[0])

    def test_large_import_is_chunked_under_the_argument_bound_and_cleaned(self):
        driver, sources = self.driver(); transport = m.PanelTransport(driver)
        transport.request('POST','/api/save',{},b'x'*100000)
        self.assertGreater(len(sources),3)
        self.assertTrue(all(len(s.encode())<65536 for s in sources))
        self.assertIn('os.O_NOFOLLOW',sources[0]);self.assertIn('os.O_EXCL',sources[0])
        self.assertIn('.read_bytes()',sources[-2]);self.assertIn('unlink',sources[-1])
        for source in sources:compile(source,'<native-panel-request>','exec')

    def test_concurrent_clients_renew_the_private_transport_once(self):
        driver,_=self.driver();transport=m.PanelTransport(driver)
        threads=[threading.Thread(target=transport.ready) for _ in range(12)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        driver.renew.assert_called_once()

    def test_head_keeps_original_content_length(self):
        driver,_=self.driver();transport=m.PanelTransport(driver)
        _,headers,_=transport.request('HEAD','/api/state',{},b'')
        self.assertEqual(headers['Content-Length'],'2')

    def test_oauth_cookies_are_not_collapsed_into_one_header(self):
        driver,_=self.driver();result=driver.run('', ['',''],timeout=45)
        data=json.loads(result['stdout']);data['cookies']=['session=a; Secure','state=b; Secure']
        result['stdout']=json.dumps(data);driver.run=mock.Mock(return_value=result)
        _,headers,_=m.PanelTransport(driver).request('GET','/api/oauth',{},b'')
        self.assertEqual(headers['Set-Cookie'],data['cookies'])

    def test_replaced_execution_identity_is_refreshed_before_dispatch(self):
        driver,_=self.driver();transport=m.PanelTransport(driver)
        valid=driver.run('/usr/bin/python3',['-c',''],timeout=45)
        driver.run=mock.Mock(side_effect=[urllib.error.HTTPError(
            'https://executor.invalid',401,'refused',{},None),valid])
        transport.request('POST','/api/save',{},b'{}')
        self.assertEqual(driver.run.call_count,2);self.assertEqual(driver.renew.call_count,2)

    def test_uncertain_execution_failures_do_not_repeat_mutations(self):
        driver,_=self.driver();transport=m.PanelTransport(driver)
        driver.run=mock.Mock(side_effect=urllib.error.HTTPError(
            'https://executor.invalid',502,'reset',{},None))
        with self.assertRaises(urllib.error.HTTPError):transport.request('POST','/api/save',{},b'{}')
        driver.run.assert_called_once();driver.renew.assert_called_once()

    def test_chunked_browser_upload_and_oversized_content_length(self):
        handler=object.__new__(m.PanelHandler)
        handler.headers={'Transfer-Encoding':'chunked'}
        handler.rfile=io.BytesIO(b'4\r\ntest\r\n2;option=yes\r\n12\r\n0\r\n\r\n')
        self.assertEqual(handler.body(),b'test12')
        handler.headers={'Content-Length':str(m.MAX_BODY+1)}
        with self.assertRaises(ValueError):handler.body()
        handler.headers={'Transfer-Encoding':'chunked'}
        handler.rfile=io.BytesIO(b'2\r\na\r\n0\r\n\r\n')
        with self.assertRaises(ValueError):handler.body()


if __name__ == '__main__':
    unittest.main()
