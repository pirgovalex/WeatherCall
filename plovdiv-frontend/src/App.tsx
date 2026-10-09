import { Route, Router } from '@solidjs/router'
import type { JSX } from 'solid-js'
import AppShell from './components/layout/AppShell'
import Forecasts from './pages/Forecasts'
import History from './pages/History'
import Method from './pages/Method'
import NotFound from './pages/NotFound'
import Now from './pages/Now'

export function AppRoutes() {
  return (
    <>
      <Route path="/" component={Now} />
      <Route path="/history" component={History} />
      <Route path="/forecasts" component={Forecasts} />
      <Route path="/method" component={Method} />
      <Route path="*404" component={NotFound} />
    </>
  )
}

export default function App(props: { base?: string; children?: JSX.Element }) {
  return (
    <Router root={AppShell} base={props.base}>
      <AppRoutes />
    </Router>
  )
}
