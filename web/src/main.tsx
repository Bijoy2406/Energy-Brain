import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { hideSplash } from './splash'

createRoot(document.getElementById('root')!).render(
  <StrictMode><App /></StrictMode>,
)
hideSplash()
