'use client';
import { useEffect, useState } from 'react';
import type { ChatProgress } from '../../store/chat/types';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';

const text = copy.agentChat.activities;

export interface ActivityPanelProps {
  activity?: ChatProgress;
  generating?: boolean;
  interrupted?: boolean;
  error?: boolean;
}

export function ActivityPanel({
  activity,
  generating,
  interrupted,
  error,
}: ActivityPanelProps) {
  const [liveElapsed, setLiveElapsed] = useState(activity?.elapsedSeconds ?? 0);

  const status = interrupted ? 'interrupted'
    : error ? 'error'
    : activity?.status === 'failed' ? 'error'
    : activity?.status === 'interrupted' ? 'interrupted'
    : generating ? (activity?.status ?? 'running')
    : 'complete';

  useEffect(() => {
    if (activity?.elapsedSeconds !== undefined) {
      setLiveElapsed(activity.elapsedSeconds);
    }
  }, [activity?.elapsedSeconds]);

  useEffect(() => {
    if (!generating || status !== 'running') return;
    const interval = setInterval(() => {
      setLiveElapsed((prev) => (prev < 900 ? prev + 1 : prev));
    }, 1000);
    return () => clearInterval(interval);
  }, [generating, status]);

  if (!generating && !activity?.steps?.length && !activity?.summary && !interrupted && !error) {
    return null;
  }

  const summary = interrupted ? text.interrupted
    : error ? text.failed
    : activity?.status === 'failed' ? text.failed
    : activity?.status === 'interrupted' ? text.interrupted
    : !generating && activity?.status === 'running' ? text.completed
    : activity?.summary || (generating ? text.initialSummary : text.completed);

  const displayElapsed = (!generating && activity?.elapsedSeconds !== undefined)
    ? activity.elapsedSeconds
    : liveElapsed;
  const rawSteps = activity?.steps ?? [];

  const steps = rawSteps.map((step) => {
    if (step.status === 'running') {
      if (interrupted || status === 'interrupted') return { ...step, status: 'error' as const };
      if (error || status === 'error') return { ...step, status: 'error' as const };
      if (!generating) return { ...step, status: 'complete' as const };
    }
    return step;
  });

  const isSpinning = generating && !interrupted && !error && status === 'running';

  return (
    <div className={`activity-panel activity-panel--${status}`} data-testid="activity-panel">
      <div className="activity-status-row">
        {isSpinning && (
          <span className="activity-indicator" aria-hidden="true" />
        )}
        <span
          className="activity-summary-text"
          {...(generating ? { 'aria-live': 'polite' as const } : {})}
        >
          {summary}
        </span>
        {displayElapsed > 0 && (
          <span className="activity-elapsed" aria-hidden="true">
            {formatText(text.secondsElapsed, { seconds: displayElapsed })}
          </span>
        )}
      </div>

      {steps.length > 0 && (
        <details className="activity-details">
          <summary className="activity-toggle">{text.toggleDetails}</summary>
          <ol className="activity-timeline" aria-label={text.timelineLabel}>
            {steps.map((step) => (
              <li
                key={step.id}
                className={`activity-step activity-step--${step.status}`}
                data-step-id={step.id}
                data-phase={step.phase}
              >
                <span className="activity-step-bullet" aria-hidden="true" />
                <span className="activity-step-label">{step.label}</span>
                {step.detail && <span className="activity-step-detail">{step.detail}</span>}
                {step.durationMs !== undefined && (
                  <span className="activity-step-duration">
                    {formatText(text.durationMs, { duration: step.durationMs })}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
