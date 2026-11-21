'use strict';
const $ = (id) => document.getElementById(id);
const q = (value) => encodeURIComponent(value);
const parts = () => new URLSearchParams(location.search).get('path')?.split('/').filter(Boolean) || [];
const fileURL = (path) => '/files/' + path.split('/').map(q).join('/');
let current = [];
const PAGE_SIZE = 50;
let filtered = [];
let displayed = 0;

function size(num) {
  if (num == null || Number.isNaN(Number(num))) return '—';
  if (num === 0) return '0 o';
  const units = ['o', 'Kio', 'Mio', 'Gio', 'Tio'];
  let n = Number(num), i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toLocaleString('fr-FR', {maximumFractionDigits: 1})} ${units[i]}`;
}
function row(entry, path) {
  const tr = document.createElement('tr');
  const isDir = entry.type === 'directory';
  const nameCell = tr.insertCell();
  const link = document.createElement('a');
  link.className = 'file';
  link.href = isDir ? '/?path=' + q(path) : fileURL(path);
  if (!isDir) link.download = entry.name;
  const ico = document.createElement('span');
  ico.className = 'ico'; ico.textContent = isDir ? '▣' : '▤';
  link.append(ico, document.createTextNode(entry.name)); nameCell.append(link);
  tr.insertCell().textContent = isDir ? 'Dossier' : 'Fichier';
  tr.insertCell().textContent = isDir ? '—' : size(entry.size);
  tr.insertCell().textContent = entry.mtime ? new Date(entry.mtime).toLocaleString('fr-FR') : '—';
  const action = tr.insertCell();
  const actionLink = document.createElement('a');
  actionLink.className = 'action';
  actionLink.href = isDir ? '/api/zip?path=' + q(path) : fileURL(path);
  actionLink.textContent = isDir ? 'ZIP ↓' : '↓';
  action.append(actionLink);
  return tr;
}
function appendPage() {
  const next = filtered.slice(displayed, displayed + PAGE_SIZE);
  const fragment = document.createDocumentFragment();
  next.forEach(({entry, path}) => fragment.append(row(entry, path)));
  $('entries').append(fragment);
  displayed += next.length;
  $('count').textContent = `${displayed} / ${filtered.length} élément(s)`;
}
function render() {
  const filter = $('search').value.toLocaleLowerCase('fr');
  const rows = current.filter(({entry}) => entry.name.toLocaleLowerCase('fr').includes(filter));
  rows.sort((a,b) => (a.entry.type === 'directory' ? 0 : 1) - (b.entry.type === 'directory' ? 0 : 1) || a.entry.name.localeCompare(b.entry.name, 'fr'));
  filtered = rows;
  displayed = 0;
  $('entries').replaceChildren();
  document.querySelector('.table-wrap').scrollTop = 0;
  appendPage();
  $('message').hidden = rows.length > 0;
  if (!rows.length) $('message').textContent = 'Aucun fichier à afficher.';
}
async function load() {
  const dir = parts().join('/');
  const breadcrumbs = $('crumbs'); breadcrumbs.replaceChildren();
  const home = document.createElement('a'); home.textContent = 'Racine'; home.href = '/'; breadcrumbs.append(home);
  const p = parts();
  p.forEach((component, i) => {
    breadcrumbs.append(document.createTextNode('/'));
    const el = document.createElement('a'); el.textContent = component;
    el.href = '/?path=' + q(p.slice(0,i+1).join('/')); breadcrumbs.append(el);
  });
  $('heading').textContent = p.at(-1) || 'Fichiers';
  $('download-zip').href = '/api/zip?path=' + q(dir);
  $('entries').replaceChildren();
  $('message').hidden = false; $('message').textContent = 'Chargement…';
  try {
    const response = await fetch('/api/list?path=' + q(dir), {cache: 'no-store'});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    const result = await response.json();
    current = result.entries.map(entry => ({entry, path: [dir, entry.name].filter(Boolean).join('/')}));
    render();
  } catch (e) {
    $('message').hidden = false;
    $('message').textContent = `Impossible de lister ce dossier (${e.message}).`;
  }
}
$('search').addEventListener('input', render);
document.querySelector('.table-wrap').addEventListener('scroll', ({currentTarget}) => {
  if (displayed < filtered.length &&
      currentTarget.scrollHeight - currentTarget.scrollTop - currentTarget.clientHeight <= 200) {
    appendPage();
  }
});
load();
