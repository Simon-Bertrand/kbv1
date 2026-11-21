import { readiness, serviceAvailable } from '../../../backend/readiness';
import { RefreshServiceStatus } from '../../../components/layout/service-availability';
import copy from '../../../content/fr.json';

const text = copy.serviceStatus;
const services = ['agent', 'database', 'retrieval', 'reranker', 'files'] as const;

export const metadata = { title: `${text.title} — ${copy.common.brand}` };
export const dynamic = 'force-dynamic';

export default async function Page() {
  const [response, reranker] = await Promise.all([readiness(), serviceAvailable('reranker')]);
  const { capabilities } = await response.json() as { capabilities: Record<Exclude<(typeof services)[number], 'reranker'>, boolean> };
  const availability = { ...capabilities, reranker };

  return <div className="service-status-page">
    <section className="conversation-container" aria-labelledby="status-title">
      <RefreshServiceStatus />
      <h1 id="status-title">{text.title}</h1>
      <ul className="service-status-list" role="list">
        {services.map((service) => <li key={service}>
          <strong>{text[service]}</strong>
          <span className="service-status-value" data-online={availability[service]}>
            <span className="status-dot" aria-hidden="true" />
            {availability[service] ? text.online : text.offline}
          </span>
        </li>)}
      </ul>
    </section>
  </div>;
}
