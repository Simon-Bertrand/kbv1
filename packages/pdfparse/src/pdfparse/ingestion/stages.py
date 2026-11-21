"""Validated JSON checkpoints, separate from legacy preparation/cache identities."""
from contextvars import ContextVar
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile

from PIL import Image
from pydantic import TypeAdapter

active_stages = ContextVar('pdfparse_stages', default=None)
_MISSING = object()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


class StageStore:
    def __init__(self, entry):
        self.entry = entry
        self.root = entry.parent / '.stages' / entry.pdf_sha256
        self.locks = {}
        self._packages = None

    def safe(self, path):
        return not any(part.is_symlink() for part in (path, *path.parents) if part != self.entry.parent.parent)

    def path(self,kind,key):
        # Pure crop inference may be reused across versions of this PDF path.
        # Source structure/extraction stays bound to the complete PDF digest.
        root=self.entry.parent/'.stages' if kind=='inference' else self.root
        return root/kind/(key+'.json')

    def get(self, kind, key):
        path=self.path(kind,key)
        if not self.safe(path):return _MISSING
        try:
            if path.stat().st_size > self.entry.pipeline.configuration.cache_max_bytes:return _MISSING
            import time
            age=self.entry.pipeline.configuration.cache_max_age_seconds
            if age is not None and time.time()-path.stat().st_mtime>age:return _MISSING
            data=json.loads(path.read_text())
            if data['format']!='pdfparse.stage.v1' or data['key']!=key or data['digest']!=digest(data['output']):return _MISSING
            os.utime(path,None)
            return data['output']
        except (OSError,ValueError,KeyError,TypeError):return _MISSING

    def put(self,kind,key,output):
        path=self.path(kind,key)
        if not self.safe(path):return
        path.parent.mkdir(parents=True,exist_ok=True)
        data=json.dumps({'format':'pdfparse.stage.v1','key':key,'digest':digest(output),'output':output},ensure_ascii=False,allow_nan=False)
        if len(data.encode()) > self.entry.pipeline.configuration.cache_max_bytes:return
        fd,temporary=tempfile.mkstemp(prefix='.write-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
            if os.name == 'posix':
                fd=os.open(path.parent,os.O_RDONLY | os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            # The common PDF lock protects this per-PDF stage retention pass.
            import time
            files=[p for p in (self.entry.parent/'.stages').rglob('*.json') if self.safe(p)]
            files.sort(key=lambda p:p.stat().st_mtime)
            total=sum(p.stat().st_size for p in files)
            config=self.entry.pipeline.configuration
            for candidate in files:
                expired=config.cache_max_age_seconds is not None and time.time()-candidate.stat().st_mtime > config.cache_max_age_seconds
                if expired or total > config.cache_max_bytes:
                    size=candidate.stat().st_size
                    candidate.unlink(missing_ok=True);total-=size
        finally:Path(temporary).unlink(missing_ok=True)

    def extraction(self,extractor):
        from .contracts import ExtractionResult, extract_source
        from .preparation import preparation_manifest
        provenance=json.loads(preparation_manifest(self.entry.pipeline,self.entry.pdf_sha256))
        key=digest({'version':1,'pdf':self.entry.pdf_sha256,'path':str(self.entry.pdfpath),
            'extractor':provenance['extractor'],'options':provenance['extraction_options'],
            'native_weights':provenance['native_weights'],'packages':{name:version for name,version in provenance['packages'].items() if name!='chonkie'},
            'extension_identity':self.entry.pipeline.configuration.cache_identity})
        reusable = not provenance.get('native_weights') or provenance['native_weights'].get('complete',False)
        output=self.get('extraction',key) if reusable else _MISSING
        adapter=TypeAdapter(ExtractionResult)
        if output is not _MISSING:
            try:
                restored=adapter.validate_python(output)
                if self.embedded_images(output):return restored
            except ValueError:pass
        result=extract_source(extractor,self.entry.pdfpath,self.entry.source_path)
        from .cache import _sha
        if _sha(self.entry.source_path) != self.entry.pdf_sha256:
            raise RuntimeError('PDF changed during extraction checkpoint')
        serialized=adapter.dump_python(result,mode='json')
        if reusable and self.embedded_images(serialized): self.put('extraction',key,serialized)
        return result

    @staticmethod
    def embedded_images(value):
        # Checkpoints must be self-contained; no external URL/file dereference on restore.
        if isinstance(value,dict):
            if 'uri' in value and not (isinstance(value['uri'],str) and value['uri'].startswith('data:image/')):return False
            return all(StageStore.embedded_images(v) for v in value.values())
        if isinstance(value,list):return all(StageStore.embedded_images(v) for v in value)
        return True

    def call_key(self,call):
        from .preparation import describe
        model=getattr(call.function,'__self__',None)
        # Only the bounded inference contracts; never memoize arbitrary extensions/I/O.
        from ..models.base import BaseInferenceModel
        if not isinstance(model,BaseInferenceModel) or getattr(call.function,'__name__','') not in ('predict','predict_batch','predict_regions','predict_table'):return None
        def encode(value):
            if isinstance(value,Image.Image):
                return {'image':hashlib.sha256(value.tobytes()).hexdigest(),'mode':value.mode,'size':value.size,
                        'palette':value.getpalette(),'info':encode(value.info),
                        'exif':hashlib.sha256(value.getexif().tobytes()).hexdigest()}
            if isinstance(value,bytes):return {'bytes':hashlib.sha256(value).hexdigest()}
            if isinstance(value,(list,tuple)):return [encode(v) for v in value]
            if isinstance(value,dict):return {k:encode(v) for k,v in value.items()}
            if value is None or isinstance(value,(str,int,float,bool)):return value
            raise TypeError('Unsupported stage input')
        if self._packages is None:
            from .preparation import preparation_manifest
            self._packages={name:version for name,version in json.loads(preparation_manifest(self.entry.pipeline,self.entry.pdf_sha256))['packages'].items() if name!='chonkie'}
        try:return digest({'version':1,'packages':self._packages,'producer':describe(model),'function':call.function.__name__,
            'args':encode(call.args),'kwargs':encode(call.kwargs),
            'extension_identity':self.entry.pipeline.configuration.cache_identity})
        except (TypeError,ValueError):return None

    def read_call(self,key):
        from ..models.picture_classifier.base import PictureClassification
        from ..models.ocr import OcrRegion
        from docling_core.types.doc import TableData
        value=self.get('inference',key)
        if value is _MISSING:return False,None
        try:
            if value['type']=='text' and isinstance(value['value'],str):return True,value['value']
            if value['type']=='texts':return True,TypeAdapter(list[str]).validate_python(value['value'],strict=True)
            if value['type']=='classifications':return True,TypeAdapter(list[PictureClassification]).validate_python(value['value'])
            if value['type']=='tables':return True,TypeAdapter(list[TableData]).validate_python(value['value'])
            if value['type']=='table':return True,TableData.model_validate(value['value'])
            if value['type']=='regions':return True,TypeAdapter(list[OcrRegion]).validate_python(value['value'])
            if value['type']=='classification':return True,TypeAdapter(PictureClassification).validate_python(value['value'])
        except (ValueError,KeyError,TypeError):pass
        return False,None

    def write_call(self,key,value):
        from ..models.picture_classifier.base import PictureClassification
        from ..models.ocr import OcrRegion
        from docling_core.types.doc import TableData
        if isinstance(value,TableData):self.put('inference',key,{'type':'table','value':value.model_dump(mode='json')})
        elif isinstance(value,list) and all(isinstance(v,OcrRegion) for v in value):self.put('inference',key,{'type':'regions','value':[asdict(v) for v in value]})
        elif isinstance(value,list) and all(isinstance(v,str) for v in value):self.put('inference',key,{'type':'texts','value':value})
        elif isinstance(value,list) and all(isinstance(v,PictureClassification) for v in value):self.put('inference',key,{'type':'classifications','value':[asdict(v) for v in value]})
        elif isinstance(value,list) and all(isinstance(v,TableData) for v in value):self.put('inference',key,{'type':'tables','value':[v.model_dump(mode='json') for v in value]})
        elif isinstance(value,str):self.put('inference',key,{'type':'text','value':value})
        elif isinstance(value,PictureClassification):self.put('inference',key,{'type':'classification','value':asdict(value)})
