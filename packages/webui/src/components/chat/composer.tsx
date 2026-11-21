'use client';
import { memo, useRef } from 'react';
import { useStoreSelector } from '../providers/use-selector';
import { shallowEqual } from '../../store/create-store';
import { activeConversation } from '../../store/chat/state';
import type { ChatModel } from '../../store/chat/model';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import { Icon } from '../ui/icon';
import { IconButton } from '../ui/button';
export const Composer = memo(function Composer({
  chat,
  demo,
  latest,
  onLatest,
}: {
  chat: ChatModel;
  demo: boolean;
  latest: boolean;
  onLatest: () => void;
}) {
  const [draft, attachments, busy] = useStoreSelector(
    chat.store,
    (state) => [activeConversation(state).draft ?? '', state.attachments, state.busy] as const,
    shallowEqual,
  );
  const prompt = useRef<HTMLTextAreaElement>(null);
  return (
    <div className="composer-area">
      {latest && (
        <button
          className="button latest-response"
          id="latest-response"
          aria-controls="conversation-scroll"
          onClick={onLatest}
        >
          <Icon name="down" />
          {copy.chat.derniereReponse}
        </button>
      )}
      <form
        id="composer"
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void chat.send();
        }}
      >
        {demo && !!attachments.length && (
          <div id="attachments" className="attachments">
            {attachments.map((file, index) => (
              <span className="attachment" key={`${file.name}:${index}`}>
                <span>{file.name}</span>
                <IconButton
                  icon="close"
                  label={formatText(copy.chatMessages.removeAttachment, { fileName: file.name })}
                  onClick={() => chat.removeFile(index)}
                />
              </span>
            ))}
          </div>
        )}
        <label className="sr-only" htmlFor="prompt">
          {copy.chat.votreMessage}
        </label>
        <textarea
          ref={prompt}
          id="prompt"
          name="prompt"
          rows={2}
          maxLength={16000}
          placeholder={copy.chat.ecrivezVotreMessage}
          value={draft}
          onChange={(event) => chat.setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (!busy) event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className="composer-toolbar">
          {demo && <div className="composer-tools">
            <label
              className="icon-button tooltip file-picker"
              id="attach-button"
              data-tooltip={copy.chat.joindreUnDocument}
            >
              <Icon name="attach" />
              <span className="sr-only">{copy.chat.joindreUnDocument}</span>
              <input
                id="file-input"
                className="sr-only"
                type="file"
                multiple
                accept=".pdf,.txt,.md,.csv"
                disabled={busy}
                onChange={(event) => {
                  chat.addFiles(Array.from(event.target.files ?? []));
                  event.target.value = '';
                }}
              />
            </label>
          </div>}
          <div className="composer-tools">
            <span className="enter-hint">{copy.chat.entreePourEnvoyer}</span>
            <button
              className="button button--primary send-button"
              id="send-button"
              type="submit"
              disabled={busy || !draft.trim()}
              aria-label={copy.chat.envoyerLeMessage}
            >
              <Icon name="arrow" />
            </button>
            <button
              className="button button--primary send-button"
              id="stop-button"
              type="button"
              aria-label={copy.chat.arreterLaReponse}
              onClick={() => {
                chat.stop();
                prompt.current?.focus();
              }}
            >
              <Icon name="stop" />
            </button>
          </div>
        </div>
      </form>
      <p className="composer-note">
        {copy.chat.verifiezLesInformationsImportantes}
        {demo && <span>{copy.common.localDemo}</span>}
      </p>
    </div>
  );
});
