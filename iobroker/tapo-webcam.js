/**
 * ioBroker-Skript (JavaScript-Adapter ≥ 7) für den tapo-webcam-Dienst.
 *
 *  - legt Datenpunkte unter 0_userdata.0.tapoWebcam.<kamera> an
 *  - aktualisiert sie, wenn der Dienst im Intervall neue Bilder macht (Polling von /status)
 *  - nimmt bei einem Ereignis (Klingel, Bewegungsmelder …) sofort ein Bild auf
 *  - schickt es optional per Telegram
 *  - Datenpunkt "snapshot" auf true setzen = manuelle Aufnahme (z. B. Button in VIS)
 *
 * In VIS/VIS-2 als Bild-URL einfach  http://<dienst-ip>:8099/eingang/latest.jpg  verwenden
 * (weckt die Kamera nicht, zeigt das letzte Intervallbild).
 */

const fs = require('fs');
const path = require('path');

// ======================= Einstellungen =======================
const BASE_URL = 'http://192.168.1.20:8099';   // wo tapo-webcam läuft
const TOKEN    = '';                            // falls in config.toml gesetzt
const CAMERA   = 'eingang';                     // name aus config.toml
const TRIGGERS = [
    // Datenpunkte, bei denen ein Bild ausgelöst wird – anpassen!
    // { id: 'tapo.0.xxxxxxxx.ring', val: true, tag: 'klingel' },
    // { id: 'hm-rpc.0.XYZ.1.MOTION', val: true, tag: 'bewegung' },
];
const TELEGRAM_INSTANCE = '';                   // z. B. 'telegram.0' – leer = kein Versand
const TMP_DIR  = '/tmp/tapo-webcam';
// =============================================================

const ROOT = `0_userdata.0.tapoWebcam.${CAMERA}`;
const q = (extra = '') => {
    const p = [];
    if (TOKEN) p.push('token=' + encodeURIComponent(TOKEN));
    if (extra) p.push(extra);
    return p.length ? '?' + p.join('&') : '';
};

async function ensureStates() {
    const defs = {
        snapshot:     { type: 'boolean', role: 'button', read: false, write: true, def: false, name: 'Bild jetzt aufnehmen' },
        lastCapture:  { type: 'string',  role: 'date',   read: true,  write: false, def: '', name: 'Letzte Aufnahme' },
        lastEvent:    { type: 'string',  role: 'text',   read: true,  write: false, def: '', name: 'Letztes Ereignisbild (Datei)' },
        lastError:    { type: 'string',  role: 'text',   read: true,  write: false, def: '', name: 'Letzter Fehler' },
        latestUrl:    { type: 'string',  role: 'url',    read: true,  write: false, def: '', name: 'URL letztes Bild' },
        eventUrl:     { type: 'string',  role: 'url',    read: true,  write: false, def: '', name: 'URL letztes Ereignisbild' },
        online:       { type: 'boolean', role: 'indicator.reachable', read: true, write: false, def: false, name: 'Dienst erreichbar' },
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
        log(`tapo-webcam: Ereignisbild '${tag}' gespeichert (${file})`);
        if (TELEGRAM_INSTANCE) {
            sendTo(TELEGRAM_INSTANCE, { text: file, type: 'photo',
                caption: `${CAMERA}: ${tag} – ${formatDate(new Date(), 'hh:mm:ss')}` });
        }
    } catch (e) {
        setState(`${ROOT}.lastError`, String(e), true);
        log(`tapo-webcam: Aufnahme fehlgeschlagen: ${e}`, 'warn');
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
        captureEvent('manuell');
    });
    schedule('*/1 * * * *', pollStatus);
    pollStatus();
})();
