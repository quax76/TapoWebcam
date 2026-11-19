/**
 * ioBroker script (JavaScript adapter ≥ 7) for the tapo-webcam service.
 *
 *  - creates states under 0_userdata.0.tapoWebcam.<camera>
 *  - updates them when the service takes new interval pictures (polling /status)
 *  - takes a picture immediately on an event (doorbell, motion sensor …)
 *  - optionally sends it via Telegram
 *  - set the state "snapshot" to true = manual capture (e.g. a button in VIS)
 *
 * In VIS/VIS-2 simply use  http://<service-ip>:8099/entrance/latest.jpg  as image URL
 * (does not wake the camera, shows the last interval picture).
 */

const fs = require('fs');
const path = require('path');

// ======================= Settings =======================
const BASE_URL = 'http://192.168.1.20:8099';   // where tapo-webcam runs
const TOKEN    = '';                            // if set in config.toml
const CAMERA   = 'entrance';                    // name from config.toml
const TRIGGERS = [
    // states that trigger a picture – adjust!
    // { id: 'tapo.0.xxxxxxxx.ring', val: true, tag: 'doorbell' },
    // { id: 'hm-rpc.0.XYZ.1.MOTION', val: true, tag: 'motion' },
];
const TELEGRAM_INSTANCE = '';                   // e.g. 'telegram.0' – empty = do not send
const TMP_DIR  = '/tmp/tapo-webcam';
// ========================================================

const ROOT = `0_userdata.0.tapoWebcam.${CAMERA}`;
const q = (extra = '') => {
    const p = [];
    if (TOKEN) p.push('token=' + encodeURIComponent(TOKEN));
    if (extra) p.push(extra);
    return p.length ? '?' + p.join('&') : '';
};

async function ensureStates() {
    const defs = {
        snapshot:     { type: 'boolean', role: 'button', read: false, write: true, def: false, name: 'Take picture now' },
        lastCapture:  { type: 'string',  role: 'date',   read: true,  write: false, def: '', name: 'Last capture' },
        lastEvent:    { type: 'string',  role: 'text',   read: true,  write: false, def: '', name: 'Last event picture (file)' },
        lastError:    { type: 'string',  role: 'text',   read: true,  write: false, def: '', name: 'Last error' },
        latestUrl:    { type: 'string',  role: 'url',    read: true,  write: false, def: '', name: 'URL of last picture' },
        eventUrl:     { type: 'string',  role: 'url',    read: true,  write: false, def: '', name: 'URL of last event picture' },
        online:       { type: 'boolean', role: 'indicator.reachable', read: true, write: false, def: false, name: 'Service reachable' },
    };
    for (const [k, c] of Object.entries(defs)) {
        if (!existsState(`${ROOT}.${k}`)) await createStateAsync(`${ROOT}.${k}`, c.def, c);
    }
    setState(`${ROOT}.latestUrl`, `${BASE_URL}/${CAMERA}/latest.jpg${q()}`, true);
}

function httpGetAsync(url, opts = {}) {
    return new Promise((resolve, reject) => {
        httpGet(url, { timeout: 45000, ...opts }, (err, res) => {
            if (err) return reject(err);
            if (res.statusCode >= 400) return reject(new Error(`HTTP ${res.statusCode}`));
            resolve(res);
        });
    });
}

async function captureEvent(tag) {
    try {
        const res = await httpGetAsync(`${BASE_URL}/${CAMERA}/trigger${q('format=jpeg&tag=' + encodeURIComponent(tag))}`,
                                       { responseType: 'arraybuffer' });
        fs.mkdirSync(TMP_DIR, { recursive: true });
        const file = path.join(TMP_DIR, `${CAMERA}_${tag}.jpg`);
        fs.writeFileSync(file, Buffer.from(res.data));
        setState(`${ROOT}.lastEvent`, file, true);
        setState(`${ROOT}.eventUrl`, `${BASE_URL}/${CAMERA}/event/${encodeURIComponent(tag)}.jpg${q('t=' + Date.now())}`, true);
        setState(`${ROOT}.lastCapture`, new Date().toISOString(), true);
        setState(`${ROOT}.lastError`, '', true);
        log(`tapo-webcam: event picture '${tag}' saved (${file})`);
        if (TELEGRAM_INSTANCE) {
            sendTo(TELEGRAM_INSTANCE, { text: file, type: 'photo',
                caption: `${CAMERA}: ${tag} – ${formatDate(new Date(), 'hh:mm:ss')}` });
        }
    } catch (e) {
        setState(`${ROOT}.lastError`, String(e), true);
        log(`tapo-webcam: capture failed: ${e}`, 'warn');
    }
}

async function pollStatus() {
    try {
        const res = await httpGetAsync(`${BASE_URL}/status${q()}`);
        const st = JSON.parse(res.data)[CAMERA] || {};
        setState(`${ROOT}.online`, true, true);
        if (st.last_capture && st.last_capture !== getState(`${ROOT}.lastCapture`).val) {
            setState(`${ROOT}.lastCapture`, st.last_capture, true);
        }
        setState(`${ROOT}.lastError`, st.last_error || '', true);
    } catch (e) {
        setState(`${ROOT}.online`, false, true);
    }
}

(async () => {
    await ensureStates();
    for (const t of TRIGGERS) {
        on({ id: t.id, change: 'ne', val: t.val }, () => captureEvent(t.tag));
    }
    on({ id: `${ROOT}.snapshot`, val: true, ack: false }, () => {
        setState(`${ROOT}.snapshot`, false, true);
        captureEvent('manual');
    });
    schedule('*/1 * * * *', pollStatus);
    pollStatus();
})();
