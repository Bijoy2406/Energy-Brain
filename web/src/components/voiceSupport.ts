import { api } from '../api/client'

export type SpeechSource = 'server' | 'browser'
export type LanguageHint = 'auto' | 'en'

export function canRecord(): boolean {
  return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

let transcriptionAvailable: Promise<boolean> | null = null
/** Whether the server can transcribe (it has a Groq key). Asked once per page; a failed check is retried later. */
export function serverTranscription(): Promise<boolean> {
  if (!transcriptionAvailable) {
    transcriptionAvailable = api.health().then((result) => {
      if (!result.ok) transcriptionAvailable = null
      return result.ok && result.data.transcription_available === true
    })
  }
  return transcriptionAvailable
}
/** For tests: forget the cached answer of the server check. */
export function resetTranscriptionCheck() { transcriptionAvailable = null }

