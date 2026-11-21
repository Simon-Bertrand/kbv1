import copy from '../../../content/fr.json';
import { SearchWorkspace } from '../../../components/search/workspace';
import { configuredSearchMode, serviceAvailable } from '../../../backend/readiness';
import { createSearchService } from '../../../backend/search/service';
import { ServiceAvailability } from '../../../components/layout/service-availability';
export const metadata = { title: `${copy.documentSearch.title} — ${copy.common.brand}` };
export const dynamic = 'force-dynamic';

export default async function Page() {
  const available = await serviceAvailable('retrieval');
  const documentTypes = available
    ? await createSearchService({ env: process.env }).documentTypes().catch(() => null)
    : null;
  return <ServiceAvailability available={available} service="retrieval">
    {available && <SearchWorkspace initialMode={configuredSearchMode()} documentTypes={documentTypes} />}
  </ServiceAvailability>;
}
