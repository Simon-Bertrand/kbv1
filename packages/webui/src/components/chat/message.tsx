'use client';
import type { Message as ChatMessage, Source } from '../../store/chat/types';
import type { Annotation } from '../../store/feedback/types';
import { isCompleteResponse } from '../../store/chat/message';
import copy from '../../content/fr.json';
import { Icon } from '../ui/icon';
import { Button, IconButton } from '../ui/button';
import { Markdown } from '../ui/markdown';
import { ResponseComparison } from './response-comparison';
import { ActivityPanel } from './activity-panel';
export interface MessageProps {
  message: ChatMessage;
  busy: boolean;
  retryable: boolean;
  feedback?: Extract<Annotation, { kind: 'feedback' }>;
  onSource: (source: Source) => void;
  onRetry: () => void;
  onEscalate?: () => void;
  onThumb: (sentiment: 'positive' | 'negative') => void;
  onComment: () => void;
  onChoose: (version: 'a' | 'b') => void;
  onCloseComparison: () => void;
  onCompare: () => void;
  onCopy: () => void;
}
export function Message({
  message,
  busy,
  retryable,
  feedback,
  onSource,
  onRetry,
  onEscalate,
  onThumb,
  onComment,
  onChoose,
  onCloseComparison,
  onCompare,
  onCopy,
}: MessageProps) {
  const assistant = message.role === 'assistant';
  return (
    <article
      className={`message message--${assistant ? 'assistant' : 'user'}`}
      data-message={message.id}
      aria-label={assistant ? copy.chat.reponseDeReno : copy.chat.votreMessage}
    >
      <div className={`avatar ${assistant ? '' : 'avatar--user'}`}>
        {assistant ? copy.common.brandMark : copy.common.userInitials}
      </div>
      <div className="message-content">
        <div className="message-heading">
          <strong>{assistant ? copy.common.brand : copy.common.you}</strong>
          <span>{assistant ? copy.common.assistant : copy.chat.aLInstant}</span>
          {assistant && message.mode === 'fast' && (
            <span className="badge badge--accent badge--fast-path" data-badge="fast-path">
              {copy.chat.fastPath}
            </span>
          )}
        </div>
        <div
          className="message-body"
          onClick={(event) => {
            const link = (event.target as Element).closest<HTMLAnchorElement>(
              'a[href^="#source-"]',
            );
            if (!link) return;
            event.preventDefault();
            const version =
              link.closest<HTMLElement>('[data-response-version]')?.dataset.responseVersion;
            const sources = message.comparison
              ? version === 'b'
                ? message.comparison.sourcesB
                : message.comparison.sourcesA
              : message.sources;
            const source = sources?.find((item) => item.id === Number(link.hash.split('-').at(-1)));
            if (source) onSource(source);
          }}
        >
          {message.comparison ? (
            <ResponseComparison
              {...message.comparison}
              id={message.id}
              onChoose={onChoose}
              onClose={onCloseComparison}
              onRetry={onCompare}
            />
          ) : (
            <Markdown text={message.content} />
          )}
        </div>
        {assistant && !message.comparison && (
          <ActivityPanel
            activity={message.activity}
            generating={message.generating}
            interrupted={message.interrupted}
            error={message.error}
          />
        )}
        {!message.comparison && !!message.sources?.length && (
          <div className="message-sources">
            <span className="eyebrow">{copy.chat.sourcesConsultees}</span>
            <div>
              {message.sources.map((source) => (
                <button
                  key={source.id}
                  className="source-chip"
                  data-source={source.id}
                  onClick={() => onSource(source)}
                >
                  <span>{source.id}</span>
                  {source.title}
                  <Icon name="external" />
                </button>
              ))}
            </div>
          </div>
        )}
        {message.interrupted && <p className="muted">{copy.common.reponseInterrompue}</p>}
        {message.error && (
          <p role="alert" className="error-text">
            {message.errorMessage || copy.chat.laReponseNAPasPuEtre}
          </p>
        )}
        {assistant && message.mode === 'fast' && !message.comparison && (
          <div className="message-escalation">
            <Button
              variant="ghost"
              className="escalate-button"
              data-escalate={message.id}
              disabled={busy || message.generating || message.escalating}
              onClick={onEscalate}
            >
              <Icon name="search" />
              <span>{message.escalating ? copy.chat.escalating : copy.chat.escalateDeep}</span>
            </Button>
          </div>
        )}
        {assistant && !message.comparison && (
          <div className="message-actions">
            <IconButton icon="copy" label={copy.chat.copierLaReponse} onClick={onCopy} />
            <IconButton
              icon="refresh"
              label={copy.chat.regenererLaReponse}
              data-retry={message.id}
              disabled={busy || !retryable}
              title={!retryable ? copy.chat.onlyLastCanRegenerate : copy.chat.regenerer}
              onClick={onRetry}
            />
            {(['positive', 'negative'] as const).map((sentiment) => (
              <IconButton
                key={sentiment}
                icon={sentiment === 'positive' ? 'thumbUp' : 'thumbDown'}
                label={
                  sentiment === 'positive' ? copy.chat.reponseUtile : copy.chat.reponseAAmeliorer
                }
                className={`feedback-${sentiment}`}
                data-feedback={sentiment}
                data-message-id={message.id}
                aria-pressed={feedback?.values.sentiment === sentiment}
                disabled={!isCompleteResponse(message)}
                onClick={() => onThumb(sentiment)}
              />
            ))}
            {feedback && (
              <Button
                variant="ghost"
                className="feedback-comment"
                data-feedback-comment={message.id}
                onClick={onComment}
              >
                {feedback.comment
                  ? copy.feedbackMessages.modifierLeCommentaire
                  : copy.common.ajouterUnCommentaire}
              </Button>
            )}
            <span className="muted" data-feedback-status={message.id}>
              {feedback
                ? copy.feedbackMessages.avisEnregistre
                : message.error
                  ? copy.chat.erreurDeGeneration
                  : copy.chat.reponseDeDemonstration}
            </span>
          </div>
        )}
      </div>
    </article>
  );
}
