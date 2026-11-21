'use client';
import copy from '../../content/fr.json';
import { formatText } from '../../content/text';
import {
  downloadAnnotations,
  exportRows,
  type ExportFormat,
} from '../../store/feedback/repository';
import { Dialog } from '../ui/dialog';
import { Button } from '../ui/button';
import { Icon } from '../ui/icon';
import { useRenoActions, useChatSelector } from './provider';
import { useUiSelector } from '../providers/store-provider';
import { activeConversation } from '../../store/chat/state';
import { shallowEqual } from '../../store/create-store';
export function FeedbackDialog() {
  const reno = useRenoActions();
  const [feedbackId, feedbackError] = useUiSelector(
    (state) => [state.feedbackId, state.feedbackError] as const,
    shallowEqual,
  );
  const message = useChatSelector((state) =>
    feedbackId
      ? activeConversation(state).messages.find((item) => item.id === feedbackId)
      : undefined,
  );
  const saved = useUiSelector((state) =>
    state.records.find(
      (item) =>
        item.kind === 'feedback' &&
        item.snapshot.messageId === message?.id &&
        item.snapshot.response === message?.content,
    ),
  );
  const feedback = saved?.kind === 'feedback' ? saved : undefined;
  return (
    <Dialog
      id="feedback-dialog"
      className="annotation-dialog quick-feedback-dialog"
      bodyId="feedback-content"
      open={!!message}
      onOpenChange={(open) => {
        if (!open) reno.openFeedback(null);
      }}
      title={copy.feedback.votreAvisSurLaReponse}
    >
      {message && (
        <form
          key={message.id + message.content}
          id="feedback-form"
          className="annotation-form"
          onSubmit={(event) => {
            if (
              (event.nativeEvent as SubmitEvent).submitter instanceof HTMLButtonElement &&
              ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement).formMethod ===
                'dialog'
            )
              return;
            event.preventDefault();
            reno.comment(message.id, new FormData(event.currentTarget));
          }}
        >
          <p className="feedback-invitation">{copy.feedback.chaqueRetourPeutAiderAAmeliorerLe}</p>
          <fieldset className="sentiment-options">
            <legend className="sr-only">{copy.feedback.votreAvis}</legend>
            {(['positive', 'negative'] as const).map((sentiment) => (
              <label key={sentiment}>
                <input
                  type="radio"
                  name="sentiment"
                  value={sentiment}
                  defaultChecked={(feedback?.values.sentiment ?? 'positive') === sentiment}
                />
                <Icon name={sentiment === 'positive' ? 'thumbUp' : 'thumbDown'} />
                {sentiment === 'positive' ? copy.common.useful : copy.common.needsImprovement}
              </label>
            ))}
          </fieldset>
          <fieldset className="issue-options">
            <legend>
              {copy.feedback.unPointAPartager} <span className="muted">{copy.common.optional}</span>
            </legend>
            {[
              ['clear', copy.feedback.claire],
              ['useful', copy.feedback.pratique],
              ['inaccurate', copy.feedback.inexacte],
              ['incomplete', copy.feedback.incomplete],
              ['bad-citation', copy.feedback.sourcesPeuUtiles],
            ].map(([value, label]) => (
              <label key={value}>
                <input
                  type="checkbox"
                  name="issues"
                  value={value}
                  defaultChecked={feedback?.values.issues.includes(value!)}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <label className="field">
            {copy.feedback.unCommentaire}
            <textarea
              className="input"
              name="comment"
              rows={3}
              maxLength={4000}
              placeholder={copy.feedback.ceQuiVousAAideOuCe}
              defaultValue={feedback?.comment ?? ''}
            />
          </label>
          <p className="form-hint muted">{copy.feedback.avisConserveSurCetAppareilDansCette}</p>
          <p role="alert" className="error-text" data-form-error>
            {feedbackError}
          </p>
          <div className="annotation-footer">
            <Button type="submit" formMethod="dialog" formNoValidate>
              {copy.common.cancel}
            </Button>
            <Button type="submit" variant="primary">
              {copy.feedback.enregistrerMonAvis}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
export function AnnotationDialog() {
  const reno = useRenoActions();
  const [annotationsOpen, records, annotationsPersistent] = useUiSelector(
    (state) => [state.annotationsOpen, state.records, state.annotationsPersistent] as const,
    shallowEqual,
  );
  return (
    <Dialog
      id="annotations-dialog"
      className="annotation-dialog"
      bodyId="annotations-content"
      open={annotationsOpen}
      onOpenChange={reno.setAnnotationsOpen}
      title={copy.feedback.annotationsEnregistrees}
    >
      <p className="muted">{copy.feedback.exportJsonlLesScoresSontDesAnnotations}</p>
      {!annotationsPersistent && (
        <p role="status">{copy.feedback.stockageIndisponibleCetteSessionUniquement}</p>
      )}
      <p>{formatText(copy.feedback.annotationCount, { count: records.length })}</p>
      <div className="export-actions">
        {(['annotations', 'dpo', 'ppo'] as ExportFormat[]).map((format) => {
          const count = exportRows(records, format).length;
          if (format === 'ppo' && !count) return null;
          return (
            <Button
              key={format}
              data-export={format}
              disabled={!count}
              onClick={() => {
                if (downloadAnnotations(records, format))
                  reno.notify(copy.feedbackMessages.annotationsExportees);
              }}
            >
              <Icon name="download" />
              {format === 'annotations'
                ? copy.feedback.tousLesRetours
                : format === 'dpo'
                  ? copy.feedback.preferencesDpo
                  : copy.feedback.notesPpo}{' '}
              <span className="badge">{count}</span>
            </Button>
          );
        })}
      </div>
      {!records.length && <p>{copy.feedback.aucunAvisPourLeMomentUtilisezLes}</p>}
      <div className="annotation-list">
        {[...records].reverse().map((record) => (
          <article className="annotation-list-item" key={record.id}>
            <span className="badge">
              {record.kind === 'feedback'
                ? copy.feedback.satisfaction
                : record.values.mode.toUpperCase()}
            </span>
            <h3>{record.snapshot.prompt}</h3>
            <p>
              {record.kind === 'feedback'
                ? record.values.sentiment === 'positive'
                  ? copy.common.useful
                  : copy.common.needsImprovement
                : record.values.mode === 'dpo'
                  ? record.values.preference === 'a'
                    ? copy.feedback.reponseAPreferee
                    : copy.feedback.reponseBPreferee
                  : formatText(copy.feedback.legacyRating, {
                      score: record.values.score,
                      maximum: 10,
                    })}
            </p>
            {record.comment && <p>{record.comment}</p>}
            <span className="muted">
              {new Date(record.updatedAt).toLocaleString(copy.application.locale)}
            </span>
          </article>
        ))}
      </div>
    </Dialog>
  );
}
