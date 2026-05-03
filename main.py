"""
Sistem de Rezervări Telefonice Bazat pe AI - Prototip
Flux: Ascultă → Transcrie (Twilio STT) → Procesează (GPT) → Repetă vocal
Suportă atât apeluri telefonice cât și apeluri din browser (Voice SDK)
"""

import os
import logging
from fastapi import FastAPI, Request
from fastapi.responses import Response, JSONResponse, HTMLResponse, FileResponse
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sistem Rezervări AI", version="1.0.0")

openai_client = AsyncOpenAI()

VOICE = "Polly.Carmen"
LANGUAGE = "ro-RO"

ACCOUNT_SID     = os.getenv("TWILIO_ACCOUNT_SID")
API_KEY_SID     = os.getenv("TWILIO_API_KEY_SID")
API_KEY_SECRET  = os.getenv("TWILIO_API_KEY_SECRET")
TWIML_APP_SID   = os.getenv("TWILIO_TWIML_APP_SID")


# ---------------------------------------------------------------------------
# TOKEN — browserul îl folosește pentru a se conecta la Twilio Voice SDK
# ---------------------------------------------------------------------------

@app.get("/twilio-voice.js")
async def serve_sdk():
    return FileResponse("twilio-voice.js", media_type="application/javascript")


@app.get("/token")
async def get_token():
    token = AccessToken(
        ACCOUNT_SID,
        API_KEY_SID,
        API_KEY_SECRET,
        identity="browser-user",
        ttl=3600
    )
    voice_grant = VoiceGrant(
        outgoing_application_sid=TWIML_APP_SID,
        incoming_allow=True
    )
    token.add_grant(voice_grant)
    return JSONResponse({"token": token.to_jwt()})


# ---------------------------------------------------------------------------
# PAGINA DE TEST — interfață browser pentru a testa fără telefon
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
        body { font-family: Arial, sans-serif; max-width: 500px; margin: 100px auto; text-align: center; background: #f5f5f5; }
        h1 { color: #333; }
        button { padding: 20px 40px; font-size: 18px; border: none; border-radius: 10px; cursor: pointer; margin: 10px; }
        #btn-call { background: #4CAF50; color: white; }
        #btn-hangup { background: #f44336; color: white; display: none; }
        #status { margin-top: 20px; padding: 15px; background: white; border-radius: 8px; color: #555; min-height: 50px; }
    </style>
</head>
<body>
    <h1>🎙️ Sistem Rezervări AI</h1>
    <p>Apasă butonul și vorbește cu AI-ul de rezervări</p>

    <button id="btn-call" onclick="startCall()">📞 Sună</button>
    <button id="btn-hangup" onclick="hangup()">📵 Închide</button>

    <div id="status">Se inițializează...</div>

    <script src="/twilio-voice.js"></script>
    <script>
        let device;
        let activeCall;

        async function setup() {
            try {
                const res = await fetch('/token');
                const data = await res.json();

                device = new Twilio.Device(data.token, {
                    codecPreferences: ['opus', 'pcmu'],
                    logLevel: 'warn'
                });

                device.on('registered', () => setStatus('Gata — apasă Sună pentru a testa'));
                device.on('error', (err) => setStatus('Eroare: ' + err.message));

                await device.register();
            } catch(e) {
                setStatus('Eroare la inițializare: ' + e.message);
            }
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
                });
                activeCall.on('error', (err) => setStatus('Eroare apel: ' + err.message));
            } catch(e) {
                setStatus('Eroare: ' + e.message);
            }
        }

        function hangup() {
            if (activeCall) activeCall.disconnect();
        }

        function setStatus(msg) {
            document.getElementById('status').innerText = msg;
        }

        setup();
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# PASUL 1: Răspundem la apel
# ---------------------------------------------------------------------------

@app.post("/voice/inbound")
async def handle_inbound_call(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid", "necunoscut")
    logger.info(f"[{call_sid}] Apel primit")

    response = VoiceResponse()
    gather = Gather(
        input="speech",
        action="/voice/process",
        method="POST",
        timeout=10,
        speech_timeout="auto",
        language=LANGUAGE,
    )
    gather.say(
        "Buna ziua! Ati sunat la sistemul nostru de rezervari. "
        "Va rog sa imi spuneti ce doriti sa rezervati, "
        "incluzand data, ora si numarul de persoane.",
        voice=VOICE,
        language=LANGUAGE,
    )
    response.append(gather)
    response.say("Nu am detectat niciun raspuns. Va rugam sa sunati din nou. La revedere!", voice=VOICE, language=LANGUAGE)
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
        gather = Gather(input="speech", action="/voice/process", method="POST", timeout=10, speech_timeout="auto", language=LANGUAGE)
        gather.say("Imi pare rau, nu am reusit sa va inteleg. Va rog sa repetati.", voice=VOICE, language=LANGUAGE)
        response.append(gather)
        return Response(content=str(response), media_type="application/xml")

    try:
        mesaj_confirmare = await extrage_detalii_rezervare(speech_result)
        logger.info(f"[{call_sid}] GPT: '{mesaj_confirmare}'")
    except Exception as exc:
        logger.error(f"[{call_sid}] Eroare GPT: {exc}")
        response.say("Am intampinat o problema tehnica. Va rog incercati din nou.", voice=VOICE, language=LANGUAGE)
        response.hangup()
        return Response(content=str(response), media_type="application/xml")

    gather = Gather(input="speech", action="/voice/confirm", method="POST", timeout=10, speech_timeout="auto", language=LANGUAGE)
    gather.say(
        f"Doar ca sa imi testez sistemele: {mesaj_confirmare} Este corect? Spuneti DA sau NU.",
        voice=VOICE, language=LANGUAGE,
    )
    response.append(gather)
    response.say("Nu am primit un raspuns. Va multumim! La revedere!", voice=VOICE, language=LANGUAGE)
    response.hangup()

    return Response(content=str(response), media_type="application/xml")


# ---------------------------------------------------------------------------
# PASUL 3: Confirmarea DA / NU
# ---------------------------------------------------------------------------

@app.post("/voice/confirm")
async def handle_confirmation(request: Request):
    form = await request.form()
    speech_result = form.get("SpeechResult", "").lower().strip()
    call_sid      = form.get("CallSid", "necunoscut")

    logger.info(f"[{call_sid}] Confirmare: '{speech_result}'")

    response = VoiceResponse()
    cuvinte_da = {"da", "corect", "exact", "bine", "perfect", "confirmat"}
    cuvinte_nu = {"nu", "gresit", "incorect", "negativ"}
    cuvinte_din_raspuns = set(speech_result.split())

    if cuvinte_din_raspuns & cuvinte_da:
        logger.info(f"[{call_sid}] TEST REUSIT")
        response.say("Excelent! Sistemul functioneaza corect. Va multumim! La revedere!", voice=VOICE, language=LANGUAGE)
    elif cuvinte_din_raspuns & cuvinte_nu:
        response.say("Inteleg, ma scuz. Sa reluam.", voice=VOICE, language=LANGUAGE)
        response.redirect("/voice/inbound", method="POST")
    else:
        response.say("Va multumim pentru apel. La revedere!", voice=VOICE, language=LANGUAGE)

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
                    "Esti un asistent vocal de rezervari. "
                    "Din mesajul utilizatorului extrage: tipul rezervarii, data, ora si numarul de persoane. "
                    "Formuleaza UN SINGUR mesaj de confirmare in romana, natural si concis. "
                    "Raspunde EXCLUSIV cu mesajul de confirmare."
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
