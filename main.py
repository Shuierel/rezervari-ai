"""
Sistem de Rezervări Telefonice Bazat pe AI - Prototip
Flux: Ascultă → Transcrie (Twilio STT) → Procesează (GPT) → Repetă vocal
Suportă atât apeluri telefonice cât și apeluri din browser (Voice SDK)
"""

import os
import io
import uuid
import logging
from collections import deque
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from openai import AsyncOpenAI
import edge_tts
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sistem Rezervări AI", version="1.0.0")

openai_client = AsyncOpenAI()

LANGUAGE = "ro-RO"
BASE_URL = os.getenv("BASE_URL", "https://rezervari-ai.onrender.com")

ACCOUNT_SID    = os.getenv("TWILIO_ACCOUNT_SID")
API_KEY_SID    = os.getenv("TWILIO_API_KEY_SID")
API_KEY_SECRET = os.getenv("TWILIO_API_KEY_SECRET")
TWIML_APP_SID  = os.getenv("TWILIO_TWIML_APP_SID")

audio_cache: dict = {}
conversation_log: deque = deque(maxlen=50)

# Stochează ultimul mesaj AI per apel pentru funcția de repetare
call_state: dict = {}


def log_entry(tip: str, text: str, call_sid: str = ""):
    conversation_log.append({
        "timp": datetime.now().strftime("%H:%M:%S"),
        "tip": tip,
        "text": text,
        "call_sid": call_sid,
    })


async def tts(text: str) -> str:
    audio_id = str(uuid.uuid4())
    communicate = edge_tts.Communicate(text, voice="ro-RO-AlinaNeural")
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    audio_cache[audio_id] = buf.getvalue()
    return f"{BASE_URL}/audio/{audio_id}"


# ---------------------------------------------------------------------------
# AUDIO
# ---------------------------------------------------------------------------

@app.get("/audio/{audio_id}")
async def serve_audio(audio_id: str):
    data = audio_cache.get(audio_id)
    if not data:
        return Response(status_code=404)
    return Response(content=data, media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# TOKEN
# ---------------------------------------------------------------------------

@app.get("/token")
async def get_token():
    token = AccessToken(ACCOUNT_SID, API_KEY_SID, API_KEY_SECRET, identity="browser-user", ttl=3600)
    token.add_grant(VoiceGrant(outgoing_application_sid=TWIML_APP_SID, incoming_allow=True))
    jwt = token.to_jwt()
    if isinstance(jwt, bytes):
        jwt = jwt.decode("utf-8")
    return JSONResponse({"token": jwt})


# ---------------------------------------------------------------------------
# LOGS
# ---------------------------------------------------------------------------

@app.get("/logs")
async def get_logs():
    return JSONResponse(list(conversation_log))


# ---------------------------------------------------------------------------
# PAGINA DE TEST
# ---------------------------------------------------------------------------

@app.get("/test", response_class=HTMLResponse)
async def test_page():
    return """
<!DOCTYPE html>
<html lang="ro">
<head>
    <meta charset="UTF-8">
    <title>Test Sistem Rezervări AI</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 600px; margin: 60px auto; text-align: center; background: #f5f5f5; }
        h1 { color: #333; }
        button { padding: 20px 40px; font-size: 18px; border: none; border-radius: 10px; cursor: pointer; margin: 10px; }
        #btn-call { background: #4CAF50; color: white; }
        #btn-hangup { background: #f44336; color: white; display: none; }
        #status { margin-top: 20px; padding: 15px; background: white; border-radius: 8px; color: #555; min-height: 50px; }
        #jurnal { margin-top: 20px; text-align: left; }
        #jurnal h3 { text-align: center; color: #333; margin-bottom: 10px; }
        #log-list { list-style: none; padding: 0; margin: 0; }
        #log-list li { padding: 10px 14px; margin-bottom: 8px; border-radius: 8px; font-size: 14px; line-height: 1.5; }
        .tip-user { background: #e3f2fd; border-left: 4px solid #2196F3; }
        .tip-ai { background: #e8f5e9; border-left: 4px solid #4CAF50; }
        .tip-confirmare { background: #fff3e0; border-left: 4px solid #FF9800; }
        .timp { font-size: 11px; color: #999; margin-right: 8px; }
        .eticheta { font-weight: bold; margin-right: 6px; }
    </style>
</head>
<body>
    <h1>🎙️ Sistem Rezervări AI</h1>
    <p>Apasă butonul și vorbește cu AI-ul de rezervări</p>
    <button id="btn-call" onclick="startCall()">📞 Sună</button>
    <button id="btn-hangup" onclick="hangup()">📵 Închide</button>
    <div id="status">Se inițializează...</div>
    <div id="jurnal">
        <h3>📋 Jurnal conversație</h3>
        <ul id="log-list"></ul>
    </div>
    <script src="https://unpkg.com/@twilio/voice-sdk@2.11.0/dist/twilio.min.js"></script>
    <script>
        let device, activeCall, lastLogCount = 0;

        async function setup() {
            try {
                const res = await fetch('/token');
                const data = await res.json();
                device = new Twilio.Device(data.token, { codecPreferences: ['opus', 'pcmu'], logLevel: 'warn' });
                device.on('registered', () => setStatus('Gata — apasă Sună pentru a testa'));
                device.on('error', (err) => setStatus('Eroare: ' + err.message));
                await device.register();
            } catch(e) { setStatus('Eroare la inițializare: ' + e.message); }
        }

        async function startCall() {
            try {
                setStatus('Se conectează...');
                activeCall = await device.connect();
                activeCall.on('accept', () => {
                    setStatus('Apel conectat — vorbește cu AI-ul...');
                    document.getElementById('btn-call').style.display = 'none';
                    document.getElementById('btn-hangup').style.display = 'inline-block';
                });
                activeCall.on('disconnect', () => {
                    setStatus('Apel încheiat');
                    document.getElementById('btn-call').style.display = 'inline-block';
                    document.getElementById('btn-hangup').style.display = 'none';
                    activeCall = null;
                    lastLogCount = 0;
                    document.getElementById('log-list').innerHTML = '';
                });
                activeCall.on('error', (err) => setStatus('Eroare apel: ' + err.message));
            } catch(e) { setStatus('Eroare: ' + e.message); }
        }

        function hangup() { if (activeCall) activeCall.disconnect(); }
        function setStatus(msg) { document.getElementById('status').innerText = msg; }

        async function refreshLogs() {
            try {
                const res = await fetch('/logs');
                const entries = await res.json();
                if (entries.length === lastLogCount) return;
                lastLogCount = entries.length;
                const ul = document.getElementById('log-list');
                ul.innerHTML = '';
                entries.forEach(e => {
                    const li = document.createElement('li');
                    let cls = 'tip-user', label = '🎤 Tu';
                    if (e.tip === 'ai') { cls = 'tip-ai'; label = '🤖 AI'; }
                    if (e.tip === 'confirmare') { cls = 'tip-confirmare'; label = '✅ Confirmare'; }
                    li.className = cls;
                    li.innerHTML = '<span class="timp">' + e.timp + '</span><span class="eticheta">' + label + ':</span>' + e.text;
                    ul.appendChild(li);
                });
                ul.lastElementChild && ul.lastElementChild.scrollIntoView({behavior: 'smooth'});
            } catch(_) {}
        }

        setup();
        setInterval(refreshLogs, 2000);
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# PASUL 1: Răspundem la apel — curățăm jurnalul la fiecare apel nou
# ---------------------------------------------------------------------------

@app.post("/voice/inbound")
async def handle_inbound_call(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "necunoscut")
    logger.info(f"[{call_sid}] Apel primit")

    # Curățăm jurnalul și starea apelului anterior
    conversation_log.clear()
    call_state.pop(call_sid, None)

    url_salut   = await tts(
        "Bună ziua! Ați sunat la sistemul nostru de rezervări. "
        "Vă rog să îmi spuneți ce doriți să rezervați, "
        "incluzând data, ora și numărul de persoane."
    )
    url_timeout = await tts("Nu am detectat niciun răspuns. Vă rugăm să sunați din nou. La revedere!")

    response = VoiceResponse()
    # Play ÎNAINTE de Gather → robotul vorbește complet, fără barge-in
    response.play(url_salut)
    gather = Gather(input="speech", action="/voice/process", method="POST",
                    timeout=15, speech_timeout="3", language=LANGUAGE)
    response.append(gather)
    response.play(url_timeout)
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# PASUL 2: Procesăm speech-ul prin GPT
# ---------------------------------------------------------------------------

@app.post("/voice/process")
async def process_speech(request: Request):
    form = await request.form()
    speech_result = form.get("SpeechResult", "").strip()
    confidence    = float(form.get("Confidence", "0"))
    call_sid      = form.get("CallSid", "necunoscut")

    logger.info(f"[{call_sid}] Transcris: '{speech_result}' | Incredere: {confidence:.2f}")

    response = VoiceResponse()

    if not speech_result:
        url = await tts("Îmi pare rău, nu am reușit să vă înțeleg. Vă rog să repetați.")
        response.play(url)
        gather = Gather(input="speech", action="/voice/process", method="POST",
                        timeout=15, speech_timeout="3", language=LANGUAGE)
        response.append(gather)
        return Response(content=str(response), media_type="application/xml")

    log_entry("user", speech_result, call_sid)

    try:
        mesaj_confirmare = await extrage_detalii_rezervare(speech_result)
        logger.info(f"[{call_sid}] GPT: '{mesaj_confirmare}'")
        log_entry("ai", mesaj_confirmare, call_sid)
    except Exception as exc:
        logger.error(f"[{call_sid}] Eroare GPT: {exc}")
        url = await tts("Am întâmpinat o problemă tehnică. Vă rog încercați din nou.")
        response.play(url)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    url_confirmare = await tts(f"{mesaj_confirmare} Este corect? Spuneți DA sau NU. Dacă nu ați auzit bine, spuneți REPETĂ.")
    url_timeout    = await tts("Nu am primit un răspuns. Vă mulțumim! La revedere!")

    # Salvăm ultimul mesaj pentru repetare
    call_state[call_sid] = {"url": url_confirmare, "text": mesaj_confirmare}

    response.play(url_confirmare)
    gather = Gather(input="speech", action="/voice/confirm", method="POST",
                    timeout=15, speech_timeout="3", language=LANGUAGE)
    response.append(gather)
    response.play(url_timeout)
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# PASUL 3: Confirmarea DA / NU / REPETĂ
# ---------------------------------------------------------------------------

@app.post("/voice/confirm")
async def handle_confirmation(request: Request):
    form = await request.form()
    speech_result = form.get("SpeechResult", "").lower().strip()
    call_sid      = form.get("CallSid", "necunoscut")

    logger.info(f"[{call_sid}] Confirmare: '{speech_result}'")
    log_entry("confirmare", speech_result, call_sid)

    response = VoiceResponse()
    cuvinte     = set(speech_result.split())
    cuvinte_da  = {"da", "corect", "exact", "bine", "perfect", "confirmat"}
    cuvinte_nu  = {"nu", "gresit", "incorect", "negativ"}
    cuvinte_rep = {"repeta", "repetă", "repetati", "repetați", "auzit", "inteles", "înțeles", "spune", "nou"}

    if cuvinte & cuvinte_rep:
        # Repetăm ultimul mesaj AI
        last = call_state.get(call_sid)
        if last:
            response.play(last["url"])
        else:
            url = await tts("Îmi pare rău, nu am ce repeta.")
            response.play(url)
        gather = Gather(input="speech", action="/voice/confirm", method="POST",
                        timeout=15, speech_timeout="3", language=LANGUAGE)
        response.append(gather)
    elif cuvinte & cuvinte_da:
        logger.info(f"[{call_sid}] CONFIRMAT")
        url = await tts("Excelent! Rezervarea a fost confirmată. Vă mulțumim! La revedere!")
        response.play(url)
        response.hangup()
    elif cuvinte & cuvinte_nu:
        url = await tts("Înțeleg, mă scuz. Să reluăm.")
        response.play(url)
        response.redirect("/voice/inbound", method="POST")
    else:
        url = await tts("Vă mulțumim pentru apel. La revedere!")
        response.play(url)
        response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# Funcție auxiliară GPT
# ---------------------------------------------------------------------------

async def extrage_detalii_rezervare(text_utilizator: str) -> str:
    completion = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "Ești un asistent vocal de rezervări. "
                    "Din mesajul utilizatorului extrage: tipul rezervării, data, ora și numărul de persoane. "
                    "Formulează UN SINGUR mesaj de confirmare în română, natural și concis, cu diacritice corecte. "
                    "Răspunde EXCLUSIV cu mesajul de confirmare."
                ),
            },
            {"role": "user", "content": text_utilizator},
        ],
        max_tokens=120,
        temperature=0.2,
    )
    return completion.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    return {"status": "activ", "mesaj": "Sistemul de rezervari AI este online"}
