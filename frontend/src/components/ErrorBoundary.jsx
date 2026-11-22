import React from 'react';

/**
 * Catches a render error, and a chunk that fails to arrive (internal design notes).
 *
 * `<Suspense>` covers the *pending* state of a lazy import, not the *rejected*
 * one. Only an error boundary catches a chunk that 404s -- which is what
 * happens when a bundle is built against the wrong asset base, and it presents
 * as a completely blank page with a console error nobody sees. This app lazily
 * imports the 3D viewer and `heic2any`, so it has two ways to hit that.
 *
 * Deliberately not a full-page takeover where it can be avoided: the caller
 * chooses the fallback, so a viewer that fails to load can leave the site's
 * description, its runs and their ARKs readable underneath.
 */
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Kept: this is the one place a chunk-load failure is observable at all.
    console.error('Unhandled error in the React tree', error, info);
    // Lets a caller show the failure somewhere the boundary itself cannot
    // reach -- the 3D viewer's fallback renders inside a <Canvas>, where a
    // DOM message is not a legal child, so its overlay listens here instead.
    this.props.onError?.(error);
  }

  handleRetry = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (typeof this.props.fallback === 'function') {
      return this.props.fallback(error, this.handleRetry);
    }
    // `!== undefined`, not truthiness: `fallback={null}` is a deliberate
    // "render nothing" and must not fall through to the default message.
    if (this.props.fallback !== undefined) return this.props.fallback;

    return (
      <div role="alert" className="error-boundary">
        <p>{this.props.message || 'Something went wrong loading this part of the page.'}</p>
        <button type="button" onClick={this.handleRetry}>
          {this.props.retryLabel || 'Try again'}
        </button>
      </div>
    );
  }
}

export default ErrorBoundary;
