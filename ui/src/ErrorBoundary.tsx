import { Component, type ErrorInfo, type ReactNode } from 'react';
import { send } from './socket';

interface State { error: Error | null }

/** Catches a rendering error anywhere in the HUD. Without this, one bad render leaves an empty dark window. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('HUD render error', error, info.componentStack);
    // Tell the core, so the crash appears in the Repair view after a reload.
    send({ type: 'client.error', message: String(error.message || error).slice(0, 600), stack: `${error.stack ?? ''}\n${info.componentStack ?? ''}`.slice(0, 4000) });
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash" role="alert">
        <h1>The display hit an error</h1>
        <p>Jarvis itself is still running in the background. Reloading the display usually fixes it.</p>
        <pre>{String(this.state.error.message || this.state.error).slice(0, 400)}</pre>
        <button className="btn btn--primary" onClick={() => window.location.reload()}>Reload display</button>
      </div>
    );
  }
}
