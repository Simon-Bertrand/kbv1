/**
 * Validation conditionnelle des variables d'environnement selon KB_ENV ('dev' ou 'production').
 */

import { log } from './logger';
import { fileserverPublicBase } from './source-files/public-base';

export interface EnvValidationResult {
  isProd: boolean;
  kbEnv: string;
  warnings: string[];
  errors: string[];
}

export function validateStartupEnvironment(env: Record<string, string | undefined> = process.env): EnvValidationResult {
  const warnings: string[] = [];
  const errors: string[] = [];

  const rawEnv = (env.KB_ENV || (env.NODE_ENV === 'production' ? 'production' : 'dev')).trim().toLowerCase();
  if (!['dev', 'development', 'production', 'prod'].includes(rawEnv)) {
    throw new Error(`KB_ENV invalide : '${rawEnv}'. Valeurs attendues : 'dev' ou 'production'.`);
  }
  const isProd = ['production', 'prod'].includes(rawEnv);

  if (!env.KB_ENV) {
    warnings.push(`[STARTUP WARNING] KB_ENV non défini, mode '${rawEnv}' déduit.`);
  }

  // 1. WEBUI_AUTH_SECRET
  const secret = env.WEBUI_AUTH_SECRET;
  if (!secret || secret.length < 32) {
    if (isProd) {
      errors.push("[FATAL ERROR] [PROD MODE] WEBUI_AUTH_SECRET est obligatoire et doit comporter au moins 32 caractères en production.");
    } else {
      warnings.push("[SECURITY WARNING] [DEV MODE] WEBUI_AUTH_SECRET non configuré ou < 32 caractères. Le chat et la recherche nécessitent un secret valide.");
    }
  }

  // 2. WEBUI_AGENT_URL
  if (!env.WEBUI_AGENT_URL) {
    warnings.push("[STARTUP WARNING] WEBUI_AGENT_URL non configuré. L’onglet Agent sera indisponible.");
  }

  // 3. WEBUI_KM_API_URL
  if (!env.WEBUI_KM_API_URL) {
    if (isProd) {
      errors.push("[FATAL ERROR] [PROD MODE] WEBUI_KM_API_URL est obligatoire en production.");
    } else {
      warnings.push("[STARTUP WARNING] [DEV MODE] WEBUI_KM_API_URL non configuré, repli sur 'http://km-api:8000'.");
    }
  }

  // 4. Fournisseurs d'authentification
  const hasAdmin = Boolean(env.WEBUI_AUTH_ADMIN_LOGIN && env.WEBUI_AUTH_ADMIN_PASSWORD);
  const hasOidc = Boolean(env.WEBUI_AUTH_OIDC_ISSUER && env.WEBUI_AUTH_OIDC_ID && env.WEBUI_AUTH_OIDC_SECRET);
  if (!hasAdmin && !hasOidc) {
    if (isProd) {
      errors.push("[FATAL ERROR] [PROD MODE] Aucun moyen d'authentification configuré. Renseigner l'administrateur local ou OIDC.");
    } else {
      warnings.push("[STARTUP WARNING] [DEV MODE] Aucun compte admin ni OIDC configuré. La connexion sera impossible.");
    }
  }

  if (env.WEBUI_AUTH_OIDC_ISSUER) {
    try {
      const url = new URL(env.WEBUI_AUTH_OIDC_ISSUER);
      if (isProd && url.protocol !== 'https:') {
        errors.push("[FATAL ERROR] [PROD MODE] WEBUI_AUTH_OIDC_ISSUER doit utiliser HTTPS en production.");
      }
    } catch {
      errors.push("[FATAL ERROR] WEBUI_AUTH_OIDC_ISSUER doit être une URL HTTP(S) valide.");
    }
  }

  // 5. WEBUI_AUTH_URL
  if (!env.WEBUI_AUTH_URL) {
    if (isProd) {
      errors.push("[FATAL ERROR] [PROD MODE] WEBUI_AUTH_URL est obligatoire en production (ne peut être omis).");
    } else {
      warnings.push("[STARTUP WARNING] [DEV MODE] WEBUI_AUTH_URL non configuré, repli sur 'http://localhost:4173'.");
    }
  } else if (isProd) {
    if (env.WEBUI_AUTH_URL.includes('localhost')) {
      errors.push(`[FATAL ERROR] [PROD MODE] WEBUI_AUTH_URL ne doit pas pointer sur 'localhost' en production (${env.WEBUI_AUTH_URL}).`);
    }
    try {
      const url = new URL(env.WEBUI_AUTH_URL);
      if (url.protocol !== 'https:') {
        errors.push(`[FATAL ERROR] [PROD MODE] WEBUI_AUTH_URL doit utiliser HTTPS en production (${env.WEBUI_AUTH_URL}).`);
      }
    } catch {
      errors.push(`[FATAL ERROR] [PROD MODE] WEBUI_AUTH_URL doit être une URL valide (${env.WEBUI_AUTH_URL}).`);
    }
  }

  if (isProd) {
    if (!env.FILESERVER_JWT_SECRET || env.FILESERVER_JWT_SECRET.length < 32)
      errors.push('[FATAL ERROR] [PROD MODE] FILESERVER_JWT_SECRET doit contenir au moins 32 caractères.');
    if (!env.WEBUI_DATABASE_URL && !env.WEBUI_DB_HOST) errors.push('[FATAL ERROR] [PROD MODE] WEBUI_DATABASE_URL est obligatoire.');
    try {
      fileserverPublicBase(env.FILESERVER_PUBLIC_URL, true);
    } catch { errors.push('[FATAL ERROR] [PROD MODE] FILESERVER_PUBLIC_URL doit être une URL HTTPS publique.'); }
  }

  // 6. Logs & Actions
  for (const warn of warnings) {
    log('WARNING', warn, { logger: 'startup' });
  }

  if (errors.length > 0) {
    for (const err of errors) {
      log('ERROR', err, { logger: 'startup' });
    }
    throw new Error(`Échec de validation de l'environnement au démarrage :\n${errors.join('\n')}`);
  }

  return { isProd, kbEnv: rawEnv, warnings, errors };
}
