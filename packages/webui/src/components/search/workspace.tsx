'use client';
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { createSearchClient } from '../../store/search/client';
import { searchDateFields, type DocumentType, type SearchDateFilters, type SearchHit, type SearchMode } from '../../store/search/types';
import { PdfDocument } from '../chat/source-dialog';
import { Button, IconButton } from '../ui/button';
import { Icon } from '../ui/icon';

const text = copy.documentSearch;

function groupResults(results: readonly SearchHit[]) {
  const groups = new Map<string, SearchHit[]>();
  for (const hit of results) {
    const group = groups.get(hit.documentId);
    if (group) group.push(hit);
    else groups.set(hit.documentId, [hit]);
  }
  return Array.from(groups, ([id, hits], index) => ({
    id,
    anchor: `document-result-${index + 1}`,
    title: hits[0]!.source.title,
    hits,
  }));
}

function Excerpt({ value, terms }: { value: string; terms: ReadonlySet<string> }) {
  return <span className="document-search-excerpt">
    {terms.size === 0 ? value : value.split(/([\p{L}\p{M}\p{N}]+)/u).map((part, index) => (
      terms.has(part.toLocaleLowerCase('fr')) ? <mark key={index}>{part}</mark> : part
    ))}
  </span>;
}

export function SearchWorkspace({ initialMode, documentTypes }: {
  initialMode: SearchMode;
  documentTypes: readonly DocumentType[] | null;
}) {
  const [client] = useState(createSearchClient);
  const state = useSyncExternalStore(client.subscribe, client.getState, client.getInitialState);
  const [question, setQuestion] = useState('');
  const [submittedQuestion, setSubmittedQuestion] = useState('');
  const [limit, setLimit] = useState(10);
  const [mode, setMode] = useState<SearchMode>(initialMode);
  const [perimeter, setPerimeter] = useState('');
  const [dateFilters, setDateFilters] = useState<SearchDateFilters>({});
  const [typeIds, setTypeIds] = useState<number[]>([]);
  const perimeters = new Map(documentTypes?.map((type) => [type.perimeter_code, type.perimeter_label]));
  const types = documentTypes?.filter((type) => !perimeter || type.perimeter_code === perimeter) ?? [];
  const submit = useRef<HTMLButtonElement>(null);
  const [selected, setSelected] = useState<SearchHit['source'] | null>(null);
  const sourcePane = useRef<HTMLElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  useEffect(() => () => client.cancel(), [client]);
  useEffect(() => {
    const pane = sourcePane.current;
    if (selected && pane && getComputedStyle(pane).position !== 'sticky') {
      pane.focus({ preventScroll: true });
      pane.scrollIntoView({ block: 'start' });
    }
  }, [selected]);

  const documents = useMemo(() => groupResults(state.results), [state.results]);
  const terms = useMemo(() => new Set(submittedQuestion.toLocaleLowerCase('fr').match(/[\p{L}\p{M}\p{N}]{2,}/gu)), [submittedQuestion]);
  const isLoading = state.status === 'loading';
  const hasResults = state.results.length > 0;
  const status = isLoading ? text.loading : state.status === 'complete'
    ? hasResults ? formatText(state.results.length === 1 ? text.summaryOne : documents.length === 1
      ? text.summarySingleDocument : text.summary, { count: state.results.length, documents: documents.length }) : text.noResults
    : '';

  function search() {
    const trimmed = question.trim();
    if (!trimmed) return;
    setSubmittedQuestion(trimmed);
    setSelected(null);
    void client.search({
      question: trimmed, limit, mode,
      ...(perimeter ? { perimeter_code: perimeter } : {}),
      ...(Object.keys(dateFilters).length ? { date_filters: dateFilters } : {}),
      ...(typeIds.length ? { type_document_ids: typeIds } : {}),
    });
  }

  return <section className="document-search" aria-labelledby="document-search-title">
    <header className="document-search-heading">
      <h1 id="document-search-title">{text.title}</h1>
      <p className="muted">{text.intro}</p>
    </header>
    <form className="document-search-form" role="search" onSubmit={(event) => {
      event.preventDefault();
      search();
    }}>
      <label htmlFor="document-query" className="sr-only">{text.query}</label>
      <div className="document-search-query">
        <Icon name="search" />
        <input id="document-query" type="search" required maxLength={4000} value={question}
          onChange={(event) => setQuestion(event.target.value)} placeholder={text.placeholder} />
        <Button ref={submit} type="submit" variant="primary" disabled={!question.trim()}>{text.submit}</Button>
      </div>
      <div className="document-search-options">
        <details>
          <summary>{text.options}<Icon name="down" /></summary>
          <div className="document-search-advanced">
            <div className="document-search-controls">
              <label className="field" htmlFor="document-search-mode">{text.mode}
                <select className="input" id="document-search-mode" value={mode}
                  onChange={(event) => setMode(event.target.value as SearchMode)}>
                  <option value="hybrid">{text.hybrid}</option>
                  <option value="semantic">{text.semantic}</option>
                  <option value="lexical">{text.lexical}</option>
                </select>
              </label>
              <label className="field" htmlFor="document-search-limit">{text.limit}
                <select className="input" id="document-search-limit" value={limit}
                  onChange={(event) => setLimit(Number(event.target.value))}>
                  {[10, 20, 50].map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
            </div>
            {perimeters.size > 0 ? <>
              <label className="field">
                {text.perimeter}
                <select className="input" value={perimeter} onChange={(event) => {
                  const selected = event.target.value;
                  setPerimeter(selected);
                  if (selected) setTypeIds((ids) => ids.filter((id) => documentTypes?.some(
                    (type) => type.type_document_id === id && type.perimeter_code === selected,
                  )));
                }}>
                  <option value="">{text.allPerimeters}</option>
                  {[...perimeters].map(([code, label]) => <option key={code} value={code}>{label}</option>)}
                </select>
              </label>
              <details>
                <summary>{typeIds.length ? formatText(text.selectedDocumentTypes, { count: typeIds.length }) : text.documentTypes}<Icon name="down" /></summary>
                <fieldset className="document-search-types">
                  <legend className="sr-only">{text.documentTypes}</legend>
                  {types.map((type) => <label key={type.type_document_id}>
                    <input type="checkbox" checked={typeIds.includes(type.type_document_id)}
                      onChange={(event) => {
                        const checked = event.target.checked;
                        const id = type.type_document_id;
                        setTypeIds((ids) => checked ? [...ids, id] : ids.filter((value) => value !== id));
                      }} />
                    {type.label}
                  </label>)}
                </fieldset>
              </details>
            </> : <p className="muted">{text.documentTypesUnavailable}</p>}
            <details className="document-search-dates">
              <summary>{text.dateFilters}<Icon name="down" /></summary>
              <div className="document-search-dates-content">
                {searchDateFields.map((field) => <fieldset className="document-search-date-range" key={field}>
                  <legend>{text.dateFields[field]}</legend>
                  {(['from', 'to'] as const).map((bound) => <label className="field" key={bound}>
                    {text.dateBounds[bound]}
                    <input className="input" type="date" value={dateFilters[field]?.[bound] ?? ''}
                      min={bound === 'to' ? dateFilters[field]?.from : '0001-01-01'}
                      max={bound === 'from' ? dateFilters[field]?.to : '9999-12-31'}
                      onChange={(event) => {
                        const value = event.target.value;
                        setDateFilters((previous) => {
                          const next = { ...previous };
                          const range = { ...next[field] };
                          if (value) range[bound] = value;
                          else delete range[bound];
                          if (range.from || range.to) next[field] = range;
                          else delete next[field];
                          return next;
                        });
                      }} />
                  </label>)}
                </fieldset>)}
              </div>
            </details>
            <Button className="document-search-reset" variant="ghost"
              disabled={!perimeter && !typeIds.length && !Object.keys(dateFilters).length}
              onClick={() => { setPerimeter(''); setTypeIds([]); setDateFilters({}); }}>
              {text.resetFilters}
            </Button>
          </div>
        </details>
        {isLoading && <Button className="document-search-cancel" onClick={() => {
          submit.current?.focus();
          client.cancel();
        }}>{text.cancel}</Button>}
      </div>
    </form>

    <section className="document-search-output" aria-label={text.results}>
      <div className={state.status === 'idle' ? 'sr-only' : hasResults ? 'document-search-summary' : 'document-search-state'}>
        {hasResults ? <h2>{text.results}</h2> : state.status !== 'idle' && <Icon name="search" />}
        <p role="status" className="muted">{status}</p>
        {state.error && <>
          <p role="alert" className="error-text">{state.error}</p>
          <Button onClick={() => {
            submit.current?.focus();
            search();
          }} disabled={!question.trim()}>{copy.common.retry}</Button>
        </>}
      </div>
      <div className="document-search-results" aria-busy={isLoading}>
        {hasResults && <div className="document-search-list">
          {documents.length > 1 && <nav className="document-search-index" aria-label={text.documents}>
            <h3>{text.documents}</h3>
            <ul>
              {documents.map(({ id, anchor, title, hits }) => <li key={id}>
                <a href={`#${anchor}`}><span>{title}</span><span className="document-search-index-count">{hits.length}</span></a>
              </li>)}
            </ul>
          </nav>}
          {documents.map(({ id, anchor, title, hits }) => <article className="document-search-document"
            id={anchor} tabIndex={-1} aria-labelledby={`${anchor}-title`} key={id}>
            <header className="document-search-document-header">
              <span className="document-search-file-icon"><Icon name="file" /></span>
              <h3 id={`${anchor}-title`}>{title}</h3>
              <span className="document-search-count">{formatText(hits.length === 1 ? text.passageOne : text.passageCount, { count: hits.length })}</span>
            </header>
            <ol className="document-search-passages">
              {hits.map(({ source }, index) => <li key={source.id}>
                <Button className="document-search-open" aria-pressed={selected?.id === source.id}
                  aria-controls="document-search-preview"
                  onClick={(event) => { opener.current = event.currentTarget; setSelected(source); }}
                  aria-label={formatText(text.openNamedPassage, { action: text.open, number: index + 1, title })}>
                  <span className="document-search-passage-heading">
                    {source.pages && <span className="document-search-pages">{source.pages}</span>}
                    <span className="document-search-open-label">{text.open}<Icon name="chevron" /></span>
                  </span>
                  <Excerpt value={source.excerpt} terms={terms} />
                </Button>
              </li>)}
            </ol>
          </article>)}
        </div>}
        {hasResults && <aside className="document-search-preview" id="document-search-preview"
          ref={sourcePane} tabIndex={-1} aria-labelledby="document-search-preview-title" data-selected={!!selected}>
          <header className="document-search-preview-header">
            <div>
              <h3 id="document-search-preview-title">{text.sourcePreview}</h3>
              {selected && <p title={selected.title}>{selected.title}</p>}
            </div>
            {selected && <IconButton icon="close" label={text.closePreview} onClick={() => {
              setSelected(null);
              opener.current?.focus();
            }} />}
          </header>
          {selected?.pdf ? <PdfDocument key={selected.id} id="document-search-pdf"
            reference={selected.pdf} title={selected.title} highlightCitation /> :
            <p className="document-search-preview-placeholder muted">
              {selected ? text.noSourcePage : text.selectPassage}
            </p>}
        </aside>}
      </div>
    </section>
  </section>;
}
