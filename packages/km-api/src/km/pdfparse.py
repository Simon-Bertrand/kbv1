"""Read-only filesystem caches. Never connects to or starts PdfParse."""
import hashlib
from pathlib import Path
import re

from .cache import CacheReader
from .inventory import source_relative, Inventory, confined, read_metadata, bind_bundle

class PdfParse:
    def __init__(self,url,root,*,upstream_root='/data',transport=None,deadline=7200,poll=2):
        # Legacy arguments remain accepted, but this adapter has no HTTP client.
        self.root,self.upstream_root=Path(root),upstream_root
        self.inventory=Inventory(root,upstream_root)
        self.cache=CacheReader(root,upstream_root)
        self.entries={}

    @property
    def diagnostics(self):
        return self.inventory.diagnostics

    def close(self):
        pass

    def documents(self,folder=''):
        found=self.inventory.documents(folder)
        self.entries.update({item['id']:item for item in found})
        return found

    def prepare(self,ident,progress=lambda stage:None,*,use_cache=True,cache_only=True,cache_key=None):
        if use_cache is not True or cache_only is not True:
            raise ValueError('Indexation autorisée uniquement depuis des caches existants ; PdfParse ne sera pas lancé.')
        relative=self.relative(ident)
        metadata=read_metadata(self.root,relative)
        progress('cache')
        bundle=self.existing_bundle(relative,cache_key=cache_key) if cache_key else self.existing_bundle(relative)
        if bundle is None:
            raise RuntimeError('Cache compatible absent : aucun traitement PdfParse ne sera lancé.')
        return bind_bundle(bundle,metadata)

    def relative(self,ident):
        if not re.fullmatch('[a-f0-9]{24}',ident):raise ValueError('Identifiant de fichier invalide')
        if ident not in self.entries:self.documents()
        if ident not in self.entries:raise ValueError('PDF absent du corpus KM')
        return self.entries[ident]['path']

    def cache_choices(self,ident):
        relative=self.relative(ident)
        return dict(self.cache.inspect(relative),document_id=ident,path=relative)

    def existing_bundle(self,relative,*,cache_key=None):
        return self.cache.load(relative,cache_key=cache_key)

    def local(self,raw):
        return confined(self.root,source_relative(raw,self.upstream_root).as_posix())

    def verify_source(self,bundle):
        with self.local(bundle['filepath']).open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=bundle.get('source_sha256',bundle['document_id']):
                raise ValueError('PDF modifié pendant l’encodage ; publication refusée')
        if bundle.get('km_metadata'):
            relative=source_relative(bundle['filepath'],self.upstream_root).as_posix()
            if read_metadata(self.root,relative)!=bundle['km_metadata']:
                raise ValueError('Métadonnées modifiées pendant l’encodage ; publication refusée')
