'use client';
import { useId } from 'react';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { Button, IconButton } from '../ui/button';
import { Markdown } from '../ui/markdown';
import { ActivityPanel } from './activity-panel';
import type { ChatProgress } from '../../store/chat/types';
export interface ResponseComparisonProps {
  responseA: string;
  responseB: string;
  state: 'generating' | 'ready' | 'error';
  choice?: 'a' | 'b';
  onChoose: (choice: 'a' | 'b') => void;
  onClose?: () => void;
  onRetry?: () => void;
  id?: string;
  activity?: ChatProgress;
}
export function ResponseComparison({
  responseA,
  responseB,
  state,
  choice,
  onChoose,
  onClose,
  onRetry,
  id,
  activity,
}: ResponseComparisonProps) {
  const titleId = useId();
  const selectable =
    state === 'ready' && !!responseB.trim() && responseA.trim() !== responseB.trim();
  return (
    <section className="response-comparison" data-comparison={id} aria-labelledby={titleId}>
      <header className="comparison-heading">
        <div>
          <h3 id={titleId}>{copy.comparison.quelleVersionRepondLeMieux}</h3>
          <p className="muted">{copy.comparison.deuxReponsesAVotreQuestionChoisissezLa}</p>
        </div>
        {onClose && (
          <IconButton icon="close" label={copy.comparison.fermerLaComparaison} onClick={onClose} />
        )}
      </header>
      <div className="comparison-grid">
        {(['a', 'b'] as const).map((version) => (
          <section
            key={version}
            className={`comparison-card ${choice === version ? 'is-chosen' : ''}`}
            data-response-version={version}
          >
            <div className="comparison-card-heading">
              <strong>
                {formatText(copy.comparison.versionLabel, { version: version.toUpperCase() })}
              </strong>
              {choice === version && (
                <span className="badge badge--success">{copy.comparison.votreChoix}</span>
              )}
            </div>
            <div className="comparison-answer">
              {version === 'a' ? (
                <Markdown text={responseA} />
              ) : state === 'generating' ? (
                <div>
                  <p role="status" className="muted">
                    {copy.comparison.preparationDeLaSecondeVersion}
                  </p>
                  <ActivityPanel activity={activity} generating={true} />
                </div>
              ) : state === 'error' ? (
                <div>
                  <p role="status">{copy.comparison.laSecondeVersionNEstPasDisponible}</p>
                  {activity && <ActivityPanel activity={activity} generating={false} error={true} />}
                </div>
              ) : (
                <div>
                  <Markdown text={responseB} />
                  {activity && <ActivityPanel activity={activity} generating={false} />}
                </div>
              )}
            </div>
            <Button
              variant={choice === version ? 'primary' : undefined}
              aria-label={formatText(copy.comparison.chooseVersion, {
                version: version.toUpperCase(),
              })}
              aria-pressed={choice === version}
              disabled={!selectable}
              data-compare-choice={version}
              data-comparison-id={id}
              onClick={() => onChoose(version)}
            >
              {choice === version
                ? copy.comparison.versionChoisie
                : copy.comparison.cetteVersionRepondMieux}
            </Button>
          </section>
        ))}
      </div>
      <p className="comparison-status muted" role="status">
        {choice
          ? copy.comparison.merciVotreChoixPeutAiderAAmeliorer
          : selectable
            ? copy.comparison.selectHint
            : state === 'ready'
              ? copy.comparison.lesDeuxVersionsSontIdentiquesAucunChoix
              : ''}
      </p>
      {(state === 'error' || (state === 'ready' && !selectable)) && onRetry && (
        <Button onClick={onRetry}>{copy.common.retry}</Button>
      )}
    </section>
  );
}
