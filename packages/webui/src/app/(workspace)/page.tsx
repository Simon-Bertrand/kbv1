import copy from '../../content/fr.json';
import { ChatWorkspace } from '../../components/chat/workspace';
import { serviceAvailable } from '../../backend/readiness';
import { ServiceAvailability } from '../../components/layout/service-availability';
export const metadata = { title: `${copy.common.conversation} — ${copy.common.brand}` };
export const dynamic = 'force-dynamic';

export default async function Page() {
  const available = await serviceAvailable('agent');
  return <ServiceAvailability available={available} service="agent">{available && <ChatWorkspace demo={false} />}</ServiceAvailability>;
}
