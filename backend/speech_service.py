import os
import io
import base64
from typing import List, Optional
import requests
from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends, Request
from pydantic import BaseModel

# Try to import auth helper
try:
    from backend.auth import get_current_customer
except ImportError:
    from auth import get_current_customer

router = APIRouter(prefix="/api/speech", tags=["speech"])

AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY") or os.getenv("AZURE_SPEECH_API_KEY")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "eastus")
# For your JarvisTest1 resource - check your region
# For Translatorjarvis it's koreacentral, but Speech might be eastus or centralindia

# ============================================================
# MODELS
# ============================================================
class SynthesizeIn(BaseModel):
    text: str
    voice: Optional[str] = "en-US-JennyNeural"
    language: Optional[str] = None

class TranscribeResponse(BaseModel):
    text: str
    detected_language: Optional[str] = None
    confidence: Optional[float] = None

# ============================================================
# AUTO-DETECT LANGUAGE LIST (Azure supported)
# ============================================================
AUTO_DETECT_LANGUAGES = [
    "en-US", "hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN",
    "kn-IN", "ml-IN", "pa-IN", "ur-IN",
    "es-ES", "fr-FR", "de-DE", "ar-SA", "zh-CN", "ja-JP", "ko-KR",
    "pt-BR", "ru-RU", "it-IT"
]

@router.get("/languages")
def get_supported_languages():
    """Return list for frontend auto-detect"""
    return {"auto_detect": AUTO_DETECT_LANGUAGES, "default": "en-US"}

@router.get("/token")
def get_speech_token(request: Request):
    """Issue short-lived token for browser SDK - with auto-detect config"""
    # Optional auth check - remove if you want public token
    # get_current_customer(request)
    if not AZURE_SPEECH_KEY:
        raise HTTPException(status_code=500, detail="AZURE_SPEECH_KEY not set in .env")
    
    # Get token from Azure
    url = f"https://{AZURE_SPEECH_REGION}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        resp.raise_for_status()
        token = resp.text
        return {
            "token": token,
            "region": AZURE_SPEECH_REGION,
            "auto_detect_languages": AUTO_DETECT_LANGUAGES
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get speech token: {str(e)}")

@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    # Optional: let client hint but we auto-detect anyway
    language: Optional[str] = Form(None)
):
    """
    Transcribe with AUTO-DETECT language.
    Accepts wav/webm/ogg, auto-detects language from AUTO_DETECT_LANGUAGES
    """
    get_current_customer(request)
    if not AZURE_SPEECH_KEY:
        raise HTTPException(status_code=500, detail="AZURE_SPEECH_KEY not set")

    audio_bytes = await file.read()
    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty audio file")

    # Azure STT REST API with auto language detection
    # Use v3.2 with languageIdentification
    try:
        import azure.cognitiveservices.speech as speechsdk
        
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        # AUTO DETECT CONFIG
        auto_detect_config = speechsdk.languageconfig.AutoDetectSourceLanguageConfig(languages=AUTO_DETECT_LANGUAGES)
        
        # Push stream from uploaded bytes
        push_stream = speechsdk.audio.PushAudioInputStream()
        push_stream.write(audio_bytes)
        push_stream.close()
        audio_config = speechsdk.audio.AudioConfig(stream=push_stream)

        speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            auto_detect_source_language_config=auto_detect_config,
            audio_config=audio_config
        )

        result = speech_recognizer.recognize_once()

        if result.reason == speechsdk.ResultReason.RecognizedSpeech:
            detected = None
            try:
                detected = result.properties.get(speechsdk.PropertyId.SpeechServiceConnection_AutoDetectSourceLanguageResult)
            except:
                pass
            return TranscribeResponse(text=result.text, detected_language=detected or "auto", confidence=1.0)
        elif result.reason == speechsdk.ResultReason.NoMatch:
            raise HTTPException(status_code=400, detail="No speech recognized")
        else:
            raise HTTPException(status_code=500, detail=f"STT failed: {result.reason}")

    except Exception:
        # Fallback REST without SDK or on SDK error
        url = f"https://{AZURE_SPEECH_REGION}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1?language=en-US"
        is_wav = bool(file.filename and file.filename.endswith(".wav"))
        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "audio/wav" if is_wav else "audio/webm",
            "Accept": "application/json"
        }
        resp = requests.post(url, headers=headers, data=audio_bytes, timeout=15)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=resp.text)
        j = resp.json()
        return TranscribeResponse(text=j.get("DisplayText",""), detected_language="en-US")

@router.post("/synthesize")
def synthesize_speech(payload: SynthesizeIn, request: Request):
    """Server-side TTS fallback - supports any voice"""
    get_current_customer(request)
    if not AZURE_SPEECH_KEY:
        raise HTTPException(status_code=500, detail="AZURE_SPEECH_KEY not set")
    
    try:
        import azure.cognitiveservices.speech as speechsdk
        speech_config = speechsdk.SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
        speech_config.speech_synthesis_voice_name = payload.voice or "en-US-JennyNeural"
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result = synthesizer.speak_text_async(payload.text).get()
        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            from fastapi.responses import Response
            return Response(content=result.audio_data, media_type="audio/wav")
        else:
            raise HTTPException(status_code=500, detail="TTS synthesis failed")
    except Exception:
        # REST fallback
        url = f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm"
        }
        voice = payload.voice or "en-US-JennyNeural"
        ssml = f"<speak version='1.0' xml:lang='en-US'><voice name='{voice}'>{payload.text}</voice></speak>"
        resp = requests.post(url, headers=headers, data=ssml.encode('utf-8'), timeout=15)
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail=resp.text)
        from fastapi.responses import Response
        return Response(content=resp.content, media_type="audio/wav")