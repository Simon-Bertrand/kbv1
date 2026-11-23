import metadata from '../../package.json';
import copy from '../content/fr.json';

export function applicationInformation() {
  const text = copy.applicationInfo;
  const environment = (process.env.KB_ENV || (process.env.NODE_ENV === 'production' ? 'production' : 'dev')).trim().toLowerCase();
  const date = process.env.WEBUI_BUILD_DATE ? new Date(process.env.WEBUI_BUILD_DATE) : null;
  const buildDate = date && Number.isFinite(date.getTime())
    ? new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'medium', timeZone: 'UTC' }).format(date)
    : text.unknown;
  return [
    { label: text.commit, value: process.env.WEBUI_BUILD_COMMIT || text.unknown },
    { label: text.branch, value: process.env.WEBUI_BUILD_BRANCH || text.unknown },
    { label: text.environment, value: environment === 'production' ? 'production' : 'dev' },
    { label: text.buildDate, value: buildDate },
    { label: text.webui, value: metadata.version },
    { label: text.next, value: metadata.dependencies.next },
    { label: text.react, value: metadata.dependencies.react },
    { label: text.pdfjs, value: metadata.dependencies['pdfjs-dist'] },
    { label: text.node, value: process.version },
  ];
}
