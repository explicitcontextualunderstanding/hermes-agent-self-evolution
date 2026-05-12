import React from 'react';

function EvolutionHubPage() {
  const [status, setStatus] = React.useState(null);

  React.useEffect(function () {
    fetch('/api/plugins/evolution-hub/batch-health')
      .then(r => r.json())
      .then(setStatus)
      .catch(function () {});
  }, []);

  return React.createElement('div', {
    style: { padding: '24px', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif' }
  },
    React.createElement('h2', { style: { marginBottom: '16px' } }, 'Evolution Hub'),
    status ? React.createElement('pre', {
      style: { background: '#1e293b', padding: '16px', borderRadius: '8px', fontSize: '13px' }
    }, JSON.stringify(status, null, 2)) : React.createElement('p', null, 'Loading batch status...')
  );
}

// Register with the Hermes dashboard plugin system
(function () {
  if (typeof window.__HERMES_PLUGINS__ !== 'undefined' &&
      typeof window.__HERMES_PLUGINS__.register === 'function') {
    window.__HERMES_PLUGINS__.register('evolution-hub', EvolutionHubPage);
  }
})();
