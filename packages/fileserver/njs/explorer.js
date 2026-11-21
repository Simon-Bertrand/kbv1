// Runs in NGINX njs (not in Node.js).
// Listings are sourced from NGINX native autoindex JSON.
// ZIP manifest generation uses bounded synchronous filesystem traversal;
// mod_zip streams file contents via NGINX internal subrequests.
import fs from 'fs';
import crypto from 'crypto';

const ROOT = '/srv/data';
const MAX_DEPTH = 32;
const MAX_ENTRIES = 5000;
const MAX_MANIFEST_BYTES = 2 * 1024 * 1024;

const DEFAULT_JWT_SECRET = 'knowledgebase-default-fileserver-jwt-secret';
const DEFAULT_UI_SECRET = 'knowledgebase-default-fileserver-ui-secret';

function isProduction() {
    const env = (process.env.KB_ENV || '').trim().toLowerCase();
    return env === 'production' || env === 'prod';
}

function jwtSecret() {
    const secret = process.env.FILESERVER_JWT_SECRET;
    if (isProduction() && (!secret || secret === DEFAULT_JWT_SECRET)) {
        throw new Error('[FATAL ERROR] [PROD MODE] FILESERVER_JWT_SECRET doit impérativement être configuré avec un secret fort en production.');
    }
    return secret || DEFAULT_JWT_SECRET;
}

function uiSecret() {
    const secret = process.env.FILESERVER_UI_SECRET;
    if (isProduction() && (!secret || secret === DEFAULT_UI_SECRET)) {
        throw new Error('[FATAL ERROR] [PROD MODE] FILESERVER_UI_SECRET doit impérativement être configuré avec un secret fort en production.');
    }
    return secret || DEFAULT_UI_SECRET;
}

function toBase64Url(buf) {
    if (typeof buf === 'string') buf = Buffer.from(buf, 'utf8');
    return buf.toString('base64').replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
}

function fromBase64Url(str) {
    let base64 = str.replace(/-/g, '+').replace(/_/g, '/');
    while (base64.length % 4) base64 += '=';
    return Buffer.from(base64, 'base64').toString('utf8');
}

function safeEqual(a, b) {
    if (typeof a !== 'string' || typeof b !== 'string') return false;
    const bufA = Buffer.from(a);
    const bufB = Buffer.from(b);
    if (bufA.length !== bufB.length) return false;
    // njs crypto does not implement Node's timingSafeEqual. Compare every byte
    // with no data-dependent early exit; lengths are public token metadata.
    let difference = 0;
    for (let i = 0; i < bufA.length; i++) difference |= bufA[i] ^ bufB[i];
    return difference === 0;
}

function signHmacSha256(data, secret) {
    return toBase64Url(crypto.createHmac('sha256', secret).update(data).digest());
}

function getCookie(r, name) {
    if (r.variables && r.variables['cookie_' + name]) {
        return r.variables['cookie_' + name];
    }
    const raw = (r.headersIn && (r.headersIn['Cookie'] || r.headersIn['cookie']));
    if (!raw || typeof raw !== 'string') return null;
    const parts = raw.split(';');
    for (let i = 0; i < parts.length; i++) {
        const pair = parts[i].trim().split('=');
        if (pair[0] === name) return decodeURIComponent(pair.slice(1).join('='));
    }
    return null;
}

function checkBasicAuth(r) {
    if (isProduction()) {
        return false;
    }
    const auth = r.headersIn && (r.headersIn['Authorization'] || r.headersIn['authorization']);
    if (!auth || typeof auth !== 'string' || !auth.startsWith('Basic ')) {
        return false;
    }
    const b64 = auth.slice(6).trim();
    let decoded = '';
    try {
        decoded = Buffer.from(b64, 'base64').toString('utf8');
    } catch (_) {
        return false;
    }
    const colon = decoded.indexOf(':');
    if (colon === -1) return false;
    const user = decoded.slice(0, colon);
    const pass = decoded.slice(colon + 1);

    const expectedUser = (process.env.FILESERVER_DEV_USER || 'dev').trim();
    const expectedPass = (process.env.FILESERVER_DEV_PASSWORD || 'dev').trim();

    const isPrimaryMatch = safeEqual(user, expectedUser) && safeEqual(pass, expectedPass);

    if (isPrimaryMatch) {
        if (r.variables) {
            r.variables.verified_jwt_user = user;
        }
        return true;
    }
    return false;
}

function checkDevLogin(r) {
    if (isProduction()) {
        return 'disabled';
    }
    if (isUiRequest(r)) {
        return 'ok';
    }
    return 'challenge';
}

function isUiRequest(r) {
    if (isProduction()) {
        return false;
    }
    const cookie = getCookie(r, 'fs_ui_session');
    if (cookie && typeof cookie === 'string') {
        const dot = cookie.indexOf('.');
        if (dot !== -1) {
            const ts = cookie.slice(0, dot);
            const sig = cookie.slice(dot + 1);
            if (/^\d+$/.test(ts)) {
                const now = Math.floor(Date.now() / 1000);
                const age = now - Number(ts);
                // Session valid for 8 hours, allow 60s clock skew
                if (age >= -60 && age <= 8 * 3600) {
                    const expected = crypto.createHmac('sha256', uiSecret()).update(ts).digest('hex');
                    if (safeEqual(sig, expected)) {
                        // Defense against cross-site exploitation (CSRF)
                        const fetchSite = r.headersIn && (r.headersIn['Sec-Fetch-Site'] || r.headersIn['sec-fetch-site']);
                        if (fetchSite && fetchSite !== 'same-origin' && fetchSite !== 'none') {
                            return false;
                        }
                        return true;
                    }
                }
            }
        }
    }

    // Direct Basic Auth header check (CLI tools, curl -u in dev)
    if (checkBasicAuth(r)) {
        return true;
    }

    return false;
}

function checkUiAuth(r) {
    return isUiRequest(r) ? 'ok' : 'denied';
}

function generateUiCookie(r) {
    if (isProduction()) {
        return '';
    }
    // Only emit cookie if request is authenticated (cookie or Basic Auth),
    // or if r is omitted (e.g. testing / direct generation helper)
    if (r && !isUiRequest(r)) {
        return '';
    }
    const now = Math.floor(Date.now() / 1000).toString();
    const sig = crypto.createHmac('sha256', uiSecret()).update(now).digest('hex');
    return 'fs_ui_session=' + now + '.' + sig + '; Path=/; SameSite=Strict; HttpOnly';
}

function parseToken(r) {
    let token = r.args && r.args.token;
    if (!token && r.headersIn) {
        const auth = r.headersIn['Authorization'] || r.headersIn['authorization'];
        if (auth && typeof auth === 'string' && auth.startsWith('Bearer ')) {
            token = auth.slice(7).trim();
        }
    }
    return typeof token === 'string' && token.length > 0 ? token : null;
}

function extractHost(urlStr) {
    if (!urlStr || typeof urlStr !== 'string') return '';
    try {
        const m = urlStr.match(/^(?:https?:\/\/)?([^/:]+)/i);
        return m ? m[1].toLowerCase() : '';
    } catch (_) {
        return '';
    }
}

function isOriginAllowed(urlStr) {
    if (!urlStr) return true;
    const configured = (process.env.WEBUI_PUBLIC_ORIGIN || '').replace(/\/$/, '');
    if (configured) return urlStr === configured;
    if (isProduction()) return false;
    const host = extractHost(urlStr);
    if (!host) return true;
    if (host === 'localhost' || host === '127.0.0.1' || host === '[::1]') return true;

    const patterns = (process.env.ALLOWED_HOSTS === undefined ? '*' : process.env.ALLOWED_HOSTS)
        .split(',').map(v => v.trim().toLowerCase()).filter(Boolean);

    for (let i = 0; i < patterns.length; i++) {
        const p = patterns[i];
        if (p === '*' || p === host) return true;
        if (p.startsWith('*.') && host.endsWith(p.slice(1))) return true;
        if (p.endsWith('.*') && host.startsWith(p.slice(0, -1))) return true;
        if (p.startsWith('files.')) {
            const webuiBase = p.slice(6);
            if (host === webuiBase || host.endsWith('.' + webuiBase)) return true;
        }
    }
    return false;
}

function checkFileAuth(r) {
    // 1. Legitimate UI session bypasses JWT
    if (isUiRequest(r)) {
        return 'ok';
    }

    // 2. Defense against cross-site requests / hotlinking / unauthorized origins
    const fetchSite = r.headersIn && (r.headersIn['Sec-Fetch-Site'] || r.headersIn['sec-fetch-site']);
    const suppliedOrigin = r.headersIn && (r.headersIn['Origin'] || r.headersIn['origin']);
    if (fetchSite && fetchSite === 'cross-site' && !(suppliedOrigin && isOriginAllowed(suppliedOrigin))) {
        return 'forbidden_origin';
    }

    const origin = r.headersIn && (r.headersIn['Origin'] || r.headersIn['origin']);
    if (origin && !isOriginAllowed(origin)) {
        return 'forbidden_origin';
    }

    const referer = r.headersIn && (r.headersIn['Referer'] || r.headersIn['referer']);
    const configuredOrigin = (process.env.WEBUI_PUBLIC_ORIGIN || '').replace(/\/$/, '');
    if (referer && !(configuredOrigin ? (referer === configuredOrigin || referer.startsWith(configuredOrigin+'/')) : isOriginAllowed(referer))) {
        return 'forbidden_origin';
    }

    // 3. External requests require a valid JWT
    const token = parseToken(r);
    if (!token) {
        return 'missing';
    }

    const parts = token.split('.');
    if (parts.length !== 3) {
        return 'malformed';
    }

    // 3. Verify HMAC signature first (timing-safe)
    const data = parts[0] + '.' + parts[1];
    const expectedSig = signHmacSha256(data, jwtSecret());
    if (!safeEqual(parts[2], expectedSig)) {
        return 'tampered';
    }

    // 4. Decode payload
    let payload;
    try {
        payload = JSON.parse(fromBase64Url(parts[1]));
        const header = JSON.parse(fromBase64Url(parts[0]));
        if (!header || header.alg !== 'HS256' || header.typ !== 'JWT') return 'malformed';
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
            return 'malformed';
        }
        if (payload.iss !== 'webui') {
            return 'tampered';
        }
        if (payload.aud !== 'fileserver') {
            return 'tampered';
        }
        if (typeof payload.sub !== 'string' || !payload.sub.trim() || payload.sub.length > 256 ||
            typeof payload.path !== 'string' || !payload.path || payload.path.length > 4096 ||
            /[\\\x00-\x1f\x7f]/.test(payload.path) ||
            payload.path.split('/').some(p => !p || p === '.' || p === '..' || deniedName(p)) ||
            typeof payload.exp !== 'number' || !isFinite(payload.exp) || Math.floor(payload.exp) !== payload.exp ||
            typeof payload.iat !== 'number' || !isFinite(payload.iat) || Math.floor(payload.iat) !== payload.iat) return 'malformed';
    } catch (_) {
        return 'malformed';
    }

    // 5. Verify path authorization
    let requestedPath = (r.uri || '').replace(/^\/files\//, '').replace(/^\/+/, '');
    // r.uri is already decoded/normalized by NGINX. Decoding twice changes
    // literal percent-containing filenames and can introduce path traversal.
    if (requestedPath !== payload.path) return 'forbidden_path';

    // 6. Check expiration AFTER signature verification
    const now = Math.floor(Date.now() / 1000);
    if (payload.exp <= now) {
        return 'expired';
    }
    if (payload.iat < 0 || payload.iat > now + 60 || payload.exp <= payload.iat || payload.exp - payload.iat > 3600) return 'malformed';

    if (r.variables) {
        r.variables.verified_jwt_user = (payload.sub && typeof payload.sub === 'string') ? payload.sub : 'anonymous';
        if (typeof payload.request_id === 'string' && /^[A-Za-z0-9._-]{1,128}$/.test(payload.request_id)) {
            r.variables.verified_jwt_request_id = payload.request_id;
        }
    }

    return 'ok';
}

function getVerifiedUser(r) {
    if (r.variables && r.variables.verified_jwt_user && r.variables.verified_jwt_user !== '') {
        return r.variables.verified_jwt_user;
    }
    return 'anonymous';
}

function getLogClientIp(r) {
    const peer = r.variables.remote_addr;
    const trusted = (process.env.FILESERVER_LOG_PROXY_IPS || '').split(',').map(ip => ip.trim());
    const original = r.headersIn['X-Real-IP'] || r.headersIn['x-real-ip'];
    if (trusted.includes(peer) && typeof original === 'string' && /^[a-fA-F0-9:.]{2,45}$/.test(original)) {
        return original;
    }
    return peer;
}

function getDurationMs(r) {
    return String(Math.round(Number(r.variables.request_time || 0) * 1000));
}

function allowedOrigin(r) {
    const origin = r.headersIn && (r.headersIn.Origin || r.headersIn.origin);
    return origin && isOriginAllowed(origin) ? origin : '';
}

function deniedName(name) {
    // Dotfiles/dotdirs plus ANY file/directory containing .pdfparsecache.
    return name.startsWith('.') || name.includes('.pdfparsecache');
}

function validName(name) {
    // Backslashes are legal on Unix but are ambiguous as ZIP path separators
    // on Windows. Exclude them; newlines/control characters break ZIP manifests.
    return name && name !== '_versions' && !deniedName(name) && !/[\\\x00-\x1f\x7f]/.test(name);
}

function parseParts(r) {
    let raw = r.args.path;
    if (raw === undefined) raw = '';
    // njs returns arrays for duplicate query arguments: reject them.
    if (typeof raw !== 'string' || raw.length > 2048) {
        throw new Error('Invalid path');
    }
    // URL component decoded by njs. Do not normalize away relative segments.
    raw = raw.replace(/^\/+|\/+$/g, '');
    const parts = raw ? raw.split('/') : [];
    if (parts.length > MAX_DEPTH) throw new Error('Path too deep');
    for (let i = 0; i < parts.length; i++) {
        if (!validName(parts[i]) || parts[i] === '.' || parts[i] === '..') {
            throw new Error('Invalid path component');
        }
    }
    return parts;
}

function diskPath(parts) {
    return ROOT + (parts.length ? '/' + parts.join('/') : '');
}

function quoteURI(parts) {
    // Encode every component independently, including %, #, ?, and spaces.
    return parts.map(encodeURIComponent).join('/');
}

function assertDirectory(parts) {
    let current = ROOT;
    const rootStat = fs.lstatSync(current);
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error('Missing root');
    for (let i = 0; i < parts.length; i++) {
        current += '/' + parts[i];
        const stat = fs.lstatSync(current);
        if (!stat.isDirectory() || stat.isSymbolicLink()) {
            throw new Error('Not a directory');
        }
    }
}

function respondError(r, status, message) {
    r.headersOut['Content-Type'] = 'application/json; charset=utf-8';
    r.headersOut['Cache-Control'] = 'no-store';
    r.return(status, JSON.stringify({ error: message }));
}

async function list(r) {
    let parts;
    try {
        parts = parseParts(r);
        assertDirectory(parts);
    } catch (_) {
        return respondError(r, 404, 'Directory not found');
    }
    try {
        const uri = '/_index/' + quoteURI(parts) + (parts.length ? '/' : '');
        const reply = await r.subrequest(uri);
        if (reply.status !== 200) {
            return respondError(r, 404, 'Directory not found');
        }
        const entries = JSON.parse(reply.responseText);
        if (!Array.isArray(entries)) throw new Error('Unexpected native listing');
        const visible = [];
        for (let i = 0; i < entries.length; i++) {
            const e = entries[i];
            if (!e || typeof e.name !== 'string' || !validName(e.name)) continue;
            // Resolve all visible entries with lstat, never follow symlinks.
            try {
                const st = fs.lstatSync(diskPath(parts.concat(e.name)));
                if (!st.isSymbolicLink() && (st.isFile() || st.isDirectory())) {
                    visible.push(e);
                }
            } catch (_) {
                // Entry removed between native autoindex and lstat.
            }
        }
        r.headersOut['Content-Type'] = 'application/json; charset=utf-8';
        r.headersOut['Cache-Control'] = 'no-store';
        r.return(200, JSON.stringify({ path: parts.join('/'), entries: visible }));
    } catch (e) {
        r.error('list error: ' + e.message);
        return respondError(r, 500, 'Index unavailable or too large');
    }
}

function zip(r) {
    let parts;
    try {
        parts = parseParts(r);
        assertDirectory(parts);
    } catch (_) {
        return respondError(r, 404, 'Directory not found');
    }

    const name = parts.length ? parts[parts.length - 1] : 'storage';
    // Include the selected folder as the archive's top-level directory.
    const prefix = name + '/';
    const lines = [];
    let count = 0;
    let bytes = 0;

    function add(line) {
        const length = Buffer.byteLength(line, 'utf8') + 1;
        if (count + 1 > MAX_ENTRIES || bytes + length > MAX_MANIFEST_BYTES) {
            throw new Error('ZIP manifest limits exceeded');
        }
        lines.push(line);
        count += 1;
        bytes += length;
    }

    try {
        add('0 0 @directory ' + prefix);
        const stack = [{ path: parts, archive: prefix, depth: 0 }];
        while (stack.length) {
            const node = stack.pop();
            if (node.depth > MAX_DEPTH) throw new Error('Directory depth exceeded');
            const entries = fs.readdirSync(diskPath(node.path), { withFileTypes: true });
            // Stable order is easier to inspect and test. No global walk sort.
            entries.sort((a, b) => a.name < b.name ? -1 : (a.name > b.name ? 1 : 0));
            for (let i = 0; i < entries.length; i++) {
                const e = entries[i];
                if (!validName(e.name) || e.isSymbolicLink()) continue;
                const child = node.path.concat(e.name);
                const st = fs.lstatSync(diskPath(child));
                if (st.isSymbolicLink()) continue;
                const archiveName = node.archive + e.name;
                if (st.isDirectory()) {
                    if (node.depth + 1 > MAX_DEPTH) throw new Error('Directory depth exceeded');
                    add('0 0 @directory ' + archiveName + '/');
                    stack.push({ path: child, archive: archiveName + '/', depth: node.depth + 1 });
                } else if (st.isFile()) {
                    if (!Number.isSafeInteger(st.size) || st.size < 0) {
                        throw new Error('Unsupported file size');
                    }
                    // '-' means unknown CRC32: ZIP is streamed but not resumable.
                    add('- ' + st.size + ' /_zipfiles/' + quoteURI(child) + ' ' + archiveName);
                }
            }
        }
    } catch (e) {
        r.warn('ZIP refused: ' + e.message);
        return respondError(r, 413, 'Folder too large, changing or unreadable');
    }

    r.headersOut['X-Archive-Files'] = 'zip';
    r.headersOut['Content-Disposition'] =
        'attachment; filename="archive.zip"; filename*=UTF-8\'\'' + encodeURIComponent(name + '.zip');
    r.headersOut['Content-Type'] = 'application/zip';
    r.headersOut['Cache-Control'] = 'no-store';
    r.return(200, lines.join('\n') + '\n');
}

function allowedHost(r) {
    const patterns = (process.env.ALLOWED_HOSTS === undefined ? '*' : process.env.ALLOWED_HOSTS)
        .split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
    if (isProduction()) {
        if (patterns.length === 0 || patterns.includes('*')) {
            throw new Error("[FATAL ERROR] [PROD MODE] ALLOWED_HOSTS doit être explicitement restreint en production (ne peut être '*' ou vide).");
        }
    }
    const host = (r.variables && r.variables.host ? r.variables.host : '').toLowerCase();
    return patterns.some(pattern => {
        if (pattern === '*' || pattern === host) return true;
        if (pattern.startsWith('*.') && host.endsWith(pattern.slice(1))) return true;
        if (pattern.endsWith('.*') && host.startsWith(pattern.slice(0, -1))) return true;
        return false;
    }) ? '1' : '0';
}

function legacyVersionsRoot() {
    return process.env.FILESERVER_LEGACY_VERSIONS_ROOT === '' ? '/srv/data/_versions' : '/srv/source-versions';
}

export default {
    legacyVersionsRoot,
    list,
    zip,
    allowedHost,
    checkFileAuth,
    checkUiAuth,
    checkDevLogin,
    checkBasicAuth,
    getVerifiedUser,
    getLogClientIp,
    getDurationMs,
    allowedOrigin,
    generateUiCookie,
    isUiRequest,
    toBase64Url,
    fromBase64Url,
    signHmacSha256,
};
