'use client';
import { useState, type ReactNode } from 'react';
import copy from '../../content/fr.json';
import { comparisonExample } from '../../store/chat/demo-data';
import { Button } from '../ui/button';
import { Dialog } from '../ui/dialog';
import { ResponseComparison } from '../chat/response-comparison';
import { useRenoActions } from '../chat/provider';
import { ClientExample } from './client-example';
const c = copy.catalog;
function Section({
  label,
  title,
  children,
  className = '',
}: {
  label: string;
  title: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      <div className="eyebrow">{label}</div>
      <h2>{title}</h2>
      {children}
    </section>
  );
}
export function Catalog() {
  const { notify, setAnnotationsOpen, save } = useRenoActions();
  const [dialog, setDialog] = useState(false);
  const [choice, setChoice] = useState<'a' | 'b'>();
  const choose = (value: 'a' | 'b') => {
    const now = new Date().toISOString();
    if (
      save({
        schemaVersion: 1,
        id: crypto.randomUUID(),
        createdAt: now,
        updatedAt: now,
        comment: '',
        kind: 'evaluation',
        snapshot: {
          conversationId: 'catalog',
          messageId: comparisonExample.id,
          prompt: comparisonExample.prompt,
          context: [{ role: 'user', content: comparisonExample.prompt }],
          response: comparisonExample.responseA,
          sources: [],
          origin: 'demo',
        },
        values: {
          mode: 'dpo',
          comparisonId: comparisonExample.id,
          preference: value,
          alternative: {
            id: `${comparisonExample.id}-b`,
            text: comparisonExample.responseB,
            origin: 'demo',
          },
        },
      })
    )
      setChoice(value);
  };
  return (
    <section className="catalog-page" aria-labelledby="catalog-title">
      <div className="eyebrow">{c.renoBibliothequeUi}</div>
      <h1 id="catalog-title">{c.composantsUi}</h1>
      <p className="catalog-intro">{c.boutonsFormulairesEtComposantsDisponiblesDansLes}</p>
      <div className="catalog-grid">
        <Section label={c.label01PaletteSemantique} title={c.couleursDesThemes}>
          <div className="swatches">
            {['accent', 'accent-soft', 'canvas', 'surface', 'text', 'muted'].map((color) => (
              <div key={color}>
                <span className={`swatch swatch--${color}`} />
                <code>
                  {c.color}
                  {color}
                </code>
              </div>
            ))}
          </div>
          <p className="muted">{c.toutesLesCouleursSontDefiniesDansTheme}</p>
        </Section>
        <Section label={c.label02Actions} title={c.boutonsVariantes}>
          <div className="sample-row">
            <Button
              variant="primary"
              onClick={() => notify(c.actionPrincipaleDeclenchee, 'success')}
            >
              {copy.common.newConversation}
            </Button>
            <Button onClick={() => notify(c.actionSecondaireDeclenchee)}>{c.secondaire}</Button>
            <Button variant="ghost" onClick={() => notify(c.actionDiscreteDeclenchee)}>
              {c.discret}
            </Button>
            <Button disabled>{c.indisponible}</Button>
          </div>
          <p className="muted">{c.etatsReposSurvolFocusClavierEtDesactive}</p>
        </Section>
        <Section label={c.label03Formulaires} title={c.champsDeSaisie}>
          <div className="sample-fields">
            <label className="field">
              {c.nomDeLEspace}
              <input className="input" placeholder={c.exEquipeDesign} />
            </label>
            <label className="field">
              {c.modele}
              <select className="input">
                <option>{c.renoDemoSimulationLocale}</option>
              </select>
            </label>
            <label className="field">
              {c.adresseEMail}
              <input
                className="input"
                type="email"
                defaultValue={c.invalidEmailExample}
                aria-invalid="true"
                aria-describedby="email-error"
              />
              <span id="email-error" className="field-error">
                {c.renseignezUneAdresseEMailValide}
              </span>
            </label>
          </div>
        </Section>
        <Section label={c.label04Indicateurs} title={c.statutsEtBadges}>
          <div className="sample-row">
            <span className="badge badge--accent">{c.label3Sources}</span>
            <span className="badge badge--success">{c.disponible}</span>
            <span className="badge">{c.demonstration}</span>
          </div>
          <details>
            <summary>{c.quandUtiliserUnBadge}</summary>
            <p>{c.pourUnStatutUneCategorieOuUne}</p>
          </details>
        </Section>
        <Section label={c.label05Typographie} title={c.exempleDeTitre}>
          <p>{c.sourceSans3PourLesTextesEt}</p>
          <p className="muted">{c.texteSecondaireContexteEtMetadonnees}</p>
          <code>{c.constClarityTrue}</code>
        </Section>
        <Section label={c.label06ElementsSuperposes} title={c.desInteractionsAccessibles}>
          <p className="muted">{c.dialoguesNatifsFermetureParEchapEtRestitution}</p>
          <div className="sample-row">
            <Button id="sample-dialog-open" onClick={() => setDialog(true)}>
              {c.ouvrirUnDialogue}
            </Button>
            <Button id="sample-menu-trigger" popoverTarget="sample-menu">
              {c.menu}
            </Button>
            <div id="sample-menu" className="sample-menu card" popover="auto">
              <Button
                variant="ghost"
                popoverTarget="sample-menu"
                popoverTargetAction="hide"
                onClick={() => notify(c.elementDeMenuSelectionne)}
              >
                {c.optionDeDemonstration}
              </Button>
            </div>
          </div>
        </Section>
        <Section
          label={c.label07ReponsesComparees}
          title={c.unChoixDirectementDansLeChat}
          className="evaluation-catalog"
        >
          <p className="muted">{c.lUtilisateurChoisitLaVersionQuiRepond}</p>
          <div id="catalog-comparison">
            <ResponseComparison
              {...comparisonExample}
              state="ready"
              choice={choice}
              onChoose={choose}
            />
          </div>
          <Button data-annotations onClick={() => setAnnotationsOpen(true)}>
            {c.consulterEtExporterLesAnnotations}
          </Button>
        </Section>
        <Section label={c.label08Notifications} title={c.retoursDAction}>
          <p className="muted">{c.notificationsLocalesFermablesEtAccessiblesLesErreurs}</p>
          <div className="sample-row">
            {(['info', 'success', 'warning', 'error'] as const).map((kind) => (
              <Button
                key={kind}
                data-notification-kind={kind}
                onClick={() =>
                  notify(
                    {
                      info: c.lesDocumentsRestentSurCetAppareil,
                      success: c.votreModificationEstEnregistree,
                      warning: c.verifiezLesInformationsAvantDeContinuer,
                      error: c.impossibleDeTerminerCetteActionReessayez,
                    }[kind],
                    kind,
                  )
                }
              >
                {copy.common[kind]}
              </Button>
            ))}
          </div>
        </Section>
        <ClientExample />
      </div>
      <Dialog id="sample-dialog" title={c.exempleDeDialogue} open={dialog} onOpenChange={setDialog}>
        <p>{c.ceDialogueUtiliseLElementHtmlNatif}</p>
        <form method="dialog">
          <Button type="submit" variant="primary">
            {c.compris}
          </Button>
        </form>
      </Dialog>
    </section>
  );
}
