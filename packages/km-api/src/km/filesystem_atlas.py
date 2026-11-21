"""Read-only atlas of filesystem identities/versions/files, independent of embeddings."""
import os
from pathlib import Path
from .inventory import VERSION, confined, identity, read_metadata

INFO = ('identifiant','designation','nature','typeDocumentId','codePerimetre','codeSousPerimetre','currentVersionNodeId','currentVersionLabel')
VERSION_FIELDS = ('nodeId','versionLabel','etatId','editionCodeId','editionDate','emploiPur','confidentialiteId')


def preview(record, keys):
    # Bounded graph labels; the detail endpoint retains the complete raw objects.
    result = {}
    for key in keys:
        value = (record or {}).get(key)
        if isinstance(value, str) and len(value) > 500:
            value = value[:500]+'… [voir la fiche brute]'
        elif isinstance(value, (list, dict)):
            value = '[valeur structurée : voir la fiche brute]'
        result[key] = value
    return result


class FilesystemAtlas:
    def __init__(self, root):
        self.root = Path(root)

    def paths(self):
        if not self.root.is_dir():
            raise RuntimeError('Corpus absent : vérifier DATA_DIR et le montage /corpus')
        root = self.root.resolve(strict=True)
        paths, directories, identities = [], 0, {}
        for parent in sorted(root.iterdir()):
            if parent.name.startswith('.') or parent.is_symlink() or not parent.is_dir():
                continue
            for version in sorted(parent.iterdir()):
                if version.is_symlink() or not version.is_dir() or not VERSION.fullmatch(version.name):
                    continue
                version_id = VERSION.fullmatch(version.name)['id']
                if version_id in identities and identities[version_id] != version:
                    raise ValueError('Identité de version attribuée à plusieurs dossiers')
                identities[version_id] = version
                files = version/'files'
                if files.is_symlink() or not files.is_dir():
                    continue
                def inaccessible(error):
                    raise error
                for directory, folders, names in os.walk(files, followlinks=False, onerror=inaccessible):
                    directories += 1
                    if directories > 200000:
                        raise RuntimeError('Corpus trop volumineux pour l’atlas')
                    folders[:] = sorted(n for n in folders if not n.startswith('.') and '.pdfparsecache' not in n.lower() and not (Path(directory)/n).is_symlink())
                    for name in sorted(names):
                        p = Path(directory)/name
                        if name.startswith('.') or '.pdfparsecache' in name.lower() or p.is_symlink() or not p.is_file():
                            continue
                        if p == files/'metadata.csv':
                            continue
                        paths.append(p.relative_to(root).as_posix())
                        if len(paths) > 100000:
                            raise RuntimeError('Plus de 100000 fichiers : réduire le périmètre du corpus')
        return sorted(paths)

    def page(self, limit=200, offset=0):
        paths = self.paths()
        selected = paths[offset:offset+limit]
        versions, files, references = {}, [], []
        omitted = 0
        for path in selected:
            parts = path.split('/')
            match = VERSION.fullmatch(parts[1])
            vid = match['id']
            if vid in versions and versions[vid]['version_folder'] != '/'.join(parts[:2]):
                raise ValueError('Identifiant de version attribué à plusieurs dossiers')
            if vid not in versions:
                error, meta = None, None
                try:
                    meta = read_metadata(self.root, path)
                except (ValueError, OSError, UnicodeError):
                    error = 'Métadonnées incohérentes ou illisibles : vérifier les JSON/CSV de cette version.'
                versions[vid] = {'parent_node_id':parts[0], 'version_node_id':vid,
                    'version_label':match['label'].replace('_','.'), 'version_folder':'/'.join(parts[:2]),
                    'info':preview(meta['info'] if meta else None, INFO),
                    'version':preview(meta['version'] if meta else None, VERSION_FIELDS),
                    'warnings':meta['warnings'] if meta else [], 'metadata_error':error,
                    'sample_path':path,'source_paths':meta.get('source_paths') if meta else None}
                declared = [('documentsLiees.json',(meta or {}).get('linked_documents') or [])]
                embedded = ((meta or {}).get('version') or {}).get('documentsLiees')
                if isinstance(embedded,list):
                    v_name = Path(meta['source_paths']['version']).name if meta and meta.get('source_paths') and meta['source_paths'].get('version') else 'versions.json'
                    declared.append((f'{v_name}.documentsLiees',embedded))
                for source, records in declared:
                    for ordinal, raw in enumerate(records):
                        if len(references) >= 1000:
                            omitted += 1
                            continue
                        raw = raw if isinstance(raw,dict) else {'value':raw}
                        target = raw.get('uuid')
                        if not isinstance(target,str) or len(target)>500:
                            target = None
                        references.append({'source_version_id':vid,'ordinal':ordinal,
                            'target_parent_id':target, 'raw':preview(raw, ('uuid','estLie')),
                            'source':source})
            files.append({'file_id':identity('file',path),'version_node_id':vid,
                          'parent_node_id':parts[0],'filename':parts[-1],'relative_path':path})
        return {'source':'filesystem','files':files,'versions':list(versions.values()),
            'references':references,'references_omitted':omitted,'offset':offset,'limit':limit,
            'total':len(paths),'next_offset':offset+len(selected) if offset+len(selected)<len(paths) else None,
            'semantics':'Hiérarchie filesystem et références déclarées ; aucun code métier, estLie ou version cible déduit.'}

    def detail(self, path):
        file = confined(self.root, path)
        if not file.is_file() or any(p.startswith('.') or '.pdfparsecache' in p.lower() for p in Path(path).parts):
            raise ValueError('Fichier de corpus invalide')
        meta = read_metadata(self.root,path)
        if meta is None:
            raise ValueError('Le fichier ne suit pas la structure parent/version/files')
        return dict(meta, source='filesystem', semantics='Valeurs sources brutes ; info.version ne remplace pas la version historique sélectionnée.')
