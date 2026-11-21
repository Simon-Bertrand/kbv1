#!/usr/bin/env python3
"""Build the Alfresco-shaped local fixture from the original raw_data PDFs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import UUID, uuid5


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "raw_data"
NAMESPACE = UUID("56f482da-fea1-571f-b320-cab9ae3eedb4")
EPOCH_MS = 1735689600000  # 2025-01-01 UTC, date d'édition synthétique.
TYPE_FIELDS = (
    "idTypeDocRef", "reference", "libelle", "codePerimetre", "libellePerimetre",
    "codeSousPerimetre", "libelleSousPerimetre",
)
TYPE_ROWS = {
    "doclaynet/financial_reports": (9001, "FIN", "Rapport financier", "GEST", "Gestion", "FINANCES", "Finances"),
    "doclaynet/manuals": (9002, "MAN", "Manuel technique", "TECH", "Technique", "MANUELS", "Manuels"),
    "doclaynet/scientific_articles": (9003, "ART", "Article scientifique", "SCI", "Sciences", "ARTICLES", "Articles"),
    "enginuity": (9004, "ING", "Document d’ingénierie", "TECH", "Technique", "MAINTENANCE", "Maintenance"),
    "nasa_ntrs": (9005, "RAP", "Rapport technique", "SCI", "Sciences", "RAPPORTS", "Rapports"),
    "olmocr/old_scans": (9006, "NUM", "Document numérisé", "ARCH", "Archives", "SCANS", "Scans"),
    "stanfordhardware": (9007, "FIC", "Fiche technique de composant", "TECH", "Technique", "ELECTRONIQUE", "Électronique"),
    "uspto": (9008, "BRE", "Brevet", "PI", "Propriété intellectuelle", "BREVETS", "Brevets"),
}
TYPES = {key: dict(zip(TYPE_FIELDS, row, strict=True)) for key, row in TYPE_ROWS.items()}
DATASHEETS = "datasets/stanfordhardware/stanford/datasheets/"
VEHICLES = "lot-manuels-vehicules"
AMPLIFIERS = "lot-amplificateurs-onsemi"
PRECISION = "versions-famille-21911"


@dataclass(frozen=True)
class SourcePdf:
    path: str
    type_key: str
    sha256: str
    size: int
    pages: int
    title: str
    title_source: str


@dataclass(frozen=True)
class Document:
    key: str
    title: str
    versions: tuple[tuple[str, ...], ...]
    scenario: str = "direct"
    linked_key: str | None = None


SCENARIOS = (
    Document(VEHICLES, "Lot de manuels de maintenance de véhicules militaires",
             (tuple(f"datasets/enginuity/data-{n}.pdf" for n in (1, 10, 6, 9)),), "lot"),
    Document(AMPLIFIERS, "Lot de fiches onsemi — Amplificateurs opérationnels",
             (tuple(DATASHEETS + name for name in (
                 "ONSemiconductor_MC33078DR2G-66c4432123f1.pdf",
                 "ONSemiconductor_MC33202DR2G-fc3543489e65.pdf",
                 "ONSemiconductor_NCS20071XV53T2G-4b70cb0e035c.pdf",
                 "ONSemiconductor_NCS20072DMR2G-932e90095b06.pdf",
             )),), "lot", PRECISION),
    Document(PRECISION, "Fiche de famille onsemi NCS21911 / NCV21911",
             ((DATASHEETS + "ONSemiconductor_NCS21911SN2T1G-99220c6747a8.pdf",),
              (DATASHEETS + "ONSemiconductor_NCV21911SN2T1G-86b4aaab2680.pdf",)), "multiversion"),
)
VERSION_NULLS = (
    "butee", "numeroCommande", "commentaire", "versionLabelByEditionCodeId",
    "entreeVigueurDate", "numeroSerie", "conditionCloture", "codageSil",
    "identificationRef", "identificationDate", "critiqueCDTAARef", "critiqueCDTAADate",
    "homologationRef", "homologationDate", "rejetRef", "rejetDate",
    "approbationRef", "approbationDate", "abrogationRef", "abrogationDate",
    "packageNom", "packageRang", "pubConfiguration", "urlF13",
    "afficheExportContenuLot", "justification",
)
IDENTITY_NULLS = (
    "commentaireIdentite", "alternateSFId", "idPlanDiff", "programmeDocumentaireNodeId",
    "liableInterUDE", "profilId", "mi", "orgDiffusion",
)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def pdf_tool(command: list[str]) -> str:
    return subprocess.run(
        command, check=True, capture_output=True, encoding="utf-8", errors="replace",
        timeout=30,
    ).stdout


def pdf_title(path: Path, title: str, type_key: str) -> tuple[str, str]:
    title = " ".join(title.split())
    if (10 <= len(title) <= 180 and re.search(r"[^\W\d_]{3,}", title)
            and not re.search(r"^(untitled|sans titre|genesys|approve sheet|microsoft word)\b", title, re.I)):
        return title, "pdf_metadata"
    if type_key == "stanfordhardware":
        return re.sub(r"-[0-9a-f]{12}$", "", path.stem).replace("_", " "), "filename"
    if type_key in {"enginuity", "doclaynet/manuals"}:
        text = pdf_tool(["pdftotext", "-enc", "UTF-8", "-f", "1", "-l", "1", str(path), "-"])
        lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
        for line in lines[:3]:
            if type_key == "enginuity" and re.fullmatch(r"(?:ARMY )?TM\s*\d[\dA-Z-]+", line, re.I):
                return f"Manuel de maintenance — {line}", "first_page"
            if type_key == "doclaynet/manuals" and len(line) <= 100 and re.search(
                r"(?:Documentation|Manual|Release Notes|User Guide)(?:, Release [\w.-]+)?$", line, re.I
            ):
                return line, "first_page"
    if type_key.startswith("doclaynet/"):
        return f"{TYPES[type_key]['libelle']} DocLayNet — {path.stem[:12]}", "filename"
    if type_key == "nasa_ntrs":
        return f"Rapport technique NASA NTRS — {path.stem}", "filename"
    return f"{TYPES[type_key]['libelle']} — {path.stem}", "filename"


def inspect_pdf(path: Path) -> SourcePdf:
    relative = path.relative_to(SOURCE)
    type_key = next((key for key in TYPES if relative.is_relative_to(Path("datasets") / key)), None)
    if type_key is None:
        raise ValueError(f"Famille documentaire non définie pour {relative}")
    info = dict(line.split(":", 1) for line in pdf_tool(
        ["pdfinfo", "-enc", "UTF-8", str(path)]
    ).splitlines() if ":" in line)
    title, title_source = pdf_title(path, info.get("Title", ""), type_key)
    return SourcePdf(relative.as_posix(), type_key, digest(path), path.stat().st_size,
                     int(info["Pages"]), title, title_source)


def stable_uuid(key: str) -> str:
    return str(uuid5(NAMESPACE, key))


def identity_id(document: Document) -> str:
    return stable_uuid(f"identity/{document.key}")


def version_id(document: Document, label: str) -> str:
    return stable_uuid(f"version/{document.key}/{label}")


def document_type(document: Document, sources: dict[str, SourcePdf]) -> dict:
    return TYPES[sources[document.versions[0][0]].type_key]


def document_reference(document: Document, sources: dict[str, SourcePdf]) -> str:
    return f"FIX-{document_type(document, sources)['reference']}-{identity_id(document)}"


def label_for(edition: int) -> str:
    return f"V{edition}_0"


def version_metadata(document: Document, sources: dict[str, SourcePdf],
                     edition: int, linked: list[dict]) -> tuple[dict, dict]:
    label = label_for(edition)
    node = identity_id(document)
    publication = version_id(document, label)
    current_label = label_for(len(document.versions))
    kind = document_type(document, sources)
    date = EPOCH_MS + (edition - 1) * 30 * 86_400_000
    record = dict.fromkeys(VERSION_NULLS)
    record.update(
        nodeId=publication, versionLabel=label, editionCodeId=edition, editionDate=date,
        etatId=1, rangMaj=str(edition), niveauMaj="0", systemesReference=[],
        stock={"quantite": 0, "positionnement": "Fixture", "observations": None, "docArmeeUuid": node},
        hasLivraisonsEnCours=False, emploiPur=False, imperatifNavId=0,
        impactPea=False, impactDtu=False, impactIme=False, impactIdops=False,
        confidentialiteId=1, specialFranceId=0, editionGroup="FIXTURE", documentsLiees=linked,
    )
    info = dict.fromkeys(IDENTITY_NULLS)
    info.update(
        identiteNodeId=node, currentVersionNodeId=version_id(document, current_label),
        currentVersionLabel=current_label, nature="Document", identifiant=document_reference(document, sources),
        designation=document.title, typeDocumentId=kind["idTypeDocRef"],
        version=record | {
            "stock": None, "hasLivraisonsEnCours": None, "justification": [],
            "identificationRef": f"FIX-ID-{publication}", "identificationDate": date,
            "approbationRef": f"FIX-APP-{publication}", "approbationDate": date,
        },
        hasCdeEnCours=False, codePerimetre=kind["codePerimetre"],
        codeSousPerimetre=kind["codeSousPerimetre"], orgApprobateur=1,
    )
    return info, record


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(staging: Path, sources: dict[str, SourcePdf]) -> dict:
    grouped = [path for doc in SCENARIOS for version in doc.versions for path in version]
    if len(grouped) != len(set(grouped)) or set(grouped).difference(sources):
        raise ValueError("Les scénarios doivent référencer des PDF sources distincts et présents.")
    for doc in SCENARIOS:
        keys = {sources[path].type_key for version in doc.versions for path in version}
        if len(keys) != 1:
            raise ValueError(f"Types documentaires incohérents dans {doc.key}")
    documents = {doc.key: doc for doc in SCENARIOS}
    documents.update({
        path: Document(path, sources[path].title, ((path,),))
        for path in sorted(set(sources).difference(grouped))
    })
    files = []
    for document in documents.values():
        linked = []
        if document.linked_key:
            target = documents[document.linked_key]
            linked.append({
                "uuid": identity_id(target), "identifiant": document_reference(target, sources),
                "designation": target.title, "version": label_for(len(target.versions)), "revision": "0",
                "typeDocumentId": document_type(target, sources)["idTypeDocRef"], "estLiee": None,
            })
        for edition, paths in enumerate(document.versions, start=1):
            info, record = version_metadata(document, sources, edition, linked)
            folder = staging / info["identiteNodeId"] / f"{record['versionLabel']}@{record['nodeId']}"
            attachments = folder / "files"
            attachments.mkdir(parents=True)
            write_json(folder / "infos.json", info)
            write_json(folder / "version.json", record)
            write_json(folder / "documentsLiees.json", linked)
            rows = []
            for number, path in enumerate(paths, start=1):
                source = sources[path]
                original_name = Path(source.path).name
                name = f"{number}.pdf" if document.scenario == "lot" else original_name
                destination = attachments / name
                shutil.copyfile(SOURCE / source.path, destination)
                if destination.stat().st_size != source.size or digest(destination) != source.sha256:
                    raise ValueError(f"La copie ne correspond pas au PDF source {source.path}")
                rows.append((name, original_name))
                files.append({
                    "source": source.path, "target": destination.relative_to(staging).as_posix(),
                    "original_filename": original_name, "sha256": source.sha256, "size": source.size,
                    "pages": source.pages, "title": source.title, "title_source": source.title_source,
                    "idTypeDocRef": info["typeDocumentId"], "identity_uuid": info["identiteNodeId"],
                    "version_uuid": record["nodeId"], "version_label": record["versionLabel"],
                    "scenario": document.scenario, "scenario_key": document.key if document.scenario != "direct" else None,
                })
            if document.scenario == "lot":
                with (attachments / "metadata.csv").open("w", encoding="utf-8", newline="") as file:
                    writer = csv.writer(file, lineterminator="\n")
                    writer.writerow(("filename", "original_filename"))
                    writer.writerows(rows)
    return {
        "format": "kbv1.data_fixture", "schema_version": 1, "source_root": "raw_data",
        "conventions": {
            "dates": "Dates d’édition et de validation synthétiques, en millisecondes Unix UTC.",
            "links": "uuid désigne l’identité cible. Les relations sont synthétiques.",
            "versions": "V1_0 et V2_0 de la famille 21911 représentent deux variantes réelles dans un scénario de versions synthétique.",
            "csv": "filename = nom stocké et original_filename = nom dans raw_data.",
        },
        "type_document_ref": list(TYPES.values()),
        "files": sorted(files, key=lambda item: item["source"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / "data")
    destination = parser.parse_args().destination.absolute()
    manifest = destination.with_name(destination.name + ".fixture.json")
    for path in (destination, manifest):
        if path.exists() or path.is_symlink():
            parser.error(f"La destination existe déjà : {path}")
    paths = sorted(path for path in SOURCE.rglob("*")
                   if path.suffix.lower() == ".pdf" and path.is_file() and not path.is_symlink())
    with ThreadPoolExecutor(max_workers=8) as executor:
        sources = {item.path: item for item in executor.map(inspect_pdf, paths)}
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".data-fixture-") as temporary:
        staging = Path(temporary) / "corpus"
        staging.mkdir()
        result = build(staging, sources)
        staged_manifest = Path(temporary) / "manifest.json"
        write_json(staged_manifest, result)
        staging.rename(destination)
        staged_manifest.rename(manifest)
    print(f"{len(sources)} PDF validés dans {destination}")
    print(f"Manifeste {manifest}")


if __name__ == "__main__":
    main()
