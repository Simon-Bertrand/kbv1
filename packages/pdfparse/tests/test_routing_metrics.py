from PIL import Image
import pytest

from pdfparse.models.base import BaseInferenceModel
from pdfparse.progress import observe_inference
from tests.test_backend_runtime import runtime_studio, selection, wait_runtime
from tests.test_backend import wait_ready, wait_job
from tests.test_vlm import engine


@pytest.mark.parametrize('mode,ocr,vlm', [('keep',False,False),('ocr',True,False),('vlm',False,True),('ocr_vlm',True,True)])
def test_independent_studio_routes_and_per_call_metrics(runtime_studio,mode,ocr,vlm):
    client,_=runtime_studio
    config=selection()
    config['enrichment']={'figures':mode,'fallback':mode,'text_ocr':False,'page_vlm':False}
    config['ocr']=config['ocr'] if ocr else None
    config['vlm']=dict(config['vlm'],device='cpu') if vlm else None
    assert client.post('/api/runtime',json=config).status_code==202
    assert wait_runtime(client)['status']=='loaded'
    document=client.get('/api/documents').json()['documents'][0]
    response=client.post(f'/api/documents/{document["id"]}/process')
    assert response.status_code==202
    ready=wait_ready(client,document['id']);assert ready['status']=='ready',ready
    result=client.get(f'/api/documents/{document["id"]}/result').json()
    calls=result['metrics']['inferences']
    implementations=[call['implementation'] for call in calls]
    assert ('FixtureOcr' in implementations)==ocr
    assert ('FixtureVlm' in implementations)==vlm
    assert all(call['seconds']>=0 and call['image_sizes'] for call in calls)
    assert not any(item['type']=='page_description' for item in result['items'])
    job_id=next(job['id'] for job in client.get('/api/jobs').json() if job['document_id']==document['id'])
    job=wait_job(client,job_id)
    assert job['inference_count']==len(calls)
    assert job['stage_seconds'] and all(seconds>=0 for seconds in job['stage_seconds'].values())


def test_class_route_can_skip_expensive_services(runtime_studio):
    client,_=runtime_studio
    config=selection()
    config['vlm']['device']='cpu'
    config['enrichment']={'class_routes':{'line_chart':'keep'},'text_ocr':False,'page_vlm':False}
    client.post('/api/runtime',json=config);assert wait_runtime(client)['status']=='loaded'
    document=client.get('/api/documents').json()['documents'][0]
    client.post(f'/api/documents/{document["id"]}/process')
    ready=wait_ready(client,document['id']);assert ready['status']=='ready',ready
    calls=client.get(f'/api/documents/{document["id"]}/result').json()['metrics']['inferences']
    assert all(call['implementation'] not in ('FixtureVlm','FixtureOcr') for call in calls)


class NestedModel(BaseInferenceModel[str]):
    def predict(self,image):
        if image.width==1:
            raise ValueError('bad pixels')
        return 'ok'
    def predict_batch(self,images):
        return [self.predict(image) for image in images]


def test_nested_batch_trace_is_single_and_failures_remain_visible(tmp_path):
    model=NestedModel(model_path=tmp_path)
    calls=[]
    with observe_inference(calls.append):
        assert model.predict_batch([Image.new('RGB',(20,30)),Image.new('RGB',(40,50))])==['ok','ok']
        with pytest.raises(ValueError,match='bad pixels'):
            model.predict(Image.new('RGB',(1,1)))
    assert len(calls)==2
    assert calls[0]['image_sizes']==[[20,30],[40,50]]
    assert calls[1]['status']=='failed' and calls[1]['error']=='bad pixels'
    model.predict(Image.new('RGB',(20,20)))
    assert len(calls)==2


def test_generated_tokens_are_counted_from_actual_sequences(tmp_path,engine):
    from pdfparse.models.vlm.catalog import MODELS
    engine.model_type='granite'
    implementation=MODELS['granite_vision_4_1_4b'].implementation
    engine.model_type=implementation.model_type
    model=implementation(model_path=tmp_path,settings={'max_new_tokens':16})
    calls=[]
    with observe_inference(calls.append):
        assert len(model.predict_batch([Image.new('RGB',(20,30)),Image.new('RGB',(20,30))]))==2
    assert len(calls)==1
    assert calls[0]['output_tokens']==4 and calls[0]['input_tokens']==14


def test_docling_worker_services_inherit_observers_and_release_them(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pdfparse.progress import inference_workers
    model=NestedModel(model_path=tmp_path)
    calls=[]
    with observe_inference(calls.append),inference_workers([model,model,None]):
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(model.predict,Image.new('RGB',(10,20))).result()=='ok'
    assert len(calls)==1 and calls[0]['image_sizes']==[[10,20]]
    assert not hasattr(model,'_inference_observers')


def test_cache_configuration_never_walks_source_or_weights(monkeypatch):
    from pdfparse.ingestion import preparation
    from tests.test_result_cache import pipeline
    engine = pipeline()
    before = preparation.cache_configuration(engine)[0]
    def unexpected(*args, **kwargs):
        raise AssertionError('Cache keys must not scan source or weight files')
    monkeypatch.setattr(preparation.os, 'walk', unexpected)
    assert preparation.cache_configuration(engine)[0] == before


@pytest.mark.parametrize('arguments,ocr,page', [(['--ocr','rapidocr_v4','--text-ocr'],True,False),
    (['--vlm','qwen3_5_4b','--vlm-settings','{"quantization":"none"}','--page-vlm'],False,True)])
def test_cli_can_use_text_ocr_or_page_vlm_without_dfc(tmp_path,monkeypatch,arguments,ocr,page):
    import sys
    import uvicorn
    from pdfparse.backend import __main__ as cli
    captured={}
    monkeypatch.setattr(cli,'create_app',lambda **kwargs: captured.update(kwargs) or object())
    monkeypatch.setattr(uvicorn,'run',lambda *args,**kwargs:None)
    monkeypatch.setattr(sys,'argv',['pdfparse-studio','--root',str(tmp_path),'--device','cpu',*arguments])
    cli.main()
    config=captured['runtime_configuration']
    assert config.picture_classifier is None
    assert config.enrichment.text_ocr==ocr and config.enrichment.page_vlm==page


def test_page_checkbox_can_be_disabled_without_clearing_selected_vlm(runtime_studio,monkeypatch):
    from docling_core.types.doc import ImageRef, Size
    from tests.test_backend_runtime import FigureExtractor
    original = FigureExtractor.extract
    def extract(self,path):
        source = original(self,path)
        source.add_page(page_no=1,size=Size(width=100,height=200),
                        image=ImageRef.from_pil(Image.new('RGB',(100,200)),dpi=72))
        return source
    monkeypatch.setattr(FigureExtractor,'extract',extract)
    client,models=runtime_studio
    config=selection()
    config['picture_classifier']=None
    config['ocr']=None
    config['vlm']['device']='cpu'
    config['enrichment']={'page_vlm':True,'text_ocr':False}
    assert client.post('/api/runtime',json=config).status_code==202
    assert wait_runtime(client)['status']=='loaded'
    document=client.get('/api/documents').json()['documents'][0]
    client.post(f'/api/documents/{document["id"]}/process')
    ready=wait_ready(client,document['id']);assert ready['status']=='ready',ready
    result=client.get(f'/api/documents/{document["id"]}/result').json()
    assert any(item['type']=='page_description' for item in result['items'])
    config['enrichment']['page_vlm']=False
    assert client.post('/api/runtime',json=config).status_code==202
    current=wait_runtime(client);assert current['status']=='loaded',current
    assert current['configuration']['vlm']['name']=='fixture'
    assert not any(key[0]=='vlm' for key in models._instances)
    client.post(f'/api/documents/{document["id"]}/process')
    ready=wait_ready(client,document['id']);assert ready['status']=='ready',ready
    result=client.get(f'/api/documents/{document["id"]}/result').json()
    assert not any(item['type']=='page_description' for item in result['items'])
    assert not any(call['implementation']=='FixtureVlm' for call in result['metrics']['inferences'])
