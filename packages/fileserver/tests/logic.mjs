// Logic-only tests (Node runs as a surrogate for the njs language subset).
// Do not mistake these for an actual NGINX/mod_zip integration test.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const codeFile = new URL('../njs/explorer.js', import.meta.url);
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'staticzip-test-'));
const store = path.join(temp, 'volume');
fs.mkdirSync(path.join(store, 'docs', 'sub'), { recursive: true });
fs.mkdirSync(path.join(store, 'docs', 'note.pdfparsecache-hidden'), { recursive: true });
fs.mkdirSync(path.join(store, 'docs', '.git'), { recursive: true });
fs.mkdirSync(path.join(store, 'docs', 'empty'));
fs.writeFileSync(path.join(store, 'docs', 'readme.pdf'), Buffer.from('hello staticzip'));
fs.writeFileSync(path.join(store, 'docs', 'sub', 'utf é #?.txt'), Buffer.from('bonjour'));
fs.writeFileSync(path.join(store, 'docs', 'note.pdfparsecache-hidden', 'private.txt'), 'secret');
fs.writeFileSync(path.join(store, 'docs', '.git', 'config'), 'secret');
fs.writeFileSync(path.join(store, 'docs', '.env'), 'secret');
fs.writeFileSync(path.join(store, 'docs', 'file.pdfparsecache.json'), 'secret');
fs.writeFileSync(path.join(store, 'docs', 'odd\\name.txt'), 'skip');
fs.symlinkSync('/etc/passwd', path.join(store, 'docs', 'evil-link'));

function entries(dir) {
    return fs.readdirSync(dir, { withFileTypes: true }).map(e => {
        const st = fs.lstatSync(path.join(dir, e.name));
        return { name: e.name, type: st.isDirectory() ? 'directory' : 'file', size: st.size, mtime: new Date(st.mtimeMs).toISOString() };
    });
}
function request(pathArg) {
    const r = {
        args: pathArg === undefined ? {} : { path: pathArg },
        headersOut: {}, status: undefined, body: undefined,
        return(status, body) { this.status = status; this.body = body; },
        warn() { }, error() { },
        async subrequest(uri) {
            assert.ok(uri.startsWith('/_index/'));
            const rel = uri.slice('/_index/'.length);
            const parts = rel.split('/').filter(Boolean).map(decodeURIComponent);
            return { status: 200, responseText: JSON.stringify(entries(path.join(store, ...parts))) };
        }
    };
    return r;
}
try {
    const code = fs.readFileSync(codeFile, 'utf8');
    const evalCode = code.replace("const ROOT = '/srv/data';", `const ROOT = ${JSON.stringify(store)};`);
    assert.notEqual(code, evalCode);
    const moduleFile = path.join(temp, 'logic.mjs');
    fs.writeFileSync(moduleFile, evalCode);
    const mod = (await import(pathToFileURL(moduleFile).href)).default;
    const previousHosts = process.env.ALLOWED_HOSTS;
    process.env.ALLOWED_HOSTS = 'files.example.org, *.internal';
    for (const host of ['files.example.org', 'gpu.internal'])
        assert.equal(mod.allowedHost({ variables: { host } }), '1');
    for (const host of ['internal', 'localhost', 'gpu.internal.example'])
        assert.equal(mod.allowedHost({ variables: { host } }), '0');
    delete process.env.ALLOWED_HOSTS;
    assert.equal(mod.allowedHost({ variables: { host: 'custom.example' } }), '1');
    if (previousHosts !== undefined) process.env.ALLOWED_HOSTS = previousHosts;

    const l = request('docs');
    await mod.list(l);
    assert.equal(l.status, 200);
    const listing = JSON.parse(l.body).entries.map(e => e.name);
    assert.ok(listing.includes('readme.pdf'));
    for (const name of ['.env', '.git', 'note.pdfparsecache-hidden', 'file.pdfparsecache.json', 'evil-link', 'odd\\name.txt']) {
        assert.ok(!listing.includes(name), `Leaked ${name}`);
    }

    const z = request('docs');
    mod.zip(z);
    assert.equal(z.status, 200);
    assert.equal(z.headersOut['X-Archive-Files'], 'zip');
    assert.ok(z.body.includes('0 0 @directory docs/empty/'));
    assert.ok(z.body.includes('- 15 /_zipfiles/docs/readme.pdf docs/readme.pdf'));
    assert.ok(z.body.includes('/_zipfiles/docs/sub/utf%20%C3%A9%20%23%3F.txt'));
    assert.ok(!z.body.includes('.pdfparsecache'));
    assert.ok(!z.body.includes('/.env'));
    assert.ok(!z.body.includes('evil-link'));
    assert.ok(!z.body.includes('odd\\name.txt'));

    for (const candidate of ['docs/../.git', 'docs/.git', 'docs/note.pdfparsecache-hidden',
        'docs\\sub', ['docs', 'docs'], 'docs//sub', 'docs/\nsecret']) {
        const rr = request(candidate);
        mod.zip(rr);
        assert.equal(rr.status, 404, JSON.stringify(candidate));
    }
    const l2 = request('docs/evil-link');
    await mod.list(l2);
    assert.equal(l2.status, 404);
    const missing = request('missing');
    mod.zip(missing);
    assert.equal(missing.status, 404);

    // Oversized archives must fail before mod_zip sees the header.
    const hugeDir = path.join(store, 'many');
    fs.mkdirSync(hugeDir);
    for (let i = 0; i < 5002; i++) fs.writeFileSync(path.join(hugeDir, `f${i}`), 'x');
    const exceeded = request('many');
    mod.zip(exceeded);
    assert.equal(exceeded.status, 413);
    assert.ok(!('X-Archive-Files' in exceeded.headersOut));

    console.log('PASS: listing/ZIP manifests, filtering, escaping, symlinks, traversal, quotas');

    // ─────────────────────────────────────────────────────────────────────────
    // Security & JWT tests
    // ─────────────────────────────────────────────────────────────────────────
    const jwtSecret = 'test-fileserver-jwt-secret';
    const uiSecret = 'test-fileserver-ui-secret';
    process.env.FILESERVER_JWT_SECRET = jwtSecret;
    process.env.FILESERVER_UI_SECRET = uiSecret;

    function makeJwt(payload, secret = jwtSecret) {
        const h = mod.toBase64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
        const p = mod.toBase64Url(JSON.stringify({iss: 'webui', aud: 'fileserver', iat: Math.floor(Date.now()/1000) - 120, ...payload}));
        const sig = mod.signHmacSha256(h + '.' + p, secret);
        return h + '.' + p + '.' + sig;
    }

    // 1. UI Cookie generation & verification
    const cookieHeader = mod.generateUiCookie();
    assert.ok(cookieHeader.startsWith('fs_ui_session='));
    assert.ok(cookieHeader.includes('SameSite=Strict'));
    assert.ok(cookieHeader.includes('HttpOnly'));

    const cookieValue = cookieHeader.split(';')[0].split('=')[1];
    const uiReq = {
        headersIn: { 'Cookie': `fs_ui_session=${cookieValue}` },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkUiAuth(uiReq), 'ok');
    // UI session bypasses JWT on files!
    assert.equal(mod.checkFileAuth(uiReq), 'ok');

    // UI session with cross-site fetch is rejected
    const crossSiteReq = {
        headersIn: {
            'Cookie': `fs_ui_session=${cookieValue}`,
            'Sec-Fetch-Site': 'cross-site'
        },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkUiAuth(crossSiteReq), 'denied');
    assert.equal(mod.checkFileAuth(crossSiteReq), 'forbidden_origin'); // cross-site is immediately rejected

    // Tampered UI cookie is rejected
    const tamperedCookieReq = {
        headersIn: { 'Cookie': `fs_ui_session=${cookieValue}corrupted` },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkUiAuth(tamperedCookieReq), 'denied');

    // 2. File JWT Authorization (External requests)
    const nowSec = Math.floor(Date.now() / 1000);
    const validToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        aud: 'fileserver',
        exp: nowSec + 1800
    });

    // Valid JWT with query param
    const fileReqValid = {
        args: { token: validToken },
        variables: {},
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqValid), 'ok');
    assert.equal(mod.getVerifiedUser(fileReqValid), 'user1');

    // Valid JWT with Authorization header
    const fileReqHeader = {
        headersIn: { 'Authorization': `Bearer ${validToken}` },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqHeader), 'ok');

    // Additional WebUI Protection: Cross-site requests rejected even with valid token
    const fileReqCrossSite = {
        args: { token: validToken },
        headersIn: { 'Sec-Fetch-Site': 'cross-site' },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqCrossSite), 'forbidden_origin');

    // Additional WebUI Protection: Malicious Origin rejected even with valid token
    process.env.ALLOWED_HOSTS = 'files.kb.company.com,files.*';
    const fileReqEvilOrigin = {
        args: { token: validToken },
        headersIn: { 'Origin': 'https://evil-attacker.com' },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqEvilOrigin), 'forbidden_origin');

    // Additional WebUI Protection: Legitimate WebUI Origin accepted
    const fileReqWebUiOrigin = {
        args: { token: validToken },
        headersIn: { 'Origin': 'https://kb.company.com' },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqWebUiOrigin), 'ok');

    // Additional WebUI Protection: Wrong JWT issuer rejected
    const wrongIssuerToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        iss: 'attacker_system',
        aud: 'fileserver',
        exp: nowSec + 1800
    });
    assert.equal(mod.checkFileAuth({ args: { token: wrongIssuerToken }, uri: '/files/docs/readme.pdf' }), 'tampered');

    // Additional WebUI Protection: Wrong JWT audience rejected
    const wrongAudienceToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        iss: 'webui',
        aud: 'rogue_service',
        exp: nowSec + 1800
    });
    assert.equal(mod.checkFileAuth({ args: { token: wrongAudienceToken }, uri: '/files/docs/readme.pdf' }), 'tampered');

    // Additional WebUI Protection: Valid JWT with iss='webui' and aud='fileserver' accepted
    const webuiIssuerToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        iss: 'webui',
        aud: 'fileserver',
        exp: nowSec + 1800
    });
    assert.equal(mod.checkFileAuth({ args: { token: webuiIssuerToken }, uri: '/files/docs/readme.pdf' }), 'ok');

    // External request cannot access /api/list or /api/zip even with valid file JWT
    assert.equal(mod.checkUiAuth(fileReqValid), 'denied');
    assert.equal(mod.checkUiAuth(fileReqHeader), 'denied');

    // Missing token
    const fileReqMissing = {
        args: {},
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqMissing), 'missing');

    // Expired token (valid signature, but expired)
    const expiredToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        exp: nowSec - 60 // 1 min ago
    });
    const fileReqExpired = {
        args: { token: expiredToken },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqExpired), 'expired');

    // Tampered token (invalid signature)
    const tamperedToken = validToken.slice(0, -5) + 'xxxxx';
    const fileReqTampered = {
        args: { token: tamperedToken },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqTampered), 'tampered');

    // Token signed with wrong secret
    const wrongSecretToken = makeJwt({
        sub: 'user1',
        path: 'docs/readme.pdf',
        exp: nowSec + 1800
    }, 'wrong-secret');
    const fileReqWrongSecret = {
        args: { token: wrongSecretToken },
        uri: '/files/docs/readme.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqWrongSecret), 'tampered');

    // Unauthorized path (token is for file A, but requesting file B)
    const fileReqWrongPath = {
        args: { token: validToken }, // valid for 'docs/readme.pdf'
        uri: '/files/docs/other.pdf'
    };
    assert.equal(mod.checkFileAuth(fileReqWrongPath), 'forbidden_path');

    for (const payload of [{iss: null}, {aud: null}, {iat: null}, {exp: 0}, {sub: ''}, {path: 'docs/'}, {path: '/docs/readme.pdf'}]) {
        const token = makeJwt({sub: 'u', path: 'docs/readme.pdf', exp: nowSec + 300, ...payload});
        assert.notEqual(mod.checkFileAuth({args: {token}, uri:'/files/docs/readme.pdf'}), 'ok');
    }
    process.env.WEBUI_PUBLIC_ORIGIN = 'https://chat.example';
    assert.equal(mod.allowedOrigin({headersIn:{Origin:'https://chat.example'}}), 'https://chat.example');
    assert.equal(mod.allowedOrigin({headersIn:{Origin:'https://evil.example'}}), '');
    assert.equal(mod.checkFileAuth({args:{token:validToken},headersIn:{Origin:'https://chat.example','Sec-Fetch-Site':'cross-site'},uri:'/files/docs/readme.pdf'}), 'ok');
    delete process.env.WEBUI_PUBLIC_ORIGIN;
    assert.equal(mod.checkBasicAuth({headersIn:{Authorization:'Basic '+Buffer.from('admin:admin').toString('base64')}}), false);
    assert.equal(mod.checkDevLogin({headersIn:{'User-Agent':'Wget'},variables:{remote_addr:'127.0.0.1'}}), 'challenge');

    // KB_ENV validation in production vs dev
    process.env.KB_ENV = 'production';
    delete process.env.FILESERVER_JWT_SECRET;
    assert.throws(() => mod.checkFileAuth(fileReqValid), /FATAL ERROR.*PROD MODE.*FILESERVER_JWT_SECRET/);

    process.env.FILESERVER_JWT_SECRET = 'valid-test-secret-at-least-32-chars-long';
    process.env.FILESERVER_UI_SECRET = 'valid-ui-secret-at-least-32-chars-long';
    // In production, UI is strictly disabled (no session bypass, no cookie, denied auth)
    assert.equal(mod.isUiRequest(uiReq), false);
    assert.equal(mod.checkUiAuth(uiReq), 'denied');
    assert.equal(mod.generateUiCookie({}), '');
    // And without UI bypass, file request with cookie but without JWT token is rejected:
    assert.equal(mod.checkFileAuth(uiReq), 'missing');

    process.env.ALLOWED_HOSTS = '*';
    assert.throws(() => mod.allowedHost({ variables: { host: 'files.example.org' } }), /FATAL ERROR.*PROD MODE.*ALLOWED_HOSTS/);
    process.env.ALLOWED_HOSTS = '*,fileserver';
    assert.throws(() => mod.allowedHost({ variables: { host: 'files.example.org' } }), /FATAL ERROR.*PROD MODE.*ALLOWED_HOSTS/);
    process.env.ALLOWED_HOSTS = 'fileserver,*';
    assert.throws(() => mod.allowedHost({ variables: { host: 'files.example.org' } }), /FATAL ERROR.*PROD MODE.*ALLOWED_HOSTS/);

    // Prefix wildcard pattern (e.g. files.* matching files.<webui_domain>)
    process.env.ALLOWED_HOSTS = 'files.*,*.internal';
    assert.equal(mod.allowedHost({ variables: { host: 'files.company.com' } }), '1');
    assert.equal(mod.allowedHost({ variables: { host: 'files.kb.company.com' } }), '1');
    assert.equal(mod.allowedHost({ variables: { host: 'app.company.com' } }), '0');
    assert.equal(mod.allowedHost({ variables: { host: 'service.internal' } }), '1');

    process.env.KB_ENV = 'dev';
    process.env.ALLOWED_HOSTS = '*';
    assert.equal(mod.allowedHost({ variables: { host: 'any.host.local' } }), '1');

    // ─────────────────────────────────────────────────────────────────────────
    // Dev Mode Basic Auth tests for Fileserver UI
    // ─────────────────────────────────────────────────────────────────────────
    delete process.env.FILESERVER_DEV_USER;
    delete process.env.FILESERVER_DEV_PASSWORD;

    const noAuthReq = { headersIn: {} };
    assert.equal(mod.checkDevLogin(noAuthReq), 'challenge');
    assert.equal(mod.generateUiCookie(noAuthReq), '');

    // Default dev:dev Basic Auth
    const devAuth = 'Basic ' + Buffer.from('dev:dev').toString('base64');
    const validBasicReq = { headersIn: { 'Authorization': devAuth } };
    assert.equal(mod.checkBasicAuth(validBasicReq), true);
    assert.equal(mod.isUiRequest(validBasicReq), true);
    assert.equal(mod.checkDevLogin(validBasicReq), 'ok');
    const devCookie = mod.generateUiCookie(validBasicReq);
    assert.ok(devCookie.startsWith('fs_ui_session='));

    // Bad password
    const badAuth = 'Basic ' + Buffer.from('dev:wrongpass').toString('base64');
    const badAuthReq = { headersIn: { 'Authorization': badAuth } };
    assert.equal(mod.checkBasicAuth(badAuthReq), false);
    assert.equal(mod.isUiRequest(badAuthReq), false);
    assert.equal(mod.checkDevLogin(badAuthReq), 'challenge');

    // Custom FILESERVER_DEV_USER and FILESERVER_DEV_PASSWORD
    process.env.FILESERVER_DEV_USER = 'alice';
    process.env.FILESERVER_DEV_PASSWORD = 'supersecretdevpassword123';
    const aliceAuth = 'Basic ' + Buffer.from('alice:supersecretdevpassword123').toString('base64');
    const aliceReq = { headersIn: { 'Authorization': aliceAuth } };
    assert.equal(mod.checkBasicAuth(aliceReq), true);
    assert.equal(mod.isUiRequest(aliceReq), true);
    assert.equal(mod.checkDevLogin(aliceReq), 'ok');

    // Previous dev:dev no longer valid when custom credentials configured
    assert.equal(mod.checkBasicAuth(validBasicReq), false);

    // In production mode, Basic Auth is completely disabled
    process.env.KB_ENV = 'production';
    assert.equal(mod.checkBasicAuth(aliceReq), false);
    assert.equal(mod.isUiRequest(aliceReq), false);
    assert.equal(mod.checkDevLogin(aliceReq), 'disabled');
    assert.equal(mod.generateUiCookie(aliceReq), '');

    console.log('PASS: JWT validation, expiration detection, UI session bypass, and cross-site protection');
} finally {
    fs.rmSync(temp, { recursive: true, force: true });
}
