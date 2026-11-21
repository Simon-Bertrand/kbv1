'use client';

import { useEffect, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { Icon } from '../ui/icon';
import copy from '../../content/fr.json';

export function RefreshServiceStatus() {
  const router = useRouter();
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') router.refresh();
    };
    const timer = window.setInterval(refresh, 5 * 60_000);
    return () => window.clearInterval(timer);
  }, [router]);

  return null;
}

export function ServiceAvailability({
  available, service, children,
}: { available: boolean; service: 'agent' | 'retrieval'; children: ReactNode }) {
  return <>
    <RefreshServiceStatus />
    {available ? children : (
      <section className="service-unavailable" aria-labelledby="unavailable-title">
        <div className="service-unavailable-panel" role="status">
          <span className="service-unavailable-icon"><Icon name="alert" /></span>
          <h1 id="unavailable-title">{copy.common.serviceUnavailable}</h1>
          <p>{copy.serviceStatus.unavailableDescription[service]}</p>
        </div>
      </section>
    )}
  </>;
}
