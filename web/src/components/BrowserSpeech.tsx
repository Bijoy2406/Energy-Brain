import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { ApiError } from '../api/client'
import { canRecord, serverTranscription } from './voiceSupport'
import type { SpeechSource } from './voiceSupport'
import { Mic } from 'lucide-react'
import { AiRecorder } from './ui/tweenly-ai-recorder'

// SpeechRecognition is still vendor-prefixed in some browsers and absent from lib.dom.
type Recognition = {
  lang: string
  interimResults: boolean
  onresult: ((event: { results: { 0: { transcript: string } }[] }) => void) | null
  onend: (() => void) | null
  onerror: ((event: { error?: string }) => void) | null
  onstart: (() => void) | null
  onspeechstart: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition
  webkitSpeechRecognition?: new () => Recognition
}


// The longest recording (seconds), the shortest worth uploading, and the loudest level (0..1, as measured by the
// recorder) below which a recording counts as silence and is not uploaded at all (Whisper invents text on silence).
const MAX_RECORDING_S = 30
const MIN_RECORDING_MS = 700
const SILENT_LEVEL = 0.05

function serverMessage(error: ApiError): string {
  if (error.kind === 'aborted') return ''
  if (error.kind === 'api') {
    const wait = error.retry_after_s ? Math.max(1, Math.ceil(error.retry_after_s)) : null
    switch (error.code) {
      case 'rate_limited':
        return `Too many voice requests right now.${wait ? ` Try again in ${wait} s.` : ' Try again shortly.'}`
      case 'busy':
        return 'Voice input is busy. Try again in a moment.'
      case 'transcription_unavailable':
        return 'Voice input is not set up on this server. Please type your question.'
      case 'audio_too_large':
        return 'The recording is too long. Please say it again, shorter.'
    }
  }
  return 'Could not turn the recording into text. Try again or type your question.'
}

function recorderMessage(kind: 'too-short' | 'silent'): string {
  if (kind === 'too-short') return 'Too short. Hold on a little longer and speak.'
  return 'No speech heard. Check your microphone and try again.'
}

/** The Tweenly recorder (record, pause, discard, play back) in front of POST /transcribe. While it is open it
 *  covers the input row; the text only fills the input box and is never sent. */
function ServerMicrophone({ disabled, onTranscript, onUse, onMessage }: {
  disabled: boolean; onTranscript: (text: string) => void
  onUse: (source: SpeechSource) => void; onMessage?: (message: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const [generation, setGeneration] = useState(0)
  const request = useRef<AbortController | null>(null)

  // A new key restarts the recorder from idle (and releases the microphone if it was still recording).
  const reset = () => { setOpen(false); setTranscribing(false); setGeneration((value) => value + 1) }
  useEffect(() => () => { request.current?.abort() }, [])
  useEffect(() => {
    if (!disabled) return
    request.current?.abort()
    request.current = null
    reset()
  }, [disabled])

  async function recorded(audio: Blob, durationMs: number, peakLevel: number) {
    if (durationMs < MIN_RECORDING_MS) { reset(); onMessage?.(recorderMessage('too-short')); return }
    if (peakLevel < SILENT_LEVEL) { reset(); onMessage?.(recorderMessage('silent')); return }
    const controller = new AbortController()
    request.current?.abort()
    request.current = controller
    setTranscribing(true)
    onMessage?.('')
    const result = await api.transcribe(audio, 'en', controller.signal)
    if (controller.signal.aborted) return
    request.current = null
    reset()
    if (!result.ok) { onMessage?.(serverMessage(result.error)); return }
    if (result.data.no_speech || !result.data.text.trim()) { onMessage?.(recorderMessage('silent')); return }
    onTranscript(result.data.text.slice(0, 500))
  }

  return <div className="chat-voice" data-active={open || transcribing}>
    {transcribing
      ? <span className="chat-voice-status" role="status"><span className="explainer-spinner" aria-hidden="true" />Turning your voice into text…</span>
      : <AiRecorder key={generation} size={44} maxDuration={MAX_RECORDING_S} accent="var(--danger)"
        onPhaseChange={(phase) => {
          setOpen(phase !== 'idle')
          if (phase === 'requesting' || phase === 'recording') { onUse('server'); onMessage?.('') }
        }}
        onRecordingComplete={(audio, ms, peak) => { void recorded(audio, ms, peak) }} />}
  </div>
}

// What the person sees for each browser error code; silent failures looked like a dead button.
function micMessage(error: string | undefined): string {
  switch (error) {
    case 'not-allowed':
    case 'service-not-allowed':
      return 'Microphone access is blocked. Allow the microphone for this site in the address bar or browser settings. Some browsers (for example Brave) also block the speech service.'
    case 'network':
      return 'Could not reach the browser’s speech service. Brave and some other browsers block it; try Chrome or Edge, or type your question.'
    case 'no-speech':
      return 'No speech heard. Check your microphone and try again.'
    case 'audio-capture':
      return 'No microphone found.'
    case 'language-not-supported':
      return 'This browser does not recognise this language. Try English or type.'
    case 'aborted':
      return ''
    default:
      return 'Voice input did not work. Please type your question.'
  }
}

function BrowserMicrophone({ disabled, onTranscript, onUse, onMessage }: {
  disabled: boolean; onTranscript: (text: string) => void; onUse: (source: SpeechSource) => void
  onMessage?: (message: string) => void
}) {
  const [listening, setListening] = useState(false)
  const [failed, setFailed] = useState(false)
  const active = useRef<Recognition | null>(null)
  const Constructor = (window as SpeechWindow).SpeechRecognition ?? (window as SpeechWindow).webkitSpeechRecognition
  useEffect(() => {
    setListening(false)
    return () => {
      const recognition = active.current
      active.current = null
      if (recognition) {
        recognition.onresult = recognition.onend = recognition.onerror = null
        recognition.abort()
      }
    }
  }, [disabled])

  if (!Constructor || failed) return null

  function start() {
    if (active.current) { active.current.stop(); return }
    onUse('browser')
    onMessage?.('')
    try {
      const recognition = new Constructor!()
      active.current = recognition
      recognition.lang = 'en-US'
      recognition.interimResults = false
      recognition.onstart = () => { if (active.current === recognition) onMessage?.('Listening… speak now.') }
      let heard = false
      recognition.onresult = (event) => {
        if (active.current !== recognition) return
        heard = true
        onMessage?.('')
        onTranscript(event.results[0][0].transcript.slice(0, 500))
      }
      const end = () => {
        const mine = active.current === recognition
        active.current = null
        setListening(false)
        // A clean end with no result means the browser heard nothing usable.
        if (mine && !heard) onMessage?.(micMessage('no-speech'))
        else if (mine && heard) onMessage?.('')
      }
      recognition.onend = end
      recognition.onerror = (event) => {
        const mine = active.current === recognition
        active.current = null
        setListening(false)
        heard = true // the error message replaces the generic "no speech" one in onend
        if (mine) onMessage?.(micMessage(event?.error))
      }
      recognition.start()
      setListening(true)
    } catch {
      active.current = null
      setListening(false)
      setFailed(true)
      onMessage?.(micMessage(undefined))
    }
  }
  return <button type="button" className="chat-mic" data-phase={listening ? 'recording' : 'idle'} disabled={disabled} aria-pressed={listening && !disabled}
    aria-label={listening ? 'Stop microphone' : 'Start microphone'}
    onClick={start}><Mic aria-hidden="true" className="chat-mic-icon" /></button>
}

/** Voice input: server transcription when the server can do it and the browser can record, else the browser's
 *  own speech recognition, else nothing. The input box is only ever filled, never sent. */
export function Microphone({ disabled, onTranscript, onUse, onMessage }: {
  disabled: boolean; onTranscript: (text: string) => void
  onUse: (source: SpeechSource) => void; onMessage?: (message: string) => void
}) {
  const [server, setServer] = useState(false)
  useEffect(() => {
    if (!canRecord()) return
    let active = true
    void serverTranscription().then((available) => { if (active) setServer(available) })
    return () => { active = false }
  }, [])
  if (server && canRecord()) {
    return <ServerMicrophone disabled={disabled} onTranscript={onTranscript} onUse={onUse} onMessage={onMessage} />
  }
  return <BrowserMicrophone disabled={disabled} onTranscript={onTranscript} onUse={onUse} onMessage={onMessage} />
}

export function Listen({ text, onUse }: { text: string; onUse: (source: SpeechSource) => void }) {
  const synthesis = window.speechSynthesis
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => synthesis?.getVoices() ?? [])
  useEffect(() => {
    if (!synthesis) return
    const update = () => setVoices(synthesis.getVoices())
    synthesis.addEventListener('voiceschanged', update)
    return () => synthesis.removeEventListener('voiceschanged', update)
  }, [synthesis])
  const voice = voices.find((v) => v.lang.toLowerCase().replace('_', '-') === 'en-us')
    ?? voices.find((v) => v.lang.toLowerCase().startsWith('en'))
  if (!synthesis || !window.SpeechSynthesisUtterance || !voice) return null
  return <button type="button" className="chat-listen" onClick={() => {
    onUse('browser')
    synthesis.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = voice.lang
    utterance.voice = voice
    synthesis.speak(utterance)
  }}>Listen</button>
}
