"""KM filesystem identities and explicit version sidecars. Never follows links."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import uuid

NAMESPACE=uuid.UUID('03173236-195b-4f2d-b1ce-7381a938b1c7')
VERSION=re.compile(r'^V(?P<label>[0-9]+(?:_[0-9]+)*)@(?P<id>[^/@\\]+)$',re.IGNORECASE)
MAX_SIDECAR=8*1024*1024

def identity(kind,value):
    return str(uuid.uuid5(NAMESPACE,kind+':'+value))

def relative_path(value):
    if not isinstance(value,str) or not value or '\\' in value or any(ord(c)<32 for c in value):
        raise ValueError('Chemin KM invalide')
    path=PurePosixPath(value)
    if path.is_absolute() or any(p in {'.','..'} for p in path.parts):
        raise ValueError('Chemin KM hors corpus')
    return path

def source_relative(value, upstream_root):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid source locator')
    path = PurePosixPath(value)
    if '..' in path.parts:
        raise ValueError('Source escapes corpus')
    if path.is_absolute():
        path = path.relative_to(PurePosixPath(upstream_root))
    if not path.parts:
        raise ValueError('Empty source locator')
    return path


def confined(root,value,*,exists=True):
    root=Path(root).resolve(strict=True)
    path=root
    for part in relative_path(value).parts:
        path=path/part
        if path.is_symlink():
            raise ValueError('Lien symbolique KM refusé')
    path.resolve(strict=exists).relative_to(root)
    return path

def adjacent_source_version(root, relative, digest):
    source = relative_path(relative)
    archived = source.with_name(source.name + '.pdfparsecache') / 'versions' / (digest + '.pdf')
    return confined(root, archived.as_posix())


def external_cache_parent(upstream_path):
    configured = os.getenv('PDFPARSE_CACHE_DIR')
    if not configured:
        return None
    key = hashlib.sha256(PurePosixPath(upstream_path).as_posix().encode()).hexdigest()
    return Path(configured).expanduser().resolve() / (key + '.pdfparsecache')


def cache_entries(pdf, upstream_path):
    external = external_cache_parent(upstream_path)
    adjacent = pdf.with_name(pdf.name + '.pdfparsecache')
    seen = set()
    for parent in (external, adjacent):
        if parent is None:
            continue
        if parent.is_symlink():
            raise ValueError('Lien symbolique de cache refusé')
        if not parent.is_dir():
            continue
        for entry in sorted(parent.iterdir()):
            if (entry.name not in seen and re.fullmatch('[a-f0-9]{64}', entry.name)
                    and not entry.is_symlink() and entry.is_dir()):
                seen.add(entry.name)
                yield entry, parent == external


def read_bytes(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_SIDECAR:
        raise ValueError('Fichier de métadonnées absent, lié ou trop volumineux')
    return path.read_bytes()

def read_json(path,expected):
    if not path.exists():
        return None
    value=json.loads(read_bytes(path).decode('utf-8-sig'))
    if not isinstance(value,expected):
        raise ValueError('Structure JSON de métadonnées invalide')
    return value

def locate_metadata_file(directory, primary_name, secondary_name, expected):
    p1 = directory / primary_name
    p2 = directory / secondary_name
    v1 = read_json(p1, expected) if p1.exists() else None
    v2 = read_json(p2, expected) if p2.exists() else None
    if v1 is not None and v2 is not None and json.dumps(v1, sort_keys=True) != json.dumps(v2, sort_keys=True):
        raise ValueError(f"Les fichiers {primary_name} et {secondary_name} divergent dans {directory} : résoudre explicitement leur provenance")
    if v1 is not None:
        return v1, p1
    if v2 is not None:
        return v2, p2
    return None, None

def read_metadata_csv(csv_path, filename):
    rows=[]
    if csv_path.exists():
        text=read_bytes(csv_path).decode('utf-8-sig')
        try:
            dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
        except csv.Error:
            dialect=csv.excel
        reader=csv.DictReader(io.StringIO(text),dialect=dialect)
        if not reader.fieldnames or 'filename' not in reader.fieldnames:
            raise ValueError('metadata.csv doit avoir une colonne filename')
        for row in reader:
            if row.get('filename')==filename:
                rows.append(row)
        if len(rows)>1:
            raise ValueError('Plusieurs lignes metadata.csv désignent le même fichier')
    return rows[0] if rows else {}

def read_metadata(root,relative):
    """Only the version containing this exact files/ path may supply metadata."""
    parts=relative_path(relative).parts
    if len(parts)<4 or parts[2]!='files':
        return None
    match=VERSION.fullmatch(parts[1])
    if not match:
        return None
    parent,version_id=parts[0],match['id']
    directory=confined(root,'/'.join(parts[:2]))
    version_info, v_info_path = locate_metadata_file(directory, 'infos.json', 'info.json', dict)
    parent_info, p_info_path = locate_metadata_file(directory.parent, 'infos.json', 'info.json', dict)
    if version_info is not None and parent_info is not None and json.dumps(version_info,sort_keys=True) != json.dumps(parent_info,sort_keys=True):
        raise ValueError('Les infos.json parent et version divergent : résoudre explicitement leur provenance')
    info=version_info if version_info is not None else parent_info
    info_sources=[p.relative_to(Path(root).resolve()).as_posix() for p in (p_info_path, v_info_path) if p is not None]
    version, version_path = locate_metadata_file(directory, 'versions.json', 'version.json', dict)
    linked=read_json(directory/'documentsLiees.json',list)
    if info and info.get('identiteNodeId') and info['identiteNodeId']!=parent:
        raise ValueError('infos.json identiteNodeId ne correspond pas au dossier parent')
    if version and version.get('nodeId') and version['nodeId']!=version_id:
        raise ValueError('versions.json nodeId ne correspond pas au dossier de version')
    # info.version can describe currentVersion rather than this historical version.
    file_relative='/'.join(parts[3:])
    csv_path=directory/'files/metadata.csv'
    row = read_metadata_csv(csv_path, file_relative)
    original=row.get('original_path',row.get('orignal_path'))
    warnings=[]
    normalized=match['label'].replace('_','.')
    if version and version.get('versionLabel') and version['versionLabel']!=normalized:
        warnings.append('versionLabel diffère du libellé du dossier ; les deux sont conservés')
    if info and info.get('currentVersionNodeId') and info['currentVersionNodeId']!=version_id:
        warnings.append('La version courante déclarée dans infos.json est une autre version')
    if linked is not None and any(not isinstance(item,dict) for item in linked):
        raise ValueError('documentsLiees.json doit contenir des objets')
    return {'file_id':identity('file',relative),'relative_path':relative,'filename':parts[-1],
        'parent_node_id':parent,'version_node_id':version_id,'version_label':normalized,
        'version_folder':'/'.join(parts[:2]),'info':info,'version':version,
        'linked_documents':linked,'original_path':original,'csv_row':row,'warnings':warnings,
        'source_paths':{'info':info_sources,'version':version_path.relative_to(Path(root).resolve()).as_posix() if version_path is not None else None,
                        'linked_documents':'/'.join(parts[:2])+'/documentsLiees.json' if linked is not None else None,
                        'csv':'/'.join(parts[:2])+'/files/metadata.csv' if csv_path.exists() else None}}

class Inventory:
    def __init__(self,root,upstream='/data'):
        self.root=Path(root)
        self.upstream=PurePosixPath(upstream)
        self.diagnostics={}

    def documents(self,folder=''):
        return sorted(self.iter_documents(folder),key=lambda item:item['path'].casefold())

    def iter_documents(self,folder='',*,include_metadata=False):
        if not self.root.is_dir():
            raise RuntimeError('Corpus KM absent : vérifier DATA_DIR et le montage /corpus')
        root=self.root.resolve(strict=True)
        start=confined(root,folder) if folder else root
        if start.is_relative_to(root / '_versions'):
            raise ValueError('Le dossier des versions PDF est réservé')
        if not start.is_dir():
            raise ValueError('Un dossier de corpus est requis')
        count=0
        visited=0
        self.diagnostics={'root':'/corpus','folder':folder,'directories':0,'pdf_count':0,'provider':'local-filesystem'}
        def walk_error(exc):
            raise exc
        for directory,folders,files in os.walk(start,followlinks=False,onerror=walk_error):
            folders[:]=sorted(name for name in folders if not name.startswith('.')
                and '.pdfparsecache' not in name.lower() and not (Path(directory)/name).is_symlink()
                and (Path(directory) != root or name != '_versions'))
            visited+=1
            self.diagnostics['directories']=visited
            if visited>200000:
                raise RuntimeError('Exploration trop volumineuse : choisir un sous-dossier')
            for name in sorted(files):
                path=Path(directory)/name
                if name.lower().endswith('.pdf') and not path.is_symlink():
                    relative=path.relative_to(root).as_posix()
                    metadata_error=None
                    try:
                        meta=read_metadata(root,relative)
                    except (ValueError,OSError,UnicodeError):
                        meta=None
                        metadata_error='Métadonnées invalides : consulter les fichiers JSON/CSV de cette version.'
                    cache_count=sum(1 for _ in cache_entries(path, self.upstream / relative))
                    row={'id':hashlib.sha256(relative.encode()).hexdigest()[:24],
                        'path':relative,'name':name,'size':path.stat().st_size,
                        'status':'cached' if cache_count else 'idle','cache_count':cache_count,
                        'parent_node_id':meta['parent_node_id'] if meta else None,
                        'version_node_id':meta['version_node_id'] if meta else None,
                        'version_label':meta['version_label'] if meta else None,'metadata_error':metadata_error}
                    if include_metadata:row['_metadata']=meta
                    count+=1
                    self.diagnostics['pdf_count']=count
                    yield row
                    if count>100000:
                        raise RuntimeError('Plus de 100000 PDF : choisir un sous-dossier')
        self.diagnostics={'root':'/corpus','folder':folder,'directories':visited,
            'pdf_count':count,'provider':'local-filesystem',
            'hint':None if count else 'Aucun PDF sous /corpus : DATA_DIR doit pointer vers packages/data, pas vers le dossier des services.'}

def metadata_digest(metadata):
    if metadata is None:
        return None
    payload = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()



def standalone_document_metadata(root, relative):
    path = confined(root, relative)
    csv_path = path.parent / 'metadata.csv'
    has_csv = csv_path.is_file()
    row = read_metadata_csv(csv_path, path.name) if has_csv else {}
    info, _ = locate_metadata_file(path.parent, 'infos.json', 'info.json', dict)
    return {'original_path': row.get('original_path', row.get('orignal_path')),
            'info': info, 'source_paths': {'csv': has_csv}}


def indexed_document_identity(bundle):
    """Use the inventoried name when a CSV exists, otherwise the PDF filename."""
    km = bundle.get('km_metadata') or bundle.get('standalone_metadata') or {}
    sources = km.get('source_paths') or {}
    original = km.get('original_path')
    if isinstance(original, str) and original.strip():
        name = Path(original.strip().replace(chr(92), '/')).name or None
    elif sources.get('csv'):
        name = None
    else:
        name = Path(bundle['filepath']).name
    document_type = (km.get('info') or {}).get('typeDocumentId')
    if type(document_type) is not int or not 0 <= document_type < 2**63:
        document_type = None
    return name, document_type

def bind_bundle(bundle,metadata):
    """Content SHA is provenance; each KM file/version keeps a distinct identity."""
    if not metadata:
        return bundle
    doc=identity('published-file',metadata['relative_path'])
    chunks={r['id']:identity('chunk',doc+':'+r['id']) for r in bundle['chunks']}
    visuals={r['id']:identity('visual',doc+':'+r['id']) for r in bundle['visuals']}
    def remap(record,mapping):
        result=dict(record,upstream_id=record['id'],upstream_document_id=record['document_id'],
                    id=mapping[record['id']],document_id=doc)
        for key in ('previous_chunk_id','next_chunk_id'):
            if record.get(key):
                result[key]=chunks.get(record[key])
        if 'chunk_ids' in record:
            result['chunk_ids']=[chunks[value] for value in record['chunk_ids']]
        return result
    return dict(bundle,document_id=doc,source_sha256=bundle.get('source_sha256',bundle['document_id']),
        chunks=[remap(r,chunks) for r in bundle['chunks']],visuals=[remap(r,visuals) for r in bundle['visuals']],
        km_metadata=metadata,metadata={**bundle.get('metadata',{}),
            'km_metadata_digest':metadata_digest(metadata),
            'km':{**{k:metadata[k] for k in ('parent_node_id','version_node_id','version_label','filename','original_path')},
                  'source_paths':metadata.get('source_paths')},
            'content_sha256':bundle.get('source_sha256',bundle['document_id'])})
