// State
let appState = {
  currentTab: 'clm',
  currentExplorerSubtab: 'campaigns',
  status: null,
  latestRun: null,
  campaigns: [],
  segments: [],
  journeys: [],
  analytics: null,
  clmBoardData: null,
  funnelChartInstance: null
};

// Initialization
document.addEventListener('DOMContentLoaded', async () => {
  await fetchStatus();
  await loadCLMBoard();
  await loadDailyData();
  await loadSettings();
  await loadChatHistory();
  lucide.createIcons();

  // Periodic poll for status & scheduler (every 20 seconds)
  setInterval(fetchStatus, 20000);
});

// Toast Notifications
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  const bg = type === 'success' ? 'bg-emerald-950 border-emerald-600 text-emerald-200' :
             type === 'error' ? 'bg-rose-950 border-rose-600 text-rose-200' :
             'bg-slate-900 border-indigo-600 text-slate-200';

  toast.className = `flex items-center space-x-2 px-4 py-3 rounded-xl border shadow-xl text-xs pointer-events-auto transform transition-all duration-300 opacity-0 translate-y-2 ${bg}`;
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);

  requestAnimationFrame(() => {
    toast.classList.remove('opacity-0', 'translate-y-2');
  });

  setTimeout(() => {
    toast.classList.add('opacity-0', 'translate-y-2');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Navigation Tabs
function switchTab(tabId) {
  appState.currentTab = tabId;
  document.querySelectorAll('.nav-tab').forEach(btn => {
    btn.classList.remove('active', 'text-slate-200', 'bg-slate-800/90', 'border-slate-700/60');
    btn.classList.add('text-slate-400');
  });

  const activeBtn = document.getElementById(`tab-btn-${tabId}`);
  if (activeBtn) {
    activeBtn.classList.add('active', 'text-slate-200');
    activeBtn.classList.remove('text-slate-400');
  }

  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.add('hidden');
    content.classList.remove('flex');
  });

  const targetContent = document.getElementById(`tab-${tabId}`);
  if (targetContent) {
    targetContent.classList.remove('hidden');
    if (tabId === 'chat') {
      targetContent.classList.add('flex');
      scrollChatToBottom();
    } else if (tabId === 'explorer') {
      loadExplorerData();
    } else if (tabId === 'clm') {
      loadCLMBoard();
    }
  }

  lucide.createIcons();
}

// ================= CLM COMMAND BOARD FUNCTIONS =================
async function loadCLMBoard() {
  try {
    const res = await fetch('/api/clm/board');
    const data = await res.json();
    appState.clmBoardData = data;

    // Scorecard
    const score = data.health_scorecard || {};
    const timeEl = document.getElementById('clm-board-time');
    if (timeEl) timeEl.innerText = `Audited: ${data.timestamp}`;
    
    const setIfExists = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.innerText = val;
    };

    setIfExists('clm-retention-idx', `${score.overall_retention_index} / 100`);
    setIfExists('clm-avg-ctr', `${score.average_ctr}%`);
    setIfExists('clm-avg-delivery', `${score.average_delivery_rate}%`);
    setIfExists('clm-total-rev', `$${(score.total_tracked_revenue || 0).toLocaleString()}`);
    setIfExists('clm-unlocked-gmv', `+$${(score.unlocked_gmv_potential || 0).toLocaleString()}`);
    setIfExists('clm-coverage-pct', `${score.clm_coverage_pct}%`);

    // Audit Table
    const audits = data.audited_campaigns || [];
    setIfExists('clm-audit-count', `${audits.length} Running Campaigns Audited`);
    const tbody = document.getElementById('clm-audit-table-body');
    if (tbody) {
      tbody.innerHTML = audits.map(a => `
        <tr class="hover:bg-slate-800/40 transition-colors">
          <td class="py-3 px-4 font-semibold text-white">${a.name}</td>
          <td class="py-3 px-4">
            <span class="px-2 py-0.5 rounded text-[10px] uppercase font-mono ${a.channel === 'Push' ? 'bg-amber-500/20 text-amber-300' : 'bg-blue-500/20 text-blue-300'}">
              ${a.channel}
            </span>
          </td>
          <td class="py-3 px-4 font-mono">
            <span class="text-indigo-300 font-bold">${a.ctr}% CTR</span>
            <span class="text-slate-500 text-[11px] block">${a.delivery_rate}% Deliv</span>
          </td>
          <td class="py-3 px-4 text-emerald-400 font-medium">$${(a.revenue || 0).toLocaleString()}</td>
          <td class="py-3 px-4">
            <span class="px-2.5 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider border ${a.verdict_badge}">
              ${a.verdict}
            </span>
          </td>
          <td class="py-3 px-4 text-slate-300 text-xs max-w-sm leading-relaxed">${a.recommendation}</td>
          <td class="py-3 px-4 text-right">
            <button onclick="optimizeCampaignWithCopilot('${escapeJs(a.name)}', '${escapeJs(a.recommendation)}')" class="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-indigo-600/30 hover:bg-indigo-600 text-indigo-200 hover:text-white border border-indigo-500/40 transition-all cursor-pointer">
              Optimize ⚡️
            </button>
          </td>
        </tr>
      `).join('');
    }

    // CLM Matrix Grid
    const matrix = data.clm_matrix || [];
    const matrixGrid = document.getElementById('clm-matrix-grid');
    if (matrixGrid) {
      matrixGrid.innerHTML = matrix.map(m => `
        <div class="p-4 rounded-xl border border-slate-800 bg-slate-950/70 hover:border-slate-700 transition-all flex flex-col justify-between space-y-3">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-mono uppercase px-2 py-0.5 rounded border ${m.status_badge}">
                ${m.status}
              </span>
              <span class="text-xs font-bold font-mono text-cyan-400">${m.coverage_pct}%</span>
            </div>
            <h4 class="font-bold text-xs text-white">${m.stage_name}</h4>
            <p class="text-[10px] text-slate-400">${m.target_users}</p>
            <div class="p-2 rounded-lg bg-slate-900/80 border border-slate-800 text-[10px] text-amber-300/90 leading-tight">
              <strong>Gap:</strong> ${m.identified_gap}
            </div>
          </div>
          <button onclick="generateDraftForStage('${m.stage_id}')" class="w-full py-1.5 rounded-lg text-[11px] font-semibold bg-indigo-600 hover:bg-indigo-500 text-white shadow-md transition-all cursor-pointer flex items-center justify-center space-x-1">
            <i data-lucide="plus" class="w-3 h-3"></i>
            <span>Draft Campaign</span>
          </button>
        </div>
      `).join('');
    }

    // Opportunities ("What Else Can Be Done?")
    const opps = data.opportunities || [];
    const oppsGrid = document.getElementById('clm-opportunities-grid');
    if (oppsGrid) {
      oppsGrid.innerHTML = opps.map(o => `
        <div class="p-4 rounded-xl border border-slate-800 bg-slate-950/60 hover:border-slate-700 transition-all space-y-2.5">
          <div class="flex items-center justify-between">
            <span class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${o.priority_badge}">
              ${o.priority}
            </span>
            <span class="text-xs font-mono text-indigo-400 font-semibold">${o.pillar}</span>
          </div>
          <h4 class="font-bold text-xs text-white">${o.title}</h4>
          <p class="text-[11px] text-rose-300/90 bg-rose-950/30 p-2 rounded-lg border border-rose-900/30">
            <strong>Problem Detected:</strong> ${o.problem}
          </p>
          <p class="text-[11px] text-slate-300 bg-slate-900/80 p-2.5 rounded-lg border border-slate-800">
            <strong>Superhuman Action:</strong> ${o.what_to_do}
          </p>
          <div class="pt-1 flex items-center justify-between text-[11px] text-emerald-400 font-medium">
            <span>⚡️ Lift: ${o.estimated_lift}</span>
            <button onclick="sendQuickPrompt('Draft execution plan for: ${escapeJs(o.title)}')" class="hover:underline text-indigo-300 cursor-pointer">
              Ask Copilot &rarr;
            </button>
          </div>
        </div>
      `).join('');
    }

    // Featured Draft Campaign
    renderDraftWorkbench(data.featured_draft_campaign);
    lucide.createIcons();

  } catch (err) {
    console.error('Failed to load CLM board:', err);
  }
}

function renderDraftWorkbench(draft) {
  if (!draft) return;
  const setIfExists = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.innerText = val;
  };

  setIfExists('draft-stage-tag', `Stage: ${draft.clm_stage || 'Lifecycle'}`);
  setIfExists('draft-campaign-title', draft.campaign_name);
  setIfExists('draft-segment-name', draft.target_segment_name);
  setIfExists('draft-criteria-box', JSON.stringify(draft.target_criteria, null, 2));
  setIfExists('draft-expected-impact', `⚡️ ${draft.expected_impact}`);

  const varA = draft.ab_variants?.[0] || {};
  const varB = draft.ab_variants?.[1] || {};

  setIfExists('draft-varA-title', varA.title || 'Variant A');
  setIfExists('draft-varA-body', varA.body || '');
  setIfExists('draft-varA-cta', varA.cta || 'Explore');

  setIfExists('draft-varB-title', varB.title || 'Variant B');
  setIfExists('draft-varB-body', varB.body || '');
  setIfExists('draft-varB-cta', varB.cta || 'Claim');
}

async function generateDraftForStage(stageId) {
  showToast(`Synthesizing CLM draft campaign for ${stageId}...`, 'info');
  try {
    const res = await fetch('/api/clm/draft-campaign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stage_id: stageId })
    });
    const draft = await res.json();
    renderDraftWorkbench(draft);
    showToast(`Draft generated for ${draft.clm_stage}!`, 'success');
    const titleEl = document.getElementById('draft-campaign-title');
    if (titleEl) titleEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (err) {
    showToast(`Failed to generate draft: ${err.message}`, 'error');
  }
}

async function pushDraftToMoEngage() {
  const btn = document.getElementById('btn-push-draft');
  const original = btn.innerHTML;
  btn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span>Pushing to MoEngage...</span>';
  btn.disabled = true;

  try {
    const name = document.getElementById('draft-campaign-title').innerText;
    const segment = document.getElementById('draft-segment-name').innerText;
    const res = await fetch('/api/clm/push-draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        campaign_name: name,
        channel: 'Push',
        target_segment_name: segment
      })
    });
    const data = await res.json();
    showToast(`Success! Draft Campaign "${name}" created in MoEngage (${data.campaign_id})`, 'success');
  } catch (err) {
    showToast(`Error pushing draft: ${err.message}`, 'error');
  } finally {
    btn.innerHTML = original;
    btn.disabled = false;
    lucide.createIcons();
  }
}

function copyDraftVar(variant) {
  const text = document.getElementById(`draft-var${variant}-body`).innerText;
  copyToClipboard(text);
}

function optimizeCampaignWithCopilot(campName, recommendation) {
  switchTab('chat');
  sendQuickPrompt(`Campaign '${campName}' received recommendation: "${recommendation}". How should we restructure and rewrite this campaign to maximize conversions?`);
}

function refreshCLMBoard() {
  showToast('Re-auditing MoEngage campaigns & lifecycle board...', 'info');
  loadCLMBoard();
}

// Fetch Status & Health
async function fetchStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    appState.status = data;

    // Update Header MoEngage Status
    const moeBadge = document.getElementById('moe-status-text');
    const moePing = document.getElementById('moe-status-ping');
    const moeDot = document.getElementById('moe-status-dot');

    if (data.moengage.valid) {
      moeBadge.innerText = data.moengage.mode === 'mock' ? 'MoEngage (Demo Mode)' : 'MoEngage Connected';
      moeBadge.className = 'text-emerald-400 font-medium';
      moePing.className = 'animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75';
      moeDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-emerald-500';
    } else {
      moeBadge.innerText = 'MoEngage Cookies Missing/Expired';
      moeBadge.className = 'text-rose-400 font-medium';
      moePing.className = 'hidden';
      moeDot.className = 'relative inline-flex rounded-full h-2 w-2 bg-rose-500';
    }

    // Update AI Engine Status
    const gemText = document.getElementById('gemini-status-text');
    if (gemText) {
      gemText.innerText = data.ai_engine?.name || 'Antigravity Local Engine';
    }

    // Update Sidebar Scheduler
    const schedBadge = document.getElementById('sidebar-sched-badge');
    const schedDesc = document.getElementById('sidebar-sched-desc');
    const lastRunText = document.getElementById('sidebar-last-run');

    schedBadge.innerText = data.scheduler.enabled ? 'Active' : 'Paused';
    schedBadge.className = data.scheduler.enabled ? 
      'px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-300' :
      'px-1.5 py-0.5 rounded text-[10px] bg-slate-700 text-slate-400';
    schedDesc.innerText = `Every day at ${data.scheduler.time}`;
    if (data.scheduler.last_run_time) {
      lastRunText.innerText = `Last run: ${data.scheduler.last_run_time}`;
    }

  } catch (err) {
    console.error('Failed to fetch status:', err);
  }
}

// Load Daily Intelligence Data
async function loadDailyData() {
  try {
    const res = await fetch('/api/automation/latest');
    const data = await res.json();
    if (!data) return;

    appState.latestRun = data;

    // Set summary and date
    document.getElementById('daily-run-date').innerText = new Date(data.created_at).toLocaleDateString(undefined, {
      weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
    });
    document.getElementById('daily-exec-summary').innerText = data.summary || 'Summary unavailable.';

    // Top Insights
    const report = data.report_data || {};
    const insights = report.top_insights || [
      "Push campaigns generated 2.4x higher conversion during evening hours (19:00 - 21:00).",
      "High drop-off rate detected at Cart to Checkout stage (64.87%).",
      "VIP customers reactivated with double-point email incentive achieved 5.15% conversion."
    ];
    const insightsContainer = document.getElementById('top-insights-container');
    insightsContainer.innerHTML = insights.map((ins, i) => `
      <div class="p-3.5 rounded-xl bg-slate-900/80 border border-slate-800 flex items-start space-x-3">
        <div class="w-6 h-6 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center shrink-0 text-xs font-bold font-mono">
          0${i+1}
        </div>
        <p class="text-xs text-slate-300 leading-relaxed">${ins}</p>
      </div>
    `).join('');

    // Campaign Ideas
    const campaigns = data.campaign_ideas || report.campaign_ideas || [];
    document.getElementById('campaign-count-badge').innerText = `${campaigns.length} Ideas`;
    const campaignsGrid = document.getElementById('campaign-cards-grid');
    campaignsGrid.innerHTML = campaigns.map(camp => {
      const channelColor = camp.channel === 'Push' ? 'bg-amber-500/20 text-amber-300 border-amber-500/30' :
                           camp.channel === 'Email' ? 'bg-blue-500/20 text-blue-300 border-blue-500/30' :
                           camp.channel === 'In-App' ? 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' :
                           'bg-purple-500/20 text-purple-300 border-purple-500/30';

      return `
        <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/70 hover:border-slate-700 transition-all space-y-3 flex flex-col justify-between">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-[11px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-md border ${channelColor}">
                ${camp.channel}
              </span>
              <span class="text-[11px] text-slate-400 flex items-center space-x-1">
                <i data-lucide="target" class="w-3 h-3 text-slate-500"></i>
                <span>${camp.target_segment || 'Audience'}</span>
              </span>
            </div>
            <h4 class="font-bold text-sm text-white">${camp.title}</h4>
            <div class="p-3 rounded-lg bg-slate-950/80 border border-slate-800/80 text-xs text-slate-200 font-sans leading-relaxed">
              ${camp.body}
            </div>
          </div>

          <div class="pt-2 border-t border-slate-800/60 flex items-center justify-between text-xs">
            <div class="text-[11px] text-emerald-400 font-medium">
              ⚡️ ${camp.expected_impact || 'High conversion probability'}
            </div>
            <button onclick="copyToClipboard('${escapeJs(camp.body)}')" class="px-2.5 py-1 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white flex items-center space-x-1 transition-colors cursor-pointer">
              <i data-lucide="copy" class="w-3 h-3"></i>
              <span>Copy</span>
            </button>
          </div>
        </div>
      `;
    }).join('');

    // Segment Recommendations
    const segments = data.segments || report.segments || [];
    document.getElementById('segment-count-badge').innerText = `${segments.length} Segments`;
    const segmentsGrid = document.getElementById('segment-cards-grid');
    segmentsGrid.innerHTML = segments.map((seg, idx) => {
      const criteriaDisplay = typeof seg.criteria === 'object' ? JSON.stringify(seg.criteria, null, 2) : (seg.criteria_json || seg.criteria || '');
      const reach = seg.estimated_reach ? seg.estimated_reach.toLocaleString() : 'Est. ~25,000';
      const isCreated = seg.status === 'created';

      return `
        <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/70 hover:border-slate-700 transition-all flex flex-col justify-between space-y-3">
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <span class="text-[11px] font-semibold text-cyan-400 bg-cyan-950/50 border border-cyan-800/50 px-2 py-0.5 rounded">
                Reach: ~${reach} users
              </span>
              <span class="text-[10px] uppercase font-mono px-2 py-0.5 rounded ${isCreated ? 'bg-emerald-900/40 text-emerald-300 border border-emerald-700/50' : 'bg-slate-800 text-slate-400'}">
                ${isCreated ? 'Active in MoEngage' : 'Draft Proposal'}
              </span>
            </div>
            <h4 class="font-bold text-sm text-white">${seg.name}</h4>
            <p class="text-xs text-slate-400 line-clamp-2">${seg.description || ''}</p>
            <details class="text-[11px] text-slate-400 group">
              <summary class="cursor-pointer text-indigo-400 hover:underline">View Filter Rules</summary>
              <pre class="mt-1 p-2 rounded bg-slate-950 text-[10px] font-mono text-slate-300 overflow-x-auto">${criteriaDisplay}</pre>
            </details>
          </div>

          <div class="pt-2 border-t border-slate-800/60">
            <button onclick="pushSegmentToMoEngage('${escapeJs(seg.name)}', '${escapeJs(seg.description)}', ${seg.id || null})" class="w-full py-1.5 rounded-lg text-xs font-semibold ${isCreated ? 'bg-slate-800 text-slate-400 cursor-default' : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-md shadow-indigo-600/20'} flex items-center justify-center space-x-1.5 transition-all">
              <i data-lucide="${isCreated ? 'check' : 'send'}" class="w-3.5 h-3.5"></i>
              <span>${isCreated ? 'Pushed to MoEngage' : 'Push to MoEngage'}</span>
            </button>
          </div>
        </div>
      `;
    }).join('');

    // Load Run History
    await loadRunHistory();
    lucide.createIcons();

  } catch (err) {
    console.error('Failed to load daily data:', err);
  }
}

// Load Run History list
async function loadRunHistory() {
  try {
    const res = await fetch('/api/automation/history');
    const history = await res.json();
    const container = document.getElementById('run-history-list');
    if (!history || history.length === 0) {
      container.innerHTML = '<p class="text-slate-500">No previous runs recorded yet.</p>';
      return;
    }

    container.innerHTML = history.map(item => `
      <div class="p-2.5 rounded-lg bg-slate-900 border border-slate-800/80 flex items-center justify-between">
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-indigo-400"></span>
          <span class="font-mono text-slate-300">${new Date(item.created_at).toLocaleString()}</span>
          <span class="text-slate-500">(${item.trigger_type})</span>
          <span class="text-slate-400 truncate max-w-md">${item.summary || ''}</span>
        </div>
        <span class="px-2 py-0.5 rounded text-[10px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">${item.status}</span>
      </div>
    `).join('');
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

// Trigger Manual Daily Process Run
async function triggerDailyRun() {
  const btn = document.getElementById('btn-run-daily');
  const label = document.getElementById('run-daily-label');
  const originalHtml = label.innerText;

  try {
    label.innerText = 'Synthesizing with Gemini...';
    btn.disabled = true;
    btn.classList.add('opacity-80');

    const res = await fetch('/api/automation/run', { method: 'POST' });
    const result = await res.json();

    if (result.success) {
      showToast('Daily intelligence analysis synthesized successfully!', 'success');
      await loadDailyData();
      await fetchStatus();
    } else {
      showToast(`Run failed: ${result.error || 'Unknown error'}`, 'error');
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  } finally {
    label.innerText = originalHtml;
    btn.disabled = false;
    btn.classList.remove('opacity-80');
    lucide.createIcons();
  }
}

// Push Segment to MoEngage
async function pushSegmentToMoEngage(name, description, dbId) {
  try {
    const payload = {
      name: name,
      description: description,
      criteria: {
        "event_filter": "Added to Cart >= 1 in last 24 hours",
        "exclusion_filter": "Purchase Completed >= 1 in last 24 hours"
      },
      segment_db_id: dbId
    };

    const res = await fetch('/api/moengage/segments/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (data.success) {
      showToast(`Segment "${name}" pushed to MoEngage! (${data.status})`, 'success');
      await loadDailyData();
    } else {
      showToast('Failed to push segment', 'error');
    }
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  }
}

// Interactive Chat
async function handleChatSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const message = input.value.trim();
  if (!message) return;

  // Append user message
  appendChatMessage('user', message);
  input.value = '';

  // Show typing indicator
  const typingId = appendTypingIndicator();
  scrollChatToBottom();

  try {
    const res = await fetch('/api/agent/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    const data = await res.json();
    removeTypingIndicator(typingId);

    if (data.reply) {
      appendChatMessage('assistant', data.reply, data.tool_used);
    } else {
      appendChatMessage('assistant', 'Sorry, I encountered an issue processing your request.');
    }
  } catch (err) {
    removeTypingIndicator(typingId);
    appendChatMessage('assistant', `Error contacting AI Brain: ${err.message}`);
  }

  scrollChatToBottom();
  lucide.createIcons();
}

function sendQuickPrompt(promptText) {
  document.getElementById('chat-input').value = promptText;
  handleChatSubmit(new Event('submit'));
}

async function loadChatHistory() {
  try {
    const res = await fetch('/api/agent/history');
    const history = await res.json();
    const container = document.getElementById('chat-messages');
    container.innerHTML = '';

    if (!history || history.length === 0) {
      // Welcome assistant message
      appendChatMessage('assistant', `👋 **Welcome to your MoEngage Gemini Copilot!**\n\nI have real-time access to your MoEngage campaigns, customer segments, and conversion metrics.\n\nYou can ask me to:\n* Audit recent campaign conversion rates\n* Generate push & email notifications for abandoned carts\n* Design new retention micro-segments\n* Inspect purchase funnel bottlenecks`);
      return;
    }

    history.forEach(item => {
      const tool = item.tool_calls ? JSON.parse(item.tool_calls).tool : null;
      appendChatMessage(item.role, item.content, tool);
    });
    scrollChatToBottom();
  } catch (err) {
    console.error('Failed to load chat:', err);
  }
}

function appendChatMessage(role, content, tool = null) {
  const container = document.getElementById('chat-messages');
  const msgDiv = document.createElement('div');
  const isUser = role === 'user';

  msgDiv.className = `flex ${isUser ? 'justify-end' : 'justify-start'} space-x-3`;

  const parsedContent = isUser ? escapeHtml(content) : marked.parse(content);
  const toolBadge = tool ? `<div class="inline-flex items-center space-x-1 px-2 py-0.5 mb-2 rounded bg-indigo-950/60 border border-indigo-700/50 text-[10px] text-indigo-300 font-mono"><i data-lucide="wrench" class="w-3 h-3"></i><span>Tool: ${tool}</span></div>` : '';

  if (isUser) {
    msgDiv.innerHTML = `
      <div class="max-w-xl bg-indigo-600 text-white px-4 py-2.5 rounded-2xl rounded-tr-none text-xs shadow-md">
        <p class="whitespace-pre-wrap">${parsedContent}</p>
      </div>
    `;
  } else {
    msgDiv.innerHTML = `
      <div class="w-7 h-7 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-indigo-400 shrink-0 mt-0.5">
        <i data-lucide="bot" class="w-4 h-4"></i>
      </div>
      <div class="max-w-2xl bg-slate-900 border border-slate-800 text-slate-200 px-4 py-3 rounded-2xl rounded-tl-none text-xs shadow-md space-y-1">
        ${toolBadge}
        <div class="chat-prose">${parsedContent}</div>
      </div>
    `;
  }

  container.appendChild(msgDiv);
  lucide.createIcons();
}

function appendTypingIndicator() {
  const container = document.getElementById('chat-messages');
  const id = 'typing-' + Date.now();
  const div = document.createElement('div');
  div.id = id;
  div.className = 'flex justify-start space-x-3';
  div.innerHTML = `
    <div class="w-7 h-7 rounded-lg bg-slate-800 border border-slate-700 flex items-center justify-center text-indigo-400 shrink-0">
      <i data-lucide="bot" class="w-4 h-4"></i>
    </div>
    <div class="bg-slate-900 border border-slate-800 px-4 py-3 rounded-2xl rounded-tl-none text-xs flex items-center space-x-1.5 text-slate-400">
      <span class="w-2 h-2 rounded-full bg-indigo-400 animate-bounce"></span>
      <span class="w-2 h-2 rounded-full bg-indigo-400 animate-bounce" style="animation-delay: 0.2s"></span>
      <span class="w-2 h-2 rounded-full bg-indigo-400 animate-bounce" style="animation-delay: 0.4s"></span>
      <span class="ml-2 text-[11px] text-slate-500">Gemini analyzing MoEngage data...</span>
    </div>
  `;
  container.appendChild(div);
  return id;
}

function removeTypingIndicator(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

async function clearChatHistory() {
  await fetch('/api/agent/clear', { method: 'POST' });
  await loadChatHistory();
  showToast('Chat history cleared');
}

function scrollChatToBottom() {
  const container = document.getElementById('chat-messages');
  if (container) {
    container.scrollTop = container.scrollHeight;
  }
}

// MoEngage Explorer Data
async function loadExplorerData() {
  try {
    const [campRes, segRes, anaRes, jnyRes] = await Promise.all([
      fetch('/api/moengage/campaigns'),
      fetch('/api/moengage/segments'),
      fetch('/api/moengage/analytics'),
      fetch('/api/moengage/journeys')
    ]);

    appState.campaigns = await campRes.json();
    appState.segments = await segRes.json();
    appState.analytics = await anaRes.json();
    appState.journeys = await jnyRes.json();

    renderExplorerCampaigns();
    renderExplorerSegments();
    renderExplorerAnalytics();
    renderExplorerJourneys();
  } catch (err) {
    console.error('Failed to load explorer data:', err);
  }
}

function switchExplorerTab(subtab) {
  appState.currentExplorerSubtab = subtab;
  ['campaigns', 'segments', 'journeys', 'analytics'].forEach(s => {
    const btn = document.getElementById(`subtab-btn-${s}`);
    const view = document.getElementById(`subtab-${s}`);
    if (btn && view) {
      if (s === subtab) {
        btn.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600/20 text-indigo-300 border border-indigo-500/30 flex items-center space-x-1.5';
        view.classList.remove('hidden');
      } else {
        btn.className = 'px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-400 hover:text-slate-200 flex items-center space-x-1.5';
        view.classList.add('hidden');
      }
    }
  });

  if (subtab === 'analytics') {
    renderFunnelChart();
  } else if (subtab === 'journeys') {
    renderExplorerJourneys();
  }
  lucide.createIcons();
}

function renderExplorerJourneys() {
  const journeys = appState.journeys || [];
  const activeJourney = journeys.find(j => j.id === 'jny_reactivation_warmup_30d') || journeys[0];
  if (!activeJourney) return;

  const flow = activeJourney.flow || {};
  const schedule = flow.step_3_split?.branch_yes?.schedule || [];
  const grid = document.getElementById('warmup-schedule-grid');
  if (!grid) return;

  grid.innerHTML = schedule.map(pn => `
    <div class="p-3.5 rounded-xl border border-slate-800 bg-slate-950/70 hover:border-slate-700 transition-all flex flex-col justify-between space-y-2.5">
      <div class="space-y-1.5">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-1.5">
            <span class="px-2 py-0.5 rounded text-[10px] font-bold font-mono bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">
              DAY ${pn.day} • PN ${pn.pn_number}
            </span>
            <span class="text-[11px] text-slate-400 font-mono">${pn.timing}</span>
          </div>
          <span class="text-[10px] px-2 py-0.2 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 uppercase">
            Push
          </span>
        </div>
        <h5 class="font-bold text-xs text-white">${pn.title}</h5>
        <p class="text-[11px] text-slate-300 leading-relaxed bg-slate-900/60 p-2.5 rounded-lg border border-slate-800/80">
          ${pn.body}
        </p>
      </div>

      <div class="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px]">
        <span class="text-slate-500 flex items-center space-x-1">
          <i data-lucide="corner-down-right" class="w-3 h-3"></i>
          <span>CTA: <strong class="text-slate-300">${pn.cta}</strong></span>
        </span>
        <button onclick="copyToClipboard('${escapeJs(pn.body)}')" class="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300 flex items-center space-x-1 transition-colors cursor-pointer">
          <i data-lucide="copy" class="w-3 h-3"></i>
          <span>Copy</span>
        </button>
      </div>
    </div>
  `).join('');

  lucide.createIcons();
}

async function runJourneySimulation() {
  const btn = document.getElementById('btn-sim-journey');
  const originalText = btn.innerHTML;
  btn.innerHTML = '<i data-lucide="loader-2" class="w-4 h-4 animate-spin"></i><span>Simulating 1,000 Users...</span>';
  btn.disabled = true;

  try {
    const res = await fetch('/api/moengage/journeys/simulate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sample_size: 1000 })
    });
    const data = await res.json();

    // Show simulation result container
    const container = document.getElementById('journey-sim-result');
    container.classList.remove('hidden');

    document.getElementById('sim-timestamp').innerText = new Date().toLocaleTimeString();
    document.getElementById('sim-enrolled').innerText = `${data.users_enrolled.toLocaleString()} users`;
    document.getElementById('sim-opened').innerText = `${data.app_opened_yes.toLocaleString()} users (${data.app_opened_pct}%)`;
    document.getElementById('sim-warmup-pns').innerText = `${data.warmup_notifications_scheduled.toLocaleString()} PNs`;
    document.getElementById('sim-exited').innerText = `${data.no_activity_exited.toLocaleString()} users (${data.exit_pct}%)`;

    // Populate traces table
    const tbody = document.getElementById('sim-trace-table-body');
    tbody.innerHTML = (data.sample_traces || []).map(t => {
      const opened = t.app_opened;
      return `
        <tr class="hover:bg-slate-900/50 transition-colors">
          <td class="py-2.5 px-3 font-mono text-slate-300">${t.user_id}</td>
          <td class="py-2.5 px-3 text-slate-400">PN sent 30D, 0 opens</td>
          <td class="py-2.5 px-3">
            <span class="px-2 py-0.5 rounded text-[10px] font-semibold ${opened ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30' : 'bg-slate-800 text-slate-400'}">
              ${opened ? `App Opened (Day ${t.day_opened})` : 'No Activity (0 Opens)'}
            </span>
          </td>
          <td class="py-2.5 px-3 text-slate-200">
            ${opened ? `<span class="text-emerald-300 font-medium">⚡️ ${t.action_taken}</span>` : `<span class="text-slate-500">${t.action_taken}</span>`}
          </td>
        </tr>
      `;
    }).join('');

    showToast('Journey simulation complete! 165 reactivated users enrolled in warm-up sequence.', 'success');
    container.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } catch (err) {
    showToast(`Simulation failed: ${err.message}`, 'error');
  } finally {
    btn.innerHTML = originalText;
    btn.disabled = false;
    lucide.createIcons();
  }
}

function renderExplorerCampaigns() {
  const tbody = document.getElementById('explorer-campaigns-table');
  tbody.innerHTML = appState.campaigns.map(c => `
    <tr class="hover:bg-slate-800/40 transition-colors">
      <td class="py-3 px-4 font-medium text-white">${c.name}</td>
      <td class="py-3 px-4">
        <span class="px-2 py-0.5 rounded text-[10px] uppercase font-mono ${c.channel === 'Push' ? 'bg-amber-500/20 text-amber-300' : 'bg-blue-500/20 text-blue-300'}">
          ${c.channel}
        </span>
      </td>
      <td class="py-3 px-4 text-slate-400">${c.target_segment}</td>
      <td class="py-3 px-4">${c.delivery_rate}%</td>
      <td class="py-3 px-4 text-indigo-300 font-bold">${c.ctr}%</td>
      <td class="py-3 px-4">${(c.conversions || 0).toLocaleString()} (${c.conversion_rate}%)</td>
      <td class="py-3 px-4 text-emerald-400 font-medium">$${(c.revenue_generated || 0).toLocaleString()}</td>
    </tr>
  `).join('');
}

function renderExplorerSegments() {
  const grid = document.getElementById('explorer-segments-grid');
  grid.innerHTML = appState.segments.map(s => `
    <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/60 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-slate-800 text-slate-300">${s.type || 'Custom'}</span>
        <span class="text-xs text-cyan-400 font-medium">~${(s.estimated_reach || 0).toLocaleString()} users</span>
      </div>
      <h4 class="font-bold text-sm text-white">${s.name}</h4>
      <p class="text-xs text-slate-400">${s.description || 'No description'}</p>
      <pre class="p-2 rounded bg-slate-950 text-[10px] font-mono text-slate-300 overflow-x-auto">${JSON.stringify(s.criteria, null, 2)}</pre>
    </div>
  `).join('');
}

function renderExplorerAnalytics() {
  const a = appState.analytics;
  if (!a) return;

  const statsContainer = document.getElementById('analytics-stats-grid');
  statsContainer.innerHTML = `
    <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/60 space-y-1">
      <p class="text-xs text-slate-400">Total Active Tracked Users</p>
      <p class="text-xl font-bold text-white">${(a.active_users_count || 0).toLocaleString()}</p>
      <p class="text-[11px] text-emerald-400">Timeframe: ${a.timeframe}</p>
    </div>
    <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/60 space-y-1">
      <p class="text-xs text-slate-400">Overall View to Purchase</p>
      <p class="text-xl font-bold text-indigo-400">${a.conversion_funnels?.overall_view_to_purchase_rate || 0}%</p>
      <p class="text-[11px] text-slate-400">Industry benchmark: 2.8%</p>
    </div>
    <div class="p-4 rounded-xl border border-slate-800 bg-slate-900/60 space-y-1">
      <p class="text-xs text-slate-400">Primary Drop-off Stage</p>
      <p class="text-xl font-bold text-rose-400">${a.top_dropoff_points?.[0]?.stage || 'Cart'}</p>
      <p class="text-[11px] text-rose-300">${a.top_dropoff_points?.[0]?.dropoff_pct || 0}% drop rate</p>
    </div>
  `;
}

function renderFunnelChart() {
  const ctx = document.getElementById('funnelChart');
  if (!ctx) return;

  if (appState.funnelChartInstance) {
    appState.funnelChartInstance.destroy();
  }

  const events = appState.analytics?.events_breakdown || [];
  const labels = events.map(e => e.event);
  const dataCounts = events.map(e => e.count);

  appState.funnelChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Event Volume (Last 7 Days)',
        data: dataCounts,
        backgroundColor: [
          'rgba(99, 102, 241, 0.7)',
          'rgba(59, 130, 246, 0.7)',
          'rgba(14, 165, 233, 0.7)',
          'rgba(20, 184, 166, 0.7)',
          'rgba(16, 185, 129, 0.7)',
          'rgba(244, 63, 94, 0.7)'
        ],
        borderRadius: 8
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false }
      },
      scales: {
        y: {
          grid: { color: 'rgba(51, 65, 85, 0.3)' },
          ticks: { color: '#94a3b8' }
        },
        x: {
          grid: { display: false },
          ticks: { color: '#94a3b8' }
        }
      }
    }
  });
}

// Settings & Session Form
async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    const data = await res.json();

    document.getElementById('setting-cookies').value = data.moengage_cookies || '';
    document.getElementById('setting-region').value = data.moengage_region || 'dashboard-01.moengage.com';
    document.getElementById('setting-appid').value = data.moengage_app_id || '';
    document.getElementById('setting-geminikey').value = data.gemini_api_key || '';
    document.getElementById('setting-geminimodel').value = data.gemini_model || 'gemini-2.5-flash';
    document.getElementById('setting-schedtime').value = data.schedule_time || '09:00';
    document.getElementById('setting-schedenabled').checked = data.schedule_enabled === 'true';
    document.getElementById('setting-mockmode').checked = data.mock_mode === 'true';
  } catch (err) {
    console.error('Failed to load settings:', err);
  }
}

async function handleSettingsSave(e) {
  e.preventDefault();
  const payload = {
    moengage_cookies: document.getElementById('setting-cookies').value.trim(),
    moengage_region: document.getElementById('setting-region').value.trim(),
    moengage_app_id: document.getElementById('setting-appid').value.trim(),
    gemini_api_key: document.getElementById('setting-geminikey').value.trim(),
    gemini_model: document.getElementById('setting-geminimodel').value.trim(),
    schedule_time: document.getElementById('setting-schedtime').value.trim(),
    schedule_enabled: document.getElementById('setting-schedenabled').checked ? 'true' : 'false',
    mock_mode: document.getElementById('setting-mockmode').checked ? 'true' : 'false'
  };

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success) {
      showToast('Settings saved successfully!', 'success');
      await fetchStatus();
    }
  } catch (err) {
    showToast(`Error saving settings: ${err.message}`, 'error');
  }
}

async function testConnection() {
  const box = document.getElementById('test-result-box');
  box.classList.remove('hidden');
  box.className = 'p-4 rounded-xl text-xs space-y-1 bg-slate-900 border border-slate-700 text-slate-300';
  box.innerHTML = 'Testing connection to MoEngage cluster...';

  // Save settings first
  await handleSettingsSave(new Event('submit'));

  try {
    const res = await fetch('/api/auth/test', { method: 'POST' });
    const result = await res.json();

    if (result.valid) {
      box.className = 'p-4 rounded-xl text-xs space-y-1 bg-emerald-950/60 border border-emerald-700 text-emerald-200';
      box.innerHTML = `
        <div class="font-bold flex items-center space-x-1.5 text-emerald-300">
          <i data-lucide="check-circle" class="w-4 h-4"></i>
          <span>MoEngage Connection Successful (${result.mode === 'mock' ? 'Mock/Demo' : 'Live'})</span>
        </div>
        <p>${result.message}</p>
        <p class="font-mono text-[11px] text-emerald-400">Workspace: ${result.workspace || 'N/A'} | Region: ${result.region}</p>
      `;
    } else {
      box.className = 'p-4 rounded-xl text-xs space-y-1 bg-rose-950/60 border border-rose-700 text-rose-200';
      box.innerHTML = `
        <div class="font-bold flex items-center space-x-1.5 text-rose-300">
          <i data-lucide="alert-circle" class="w-4 h-4"></i>
          <span>Connection Failed</span>
        </div>
        <p>${result.message}</p>
      `;
    }
    lucide.createIcons();
    await fetchStatus();
  } catch (err) {
    box.className = 'p-4 rounded-xl text-xs space-y-1 bg-rose-950/60 border border-rose-700 text-rose-200';
    box.innerText = `Error: ${err.message}`;
  }
}

// Modal helper
function openCreateSegmentModal() {
  document.getElementById('create-segment-modal').classList.remove('hidden');
  document.getElementById('modal-seg-name').value = '';
  document.getElementById('modal-seg-desc').value = '';
  document.getElementById('modal-seg-criteria').value = JSON.stringify({
    "event_filter": "Product Viewed >= 3 in last 48 hours",
    "exclusion_filter": "Added to Cart >= 1 in last 48 hours"
  }, null, 2);
  lucide.createIcons();
}

function closeCreateSegmentModal() {
  document.getElementById('create-segment-modal').classList.add('hidden');
}

async function handleSegmentSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('modal-seg-name').value.trim();
  const desc = document.getElementById('modal-seg-desc').value.trim();
  let criteria = {};
  try {
    criteria = JSON.parse(document.getElementById('modal-seg-criteria').value);
  } catch (err) {
    showToast('Invalid JSON in criteria field', 'error');
    return;
  }

  try {
    const res = await fetch('/api/moengage/segments/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, description: desc, criteria })
    });
    const result = await res.json();
    if (result.success) {
      showToast(`Segment "${name}" created!`, 'success');
      closeCreateSegmentModal();
      await loadExplorerData();
      await loadDailyData();
    }
  } catch (err) {
    showToast(`Error creating segment: ${err.message}`, 'error');
  }
}

// Utilities
function copyToClipboard(text) {
  navigator.clipboard.writeText(text).then(() => {
    showToast('Campaign copy copied to clipboard!', 'success');
  }).catch(() => {
    showToast('Failed to copy text', 'error');
  });
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function escapeJs(str) {
  if (!str) return '';
  return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"').replace(/\n/g, '\\n');
}
