"""Real NGINX -> shared frontend checks; dedicated disposable fixture server only."""
import io
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pytest

pytestmark=pytest.mark.skipif(not os.environ.get('FRONTEND_FILE_TEST_URL'), reason='Disposable fileserver/frontend pair required')

def get(action,path,**kwargs):
    base=os.environ['FRONTEND_FILE_TEST_URL']
    request=Request(base+'/api/files/'+action+'?'+urlencode({'path':path}),**kwargs)
    try:
        response=urlopen(request,timeout=30)
    except HTTPError as error:
        response=error
    with response:
        return response.status,response.headers,response.read()

def test_real_listing_unicode_download_range_head_and_zip():
    status,headers,body=get('list','docs')
    assert status==200
    names={e['name'] for e in json.loads(body)['entries']}
    assert 'test.pdf' in names and '.env' not in names and 'link' not in names
    status,headers,body=get('raw','docs/test.pdf',headers={'Range':'bytes=0-4'})
    assert status==206 and body==b'hello'
    assert headers['Content-Range']=='bytes 0-4/15'
    assert headers['Content-Disposition'].startswith('attachment;')
    status,headers,body=get('raw','docs/test.pdf',method='HEAD')
    assert status==200 and body==b'' and headers['Content-Length']=='15'
    status,headers,body=get('raw','docs/sub é #?%/utf é #?%+.txt')
    assert status==200 and body=='été\n'.encode()
    assert headers.get_content_type()=='application/octet-stream'
    status,headers,body=get('zip','docs')
    assert status==200
    with ZipFile(io.BytesIO(body)) as archive:
        assert archive.read('docs/test.pdf')==b'hello staticzip'
        assert all('.pdfparsecache' not in name and '.git' not in name for name in archive.namelist())

def test_large_download_is_streamed_and_traversal_is_refused():
    status,headers,body=get('raw','large.bin')
    assert status==200 and len(body)==17*1024*1024
    assert body[:16]==bytes(range(16))
    assert get('raw','../secret')[0]==422
    assert get('raw','docs/.env')[0]==422
