import { BrowserRouter } from 'react-router'
import { createRoot } from 'react-dom/client'

import './index.scss'

import Routing from './router.tsx'

createRoot(document.getElementById('root')!).render(
  <BrowserRouter>
    <Routing />
  </BrowserRouter>,
)
