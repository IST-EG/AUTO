// Client JavaScript for Integra Outreach Control Center (IDS)

function getCookie(name) {
  const value = `; ${document.cookie}`;
  const parts = value.split(`; ${name}=`);
  if (parts.length === 2) return parts.pop().split(';').shift();
  return null;
}

function getCsrfToken() {
  return getCookie('outreach_csrf_token') || '';
}

async function apiFetch(url, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = options.headers || {};

  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    headers['X-CSRF-Token'] = getCsrfToken();
  }

  if (options.body && typeof options.body === 'object' && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }

  options.headers = headers;
  return fetch(url, options);
}

function showAlert(elementId, message, type = 'error') {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.className = `alert alert-${type}`;
  el.textContent = message;
  el.style.display = 'block';
}

function hideAlert(elementId) {
  const el = document.getElementById(elementId);
  if (el) el.style.display = 'none';
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.add('show');
  }
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.remove('show');
    // Clear errors inside modal
    const errs = modal.querySelectorAll('.alert-error, .alert-danger');
    errs.forEach(e => { e.style.display = 'none'; e.textContent = ''; });
  }
}

// Close modal on escape key or clicking backdrop
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-backdrop.show').forEach(m => m.classList.remove('show'));
  }
});

document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-backdrop')) {
    e.target.classList.remove('show');
  }
});

/* ========================================================================= */
/* DASHBOARD REAL-TIME TELEMETRY & REMOTE SUPERVISION                        */
/* ========================================================================= */

let sseEventSource = null;
let pollingTimer = null;

function setTelemetryStatus(mode) {
  const dot = document.getElementById('telemetry-dot');
  const text = document.getElementById('telemetry-text');
  if (!dot || !text) return;

  if (mode === 'live') {
    dot.className = 'pulse-dot pulse';
    text.textContent = 'Live Telemetry';
  } else if (mode === 'polling') {
    dot.className = 'pulse-dot amber';
    text.textContent = 'Polling (5s)';
  } else {
    dot.className = 'pulse-dot red';
    text.textContent = 'Offline';
  }
}

function updateDashboard(data) {
  if (!data) return;

  // KPI 1: System Health
  if (data.system_health) {
    const healthEl = document.getElementById('kpi-system-health');
    if (healthEl) {
      healthEl.textContent = data.system_health.state;
      healthEl.className = `status-pill status-${data.system_health.state.toLowerCase()}`;
    }
    const healthSubEl = document.getElementById('kpi-system-health-subtext');
    if (healthSubEl) {
      if (data.system_health.reasons && data.system_health.reasons.length > 0) {
        healthSubEl.textContent = data.system_health.reasons[0];
      } else {
        healthSubEl.textContent = 'All monitored subsystems fully operational';
      }
    }
  }

  // Subsystem Telemetry
  if (data.database) {
    const dbEl = document.getElementById('subsystem-db');
    if (dbEl) {
      dbEl.textContent = data.database.status;
      dbEl.className = `status-pill status-${data.database.connected ? 'healthy' : 'unhealthy'}`;
    }
  }

  if (data.whatsapp) {
    const waEl = document.getElementById('subsystem-whatsapp');
    if (waEl) {
      waEl.textContent = data.whatsapp.profile_exists ? 'Configured' : 'Missing Profile';
      waEl.className = `status-pill status-${data.whatsapp.profile_exists ? 'healthy' : 'unhealthy'}`;
    }
  }

  if (data.emergency_stop) {
    const isAct = data.emergency_stop.is_active;
    const estopEl = document.getElementById('subsystem-estop');
    if (estopEl) {
      estopEl.textContent = isAct ? 'ACTIVE' : 'INACTIVE';
      estopEl.className = `status-pill status-${isAct ? 'unhealthy' : 'healthy'}`;
    }

    const ctrlEstopState = document.getElementById('ctrl-estop-state');
    if (ctrlEstopState) {
      ctrlEstopState.textContent = isAct ? 'ACTIVE (Dispatch Blocked)' : 'INACTIVE (Dispatch Allowed)';
      ctrlEstopState.className = `status-pill status-${isAct ? 'unhealthy' : 'healthy'}`;
    }
    const ctrlEstopTrig = document.getElementById('ctrl-estop-triggered');
    if (ctrlEstopTrig) {
      ctrlEstopTrig.textContent = data.emergency_stop.activated_at || 'Never';
    }
    const ctrlEstopReason = document.getElementById('ctrl-estop-reason');
    if (ctrlEstopReason) {
      ctrlEstopReason.textContent = data.emergency_stop.reason || 'None';
    }

    const role = window.CURRENT_USER_ROLE || '';
    const btnEstop = document.getElementById('btn-emergency-stop');
    if (btnEstop) {
      btnEstop.disabled = isAct || role === 'VIEWER';
    }
    const btnResume = document.getElementById('btn-emergency-resume');
    if (btnResume) {
      btnResume.disabled = !isAct || !['ADMIN', 'OWNER'].includes(role);
    }
  }

  if (data.circuit_breaker) {
    const cbEl = document.getElementById('subsystem-cb');
    if (cbEl) {
      cbEl.textContent = data.circuit_breaker.status;
      cbEl.className = `status-pill status-${data.circuit_breaker.status.toLowerCase()}`;
    }
  }

  // KPI 2: Confirmed Send Rate
  const sendRateEl = document.getElementById('kpi-send-rate');
  if (sendRateEl) {
    if (data.active_campaign && data.active_campaign.confirmed_send_rate !== null && data.active_campaign.confirmed_send_rate !== undefined) {
      sendRateEl.textContent = `${data.active_campaign.confirmed_send_rate}%`;
    } else {
      sendRateEl.textContent = 'N/A';
    }
  }

  // KPI 3: Queue Depth & Queue Breakdown
  if (data.queue) {
    const qDepthEl = document.getElementById('kpi-queue-depth');
    if (qDepthEl) {
      qDepthEl.textContent = (data.queue.queued || 0) + (data.queue.processing || 0);
    }
    const qSubEl = document.getElementById('kpi-queue-subtext');
    if (qSubEl) {
      qSubEl.textContent = `${data.queue.queued || 0} pending, ${data.queue.processing || 0} active leases`;
    }

    const updateQ = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val !== undefined ? val : 0;
    };
    updateQ('qc-pending', data.queue.queued);
    updateQ('qc-claimed', data.queue.processing);
    updateQ('qc-confirmed', data.queue.confirmed_sends);
    updateQ('qc-failed', data.queue.failed);
    updateQ('qc-retry', data.queue.retry_pending);
    updateQ('qc-unknown-outcome', data.queue.unknown_outcome);
  }

  // KPI 4 & Remote Runner Supervision
  if (data.runner) {
    const rStatEl = document.getElementById('kpi-runner-status');
    if (rStatEl) {
      rStatEl.textContent = data.runner.state;
      rStatEl.className = `status-pill status-${data.runner.state.toLowerCase()}`;
    }
    const rSubEl = document.getElementById('kpi-runner-subtext');
    if (rSubEl) {
      rSubEl.textContent = data.runner.pid ? `PID: ${data.runner.pid}` : 'OS lock released';
    }

    const ctrlState = document.getElementById('ctrl-runner-state');
    if (ctrlState) ctrlState.textContent = data.runner.state;
    const ctrlCamp = document.getElementById('ctrl-runner-campaign');
    if (ctrlCamp) ctrlCamp.textContent = data.runner.campaign_id ? `#${data.runner.campaign_id}` : 'None';
    const ctrlHb = document.getElementById('ctrl-runner-heartbeat');
    if (ctrlHb) {
      ctrlHb.textContent = data.runner.heartbeat_age_seconds !== null && data.runner.heartbeat_age_seconds !== undefined
        ? `${data.runner.heartbeat_age_seconds}s ago`
        : 'Offline';
    }

    const role = window.CURRENT_USER_ROLE || '';
    const btnStart = document.getElementById('btn-start-runner');
    if (btnStart) {
      btnStart.disabled = data.runner.state === 'RUNNING' || role === 'VIEWER';
    }
    const btnStop = document.getElementById('btn-stop-runner');
    if (btnStop) {
      btnStop.disabled = data.runner.state !== 'RUNNING' || role === 'VIEWER';
    }
  }

  // Active Campaign Card
  const campContainer = document.getElementById('active-campaign-container');
  if (campContainer && data.active_campaign) {
    const ac = data.active_campaign;
    const nameEl = document.getElementById('campaign-name');
    if (nameEl) nameEl.textContent = ac.name;
    const statEl = document.getElementById('campaign-status');
    if (statEl) {
      statEl.textContent = ac.status;
      statEl.className = `status-pill status-${ac.status.toLowerCase()}`;
    }
    const idEl = document.getElementById('campaign-id');
    if (idEl) idEl.textContent = `#${ac.id}`;

    const pct = ac.completion_percentage || 0.0;
    const barEl = document.getElementById('campaign-progress-bar');
    if (barEl) barEl.style.width = `${pct}%`;
    const pctEl = document.getElementById('campaign-progress-pct');
    if (pctEl) pctEl.textContent = `${pct}%`;

    const confEl = document.getElementById('camp-confirmed');
    if (confEl) confEl.textContent = ac.send_confirmed || 0;
    const failEl = document.getElementById('camp-failed');
    if (failEl) failEl.textContent = ac.failed || 0;
    const unkEl = document.getElementById('camp-unknown');
    if (unkEl) unkEl.textContent = ac.unknown_outcome || 0;
  }

  // Alerts Panel
  if (data.alerts) {
    const badgeEl = document.getElementById('alerts-count-badge');
    if (badgeEl) {
      badgeEl.textContent = `${data.alerts.length} active`;
      badgeEl.style.backgroundColor = data.alerts.length > 0 ? 'var(--ids-warning)' : 'var(--ids-success)';
      badgeEl.style.color = '#000000';
    }
    const alertsContainer = document.getElementById('alerts-container');
    if (alertsContainer) {
      if (data.alerts.length === 0) {
        alertsContainer.innerHTML = '<div style="color: var(--ids-text-muted); font-size: 13px; text-align: center; padding: 1rem 0;">All subsystems operational. No active alerts.</div>';
      } else {
        alertsContainer.innerHTML = data.alerts.map(a => `
          <div class="alert-item ${a.severity}">
            <div><strong>[${a.severity}]</strong> ${a.message} <span style="font-size: 11px; opacity: 0.8; font-family: var(--font-mono);">(${a.id})</span></div>
          </div>
        `).join('');
      }
    }
  }
}

async function fetchDashboardSnapshot() {
  try {
    const res = await apiFetch('/api/v1/dashboard/summary');
    if (res.ok) {
      const data = await res.json();
      updateDashboard(data);
    }
  } catch (err) {
    console.error('Failed to fetch dashboard summary:', err);
  }
}

function startPolling() {
  if (pollingTimer) return;
  setTelemetryStatus('polling');
  pollingTimer = setInterval(fetchDashboardSnapshot, 5000);
}

function stopPolling() {
  if (pollingTimer) {
    clearInterval(pollingTimer);
    pollingTimer = null;
  }
}

function initTelemetry() {
  const indicator = document.getElementById('telemetry-indicator');
  if (!indicator) return;

  if (typeof EventSource === 'undefined') {
    startPolling();
    return;
  }

  try {
    sseEventSource = new EventSource('/api/v1/events/stream');

    sseEventSource.onopen = () => {
      stopPolling();
      setTelemetryStatus('live');
    };

    sseEventSource.addEventListener('telemetry', (event) => {
      try {
        const payload = JSON.parse(event.data);
        updateDashboard(payload);
      } catch (err) {
        console.error('Error parsing telemetry SSE payload:', err);
      }
    });

    sseEventSource.onerror = () => {
      console.warn('SSE connection interrupted. Falling back to polling.');
      if (sseEventSource) {
        sseEventSource.close();
        sseEventSource = null;
      }
      startPolling();

      // Retry SSE in 15 seconds
      setTimeout(() => {
        if (!sseEventSource) {
          initTelemetry();
        }
      }, 15000);
    };
  } catch (err) {
    console.error('SSE initialization error:', err);
    startPolling();
  }
}

// Setup Event Handlers when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Mobile drawer controls
  const toggleBtn = document.getElementById('mobile-drawer-toggle');
  const sidebar = document.querySelector('.sidebar');
  const overlay = document.querySelector('.sidebar-overlay');

  if (toggleBtn && sidebar && overlay) {
    toggleBtn.addEventListener('click', () => {
      sidebar.classList.toggle('open');
      overlay.classList.toggle('open');
    });

    overlay.addEventListener('click', () => {
      sidebar.classList.remove('open');
      overlay.classList.remove('open');
    });
  }

  // Initialize telemetry if on dashboard
  if (document.getElementById('telemetry-indicator')) {
    initTelemetry();
  }

  // Refresh button
  const btnRefresh = document.getElementById('btn-refresh');
  if (btnRefresh) {
    btnRefresh.addEventListener('click', async () => {
      btnRefresh.disabled = true;
      await fetchDashboardSnapshot();
      setTimeout(() => { btnRefresh.disabled = false; }, 500);
    });
  }

  // Runner controls
  const btnStartRunner = document.getElementById('btn-start-runner');
  if (btnStartRunner) {
    btnStartRunner.addEventListener('click', () => openModal('modal-start-runner'));
  }

  const btnConfirmStartRunner = document.getElementById('btn-confirm-start-runner');
  if (btnConfirmStartRunner) {
    btnConfirmStartRunner.addEventListener('click', async () => {
      const campInput = document.getElementById('start-runner-campaign-id');
      const campaignId = campInput ? campInput.value.trim() : '';
      if (!campaignId) {
        showAlert('start-runner-error', 'Campaign ID is required to start runner.', 'error');
        return;
      }

      btnConfirmStartRunner.disabled = true;
      hideAlert('start-runner-error');
      try {
        const res = await apiFetch('/api/v1/runner/start', {
          method: 'POST',
          body: { campaign_id: campaignId }
        });
        const data = await res.json();
        if (res.ok) {
          closeModal('modal-start-runner');
          showAlert('dashboard-global-alert', `Runner start requested successfully (PID: ${data.pid || 'Remote Desired State Set'}).`, 'success');
          await fetchDashboardSnapshot();
        } else {
          showAlert('start-runner-error', data.detail || 'Failed to start runner.', 'error');
        }
      } catch (err) {
        showAlert('start-runner-error', `Request error: ${err.message}`, 'error');
      } finally {
        btnConfirmStartRunner.disabled = false;
      }
    });
  }

  const btnStopRunner = document.getElementById('btn-stop-runner');
  if (btnStopRunner) {
    btnStopRunner.addEventListener('click', () => openModal('modal-stop-runner'));
  }

  const btnConfirmStopRunner = document.getElementById('btn-confirm-stop-runner');
  if (btnConfirmStopRunner) {
    btnConfirmStopRunner.addEventListener('click', async () => {
      btnConfirmStopRunner.disabled = true;
      hideAlert('stop-runner-error');
      try {
        const res = await apiFetch('/api/v1/runner/stop', {
          method: 'POST',
          body: { timeout_seconds: 10 }
        });
        const data = await res.json();
        if (res.ok) {
          closeModal('modal-stop-runner');
          showAlert('dashboard-global-alert', 'Runner graceful stop requested successfully.', 'success');
          await fetchDashboardSnapshot();
        } else {
          showAlert('stop-runner-error', data.detail || 'Failed to stop runner.', 'error');
        }
      } catch (err) {
        showAlert('stop-runner-error', `Request error: ${err.message}`, 'error');
      } finally {
        btnConfirmStopRunner.disabled = false;
      }
    });
  }

  // Emergency stop controls
  const btnEstop = document.getElementById('btn-emergency-stop');
  if (btnEstop) {
    btnEstop.addEventListener('click', () => openModal('modal-emergency-stop'));
  }

  const btnConfirmEstop = document.getElementById('btn-confirm-emergency-stop');
  if (btnConfirmEstop) {
    btnConfirmEstop.addEventListener('click', async () => {
      const reasonInput = document.getElementById('estop-reason');
      const reason = reasonInput ? reasonInput.value.trim() : '';
      if (!reason) {
        showAlert('emergency-stop-error', 'Reason is required to activate emergency stop.', 'error');
        return;
      }

      btnConfirmEstop.disabled = true;
      hideAlert('emergency-stop-error');
      try {
        const res = await apiFetch('/api/v1/system/emergency-stop', {
          method: 'POST',
          body: { reason: reason }
        });
        const data = await res.json();
        if (res.ok) {
          closeModal('modal-emergency-stop');
          showAlert('dashboard-global-alert', 'EMERGENCY STOP ACTIVE — All queue dispatch claims blocked.', 'error');
          await fetchDashboardSnapshot();
        } else {
          showAlert('emergency-stop-error', data.detail || 'Failed to trigger emergency stop.', 'error');
        }
      } catch (err) {
        showAlert('emergency-stop-error', `Request error: ${err.message}`, 'error');
      } finally {
        btnConfirmEstop.disabled = false;
      }
    });
  }

  const btnResume = document.getElementById('btn-emergency-resume');
  if (btnResume) {
    btnResume.addEventListener('click', () => openModal('modal-emergency-resume'));
  }

  const btnConfirmResume = document.getElementById('btn-confirm-emergency-resume');
  if (btnConfirmResume) {
    btnConfirmResume.addEventListener('click', async () => {
      const reasonInput = document.getElementById('resume-reason');
      const reason = reasonInput ? reasonInput.value.trim() : '';
      if (!reason) {
        showAlert('emergency-resume-error', 'Reason is required to resume dispatch operations.', 'error');
        return;
      }

      btnConfirmResume.disabled = true;
      hideAlert('emergency-resume-error');
      try {
        const res = await apiFetch('/api/v1/system/emergency-resume', {
          method: 'POST',
          body: { reason: reason }
        });
        const data = await res.json();
        if (res.ok) {
          closeModal('modal-emergency-resume');
          showAlert('dashboard-global-alert', 'Dispatch operations resumed successfully. Emergency stop inactive.', 'success');
          await fetchDashboardSnapshot();
        } else {
          showAlert('emergency-resume-error', data.detail || 'Failed to resume operations.', 'error');
        }
      } catch (err) {
        showAlert('emergency-resume-error', `Request error: ${err.message}`, 'error');
      } finally {
        btnConfirmResume.disabled = false;
      }
    });
  }
});
