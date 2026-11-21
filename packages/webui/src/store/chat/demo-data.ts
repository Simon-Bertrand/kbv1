import localeCopy from '../../content/fr.json';
const {demo:demoCopy}=localeCopy;
import type { Source } from './types';
export const sources: Source[] = [
  {
    id: 1,
    pdf: { url: '/documents/guide-marque.pdf', page: 8 },
    title: demoCopy.guideDeMarque,
    category: demoCopy.identiteDesign,
    pages: demoCopy.pages812,
    excerpt: demoCopy.laSimpliciteGuideChaqueDecisionNotreIdentite,
  },
  {
    id: 2,
    pdf: { url: '/documents/principes-produit.pdf', page: 3 },
    title: demoCopy.principesProduit,
    category: demoCopy.strategieProduit,
    pages: demoCopy.pages35,
    excerpt: demoCopy.concevoirPourLaClarteChaqueEcranDoit,
  },
  {
    id: 3,
    pdf: { url: '/documents/systeme-design.pdf', page: 16 },
    title: demoCopy.systemeDeDesign,
    category: demoCopy.documentationTechnique,
    pages: demoCopy.pages1621,
    excerpt: demoCopy.lesCouleursSontExprimeesSousFormeDe,
  },
];
export const initialAnswer = demoCopy.voiciLesTroisPrincipesQuiStructurentNotre;
export const comparisonQuestion = demoCopy.commentPersonnaliserLesCouleurs;
export const comparisonAnswerA = demoCopy.modifiezLesVariablesDansThemeCssAccent;
export const comparisonAnswerB = demoCopy.pourChangerLaCouleurPrincipale1Ouvrez;

export const comparisonExample = {
  id: 'catalog-demo-evaluation',
  prompt: demoCopy.commentPersonnaliserLaCouleurPrincipaleDeReno,
  responseA: demoCopy.modifiezLeTokenColorAccentDansTheme,
  responseB: demoCopy.modifiezManuellementLaCouleurDeChaqueBouton,
};
