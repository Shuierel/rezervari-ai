"""
Sistem de Rezervări Telefonice Bazat pe AI
Flux: Ascultă → Transcrie → GPT gestionează conversația → Răspunde vocal
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Sistem Rezervări AI", version="2.0.0")
openai_client = AsyncOpenAI()

LANGUAGE = "ro-RO"
BASE_URL  = os.getenv("BASE_URL", "https://rezervari-ai.onrender.com")

ACCOUNT_SID    = os.getenv("TWILIO_ACCOUNT_SID")
API_KEY_SID    = os.getenv("TWILIO_API_KEY_SID")
API_KEY_SECRET = os.getenv("TWILIO_API_KEY_SECRET")
TWIML_APP_SID  = os.getenv("TWILIO_TWIML_APP_SID")

audio_cache:    dict  = {}
conversations:  dict  = {}  # call_sid -> istoric mesaje GPT
conversation_log: deque = deque(maxlen=50)

SYSTEM_PROMPT = """Ești un asistent vocal de rezervări. Tu gestionezi întreaga conversație cu clientul.

Obiectivele tale:
1. Află tipul rezervării (masă, cameră, etc.), data, ora și numărul de persoane
2. Confirmă detaliile cu clientul
3. Dacă clientul confirmă, mulțumește-le și adaugă exact [INCHEIAT] la finalul mesajului
4. Dacă clientul nu a înțeles sau cere să repeți, repetă ultima ta replică
5. Dacă detaliile sunt greșite, cere-le să reformuleze

Reguli stricte:
- Răspunde EXCLUSIV în română, natural, maxim 2 propoziții
- Scrie DOAR ceea ce spui clientului, fără explicații sau comentarii
- Când conversația s-a terminat (confirmat sau renunțat), adaugă [INCHEIAT] la final"""

SALUT = (
    "Bună ziua! Ați sunat la sistemul nostru de rezervări. "
    "Vă rog să îmi spuneți ce doriți să rezervați, "
    "incluzând data, ora și numărul de persoane."
)


def log_entry(tip: str, text: str, call_sid: str = ""):
    conversation_log.append({
        "timp": datetime.now().strftime("%H:%M:%S"),
        "tip": tip, "text": text, "call_sid": call_sid,
    })


async def tts(text: str) -> str:
    audio_id  = str(uuid.uuid4())
    communicate = edge_tts.Communicate(text, voice="ro-RO-AlinaNeural")
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    audio_cache[audio_id] = buf.getvalue()
    return f"{BASE_URL}/audio/{audio_id}"


async def raspuns_gpt(call_sid: str, mesaj_user: str) -> tuple[str, bool]:
    """Trimite mesajul la GPT și returnează (răspuns, conversație_încheiată)."""
    conversations[call_sid].append({"role": "user", "content": mesaj_user})

    completion = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": SYSTEM_PROMPT}] + conversations[call_sid],
        max_tokens=150,
        temperature=0.3,
    )

    raspuns_brut = completion.choices[0].message.content.strip()
    incheiat     = "[INCHEIAT]" in raspuns_brut
    raspuns      = raspuns_brut.replace("[INCHEIAT]", "").strip()

    conversations[call_sid].append({"role": "assistant", "content": raspuns})
    return raspuns, incheiat


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
# PASUL 1: Salut la apel
# ---------------------------------------------------------------------------

@app.post("/voice/inbound")
async def handle_inbound_call(request: Request):
    form     = await request.form()
    call_sid = form.get("CallSid", "necunoscut")
    logger.info(f"[{call_sid}] Apel primit")

    conversation_log.clear()
    conversations[call_sid] = []

    url = await tts(SALUT)
    log_entry("ai", SALUT, call_sid)

    response = VoiceResponse()
    response.play(url)
    gather = Gather(input="speech", action="/voice/respond", method="POST",
                    timeout=8, speech_timeout="2", language=LANGUAGE)
    response.append(gather)
    url_timeout = await tts("Nu am detectat niciun răspuns. Vă rugăm să sunați din nou. La revedere!")
    response.play(url_timeout)
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# PASUL 2: GPT gestionează toată conversația
# ---------------------------------------------------------------------------

@app.post("/voice/respond")
async def handle_response(request: Request):
    form          = await request.form()
    speech_result = form.get("SpeechResult", "").strip()
    call_sid      = form.get("CallSid", "necunoscut")

    logger.info(f"[{call_sid}] Utilizator: '{speech_result}'")

    response = VoiceResponse()

    if not speech_result:
        url = await tts("Îmi pare rău, nu am înțeles. Puteți repeta?")
        response.play(url)
        gather = Gather(input="speech", action="/voice/respond", method="POST",
                        timeout=8, speech_timeout="2", language=LANGUAGE)
        response.append(gather)
        return Response(content=str(response), media_type="application/xml")

    log_entry("user", speech_result, call_sid)

    try:
        raspuns, incheiat = await raspuns_gpt(call_sid, speech_result)
        logger.info(f"[{call_sid}] GPT: '{raspuns}' | Încheiat: {incheiat}")
        log_entry("ai", raspuns, call_sid)
    except Exception as exc:
        logger.error(f"[{call_sid}] Eroare GPT: {exc}")
        url = await tts("Am întâmpinat o problemă tehnică. Vă rog încercați din nou.")
        response.play(url)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    url = await tts(raspuns)
    response.play(url)

    if incheiat:
        response.hangup()
    else:
        gather = Gather(input="speech", action="/voice/respond", method="POST",
                        timeout=8, speech_timeout="2", language=LANGUAGE)
        response.append(gather)
        url_timeout = await tts("Nu am primit răspuns. La revedere!")
        response.play(url_timeout)
        response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/")
async def health_check():
    return {"status": "activ", "mesaj": "Sistemul de rezervari AI este online"}
