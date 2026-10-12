/**
 * ADROIT ATS - Multi-Consultant US IT Staffing & 1-Click Outreach Platform
 * Complete Dashboard Controller & Interactive Engine
 */

let state = {
    consultants: [],
    activeConsultantId: 1,
    jobs: [],
    students: [],
    studentsLoaded: false,
    pipeline: {},
    activeTab: 'jobs',
    lastOptimizedResumeText: '',
    lastOptimizedDocxBase64: '',
    lastOptimizedCandidateName: 'Consultant'
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initConsultants();
    initJobsTable();
    initModals();
    initResumeBot();
    initStudentsTab();
    initEducationFilters();
    initSourcingTracker();
    checkUrlAuthParams();
});

function checkUrlAuthParams() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('auth_success')) {
        showToast('Gmail successfully connected for consultant!', 'success');
        window.history.replaceState({}, document.title, window.location.pathname);
    }
}

// =========================================================================
// 1. Navigation & Tab Switching
// =========================================================================
function initNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabId = item.getAttribute('data-tab');
            switchTab(tabId);
        });
    });

    // Hash support
    if (window.location.hash) {
        const hashTab = window.location.hash.replace('#', '');
        switchTab(hashTab);
    } else {
        switchTab('dashboard');
    }
}

const tabAliasMap = {
    'dashboard': 'dashboard',
    'candidates': 'consultants',
    'consultants': 'consultants',
    'jobs': 'jobs',
    'sourcing': 'students',
    'students': 'students',
    'reporting': 'drafts',
    'drafts': 'drafts',
    'resumebot': 'resumebot',
    'vendors': 'vendors',
    'settings': 'team',
    'team': 'team',
    'resumebot': 'resumebot'
};

function switchTab(rawTabId) {
    const paneKey = tabAliasMap[rawTabId] || rawTabId;
    state.activeTab = rawTabId;

    document.querySelectorAll('.nav-item').forEach(el => {
        const dt = el.getAttribute('data-tab');
        if (dt === rawTabId || tabAliasMap[dt] === paneKey) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });

    document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
    const activePane = document.getElementById(`tab-${paneKey}`) || document.getElementById(`tab-${rawTabId}`);
    if (activePane) activePane.classList.add('active');

    if (paneKey === 'dashboard') {
        if (typeof renderDashboardPipeline === 'function') renderDashboardPipeline();
    } else if (paneKey === 'consultants') {
        if (typeof renderConsultantsTable === 'function') renderConsultantsTable();
    } else if (paneKey === 'drafts') {
        loadPipeline();
    } else if (paneKey === 'resumebot') {
        if (typeof window.refreshResumeBotSource === 'function') window.refreshResumeBotSource(true);
    } else if (paneKey === 'team') {
        loadRecruiters();
        loadUnmappedInstitutions();
    } else if (paneKey === 'students') {
        eduOnTabOpen();
    } else if (paneKey === 'vendors') {
        loadVendors();
    } else if (paneKey === 'jobs') {
        if (!state.jobs || state.jobs.length === 0) {
            searchJobs(false);
        }
    }
}

// =========================================================================
// 2. US Bench Consultants Hub
// =========================================================================
async function initConsultants() {
    await fetchConsultants();

    const globalSelect = document.getElementById('global-active-consultant');
    if (globalSelect) {
        globalSelect.addEventListener('change', (e) => {
            state.activeConsultantId = parseInt(e.target.value);
            updateActiveConsultantUI();
            updateTableConsultantSelects();
        });
    }

    const btnAddTab = document.getElementById('btn-add-consultant-tab');
    if (btnAddTab) {
        btnAddTab.addEventListener('click', () => openConsultantModal());
    }

    const filterInput = document.getElementById('filter-consultants-search');
    if (filterInput) {
        filterInput.addEventListener('input', (e) => {
            const q = e.target.value.toLowerCase().trim();
            const rows = document.querySelectorAll('#consultants-table-body tr');
            rows.forEach(row => {
                const text = row.innerText.toLowerCase();
                row.style.display = (!q || text.includes(q)) ? '' : 'none';
            });
            const cards = document.querySelectorAll('#consultants-cards-container .consultant-card');
            cards.forEach(card => {
                const text = card.innerText.toLowerCase();
                card.style.display = (!q || text.includes(q)) ? '' : 'none';
            });
        });
    }

    const btnHeaderAdd = document.getElementById('btn-open-add-consultant');
    if (btnHeaderAdd) {
        btnHeaderAdd.addEventListener('click', () => openConsultantModal());
    }

    const btnHeaderScrape = document.getElementById('btn-header-scrape-us');
    if (btnHeaderScrape) {
        btnHeaderScrape.addEventListener('click', () => triggerUsScrape());
    }

    const btnHeaderPasteDraft = document.getElementById('btn-open-paste-draft-modal');
    if (btnHeaderPasteDraft) {
        btnHeaderPasteDraft.addEventListener('click', () => openPasteDraftModal(state.activeConsultantId));
    }
}

async function fetchConsultants(recruiterId = null) {
    try {
        const filterEl = document.getElementById('select-consultant-recruiter-filter');
        const rId = recruiterId !== null ? recruiterId : (filterEl ? filterEl.value : '');
        const url = rId ? `/api/consultants?recruiter_id=${encodeURIComponent(rId)}` : '/api/consultants';
        const res = await fetch(url);
        const data = await res.json();
        state.consultants = Array.isArray(data) ? data : [];
        populateConsultantDropdowns();
        updateActiveConsultantUI();
        refreshJobConsultantSelects();
        if (typeof renderConsultantsTable === 'function') renderConsultantsTable();
        renderConsultantsGrid();
        if (typeof renderDashboardPipeline === 'function') renderDashboardPipeline();
    } catch (err) {
        console.error('Error fetching consultants:', err);
    }
}

function populateConsultantDropdowns() {
    const globalSelect = document.getElementById('global-active-consultant');
    const resumeSelect = document.getElementById('resumebot-consultant-select');
    const pdSelect = document.getElementById('pd-consultant-select');

    if (globalSelect) {
        globalSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})
            </option>`
        ).join('');
    }

    if (pdSelect) {
        pdSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})${c.target_rate ? ' - ' + escapeHtml(c.target_rate) : ''}
            </option>`
        ).join('');
    }

    if (resumeSelect) {
        resumeSelect.innerHTML = state.consultants.map(c => 
            `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
                ${escapeHtml(c.name)} - ${escapeHtml(c.title || 'Consultant')}
            </option>`
        ).join('');
    }
}

function updateActiveConsultantUI() {
    const active = state.consultants.find(c => c.id === state.activeConsultantId) || state.consultants[0];
    if (!active) return;

    state.activeConsultantId = active.id;

    const gmailBadge = document.getElementById('active-gmail-badge');
    if (gmailBadge) {
        if (active.gmail_connected) {
            gmailBadge.innerHTML = `🟢 ${escapeHtml(active.gmail_account || 'Gmail Connected')}`;
            gmailBadge.style.color = '#34d399';
            gmailBadge.style.background = 'rgba(52, 211, 153, 0.15)';
            gmailBadge.style.border = '1px solid rgba(52, 211, 153, 0.35)';
        } else {
            gmailBadge.innerHTML = `⚠️ Connect Gmail`;
            gmailBadge.style.color = '#fbbf24';
            gmailBadge.style.background = 'rgba(251, 191, 36, 0.15)';
            gmailBadge.style.border = '1px solid rgba(251, 191, 36, 0.35)';
        }
    }

    const rateBadge = document.getElementById('active-rate-badge');
    if (rateBadge) {
        rateBadge.innerText = active.target_rate || '';
    }
}

// Job rows are often rendered before the consultant list has loaded (both load at startup, the
// jobs answer can come first): their Target Candidate dropdowns were then left EMPTY for good, so
// 1-Click Draft / Optimize Resume had no consultant. Refill them whenever consultants load.
function refreshJobConsultantSelects() {
    const options = (state.consultants || []).map(c =>
        `<option value="${c.id}">${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})</option>`).join('');
    document.querySelectorAll('.job-consultant-select').forEach(sel => {
        const keep = sel.value;
        sel.innerHTML = options;
        const want = keep && (state.consultants || []).some(c => String(c.id) === keep) ? keep : String(state.activeConsultantId);
        sel.value = want;
    });
}

function updateTableConsultantSelects() {
    document.querySelectorAll('.job-consultant-select').forEach(sel => {
        sel.value = state.activeConsultantId;
    });
}

function renderConsultantsGrid() {
    const container = document.getElementById('consultants-cards-container');
    if (!container) return;

    if (!state.consultants || state.consultants.length === 0) {
        container.innerHTML = `
            <div style="grid-column: 1 / -1; text-align:center; padding: 40px; color: var(--text-muted); background: var(--card-bg); border-radius: 12px;">
                <p>No consultants found. Click <strong>"Add New Consultant Profile"</strong> to add your first bench candidate.</p>
            </div>`;
        return;
    }

    container.innerHTML = state.consultants.map(c => {
        const initials = c.name.split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const hasResume = Boolean(c.resume_filename || c.resume_path);
        const gmailConnected = Boolean(c.gmail_connected);
        const cleanName = (c.name || "Consultant").replace(/[,\/]/g, '').trim();
        const liUrl = (c.linkedin_url && c.linkedin_url.startsWith('http') && !c.linkedin_url.endsWith('-devops/') && !c.linkedin_url.endsWith('-data-analyst/'))
            ? c.linkedin_url
            : `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}&origin=GLOBAL_SEARCH_HEADER`;

        return `
        <div class="consultant-card" data-id="${c.id}">
            <div class="cand-header">
                <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                    <div class="cand-avatar" style="cursor:pointer;" title="Click to view LinkedIn">${initials}</div>
                </a>
                <div class="cand-details">
                    <h3>
                        <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:6px;" onmouseover="this.style.color='#38bdf8'" onmouseout="this.style.color='inherit'" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                            ${escapeHtml(c.name)}
                            <span title="View LinkedIn Profile" style="color:#38bdf8; font-size:12px;">↗</span>
                        </a>
                    </h3>
                    <div class="cand-title">${escapeHtml(c.title || 'Technical Consultant')}</div>
                </div>
            </div>

            <div class="cand-meta-row">
                <span class="meta-chip green">${escapeHtml(c.target_rate || '—')}</span>
                <span class="meta-chip">${escapeHtml(c.visa_status || (c.country === 'India' ? 'India-based' : 'Visa not set'))}</span>
                <span class="meta-chip">${c.experience_years ? c.experience_years + '+ Yrs Exp' : 'Exp not set'}</span>
                <span class="meta-chip">${escapeHtml(c.location || c.country || '—')}</span>
            </div>

            ${c.recruiter_name ? `
            <div style="font-size: 0.75rem; color: #94a3b8; margin: 6px 0 10px; display: flex; align-items: center; justify-content: space-between; background: rgba(30, 41, 59, 0.6); padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.06);">
                <span>👤 Recruiter: <strong style="color: #38bdf8;">${escapeHtml(c.recruiter_name)}</strong></span>
                <button class="btn-trigger-reassign" data-id="${c.id}" data-name="${escapeHtml(c.name)}" style="background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.3); color: #c084fc; border-radius: 4px; padding: 2px 7px; cursor: pointer; font-size: 0.72rem; font-weight: 600;">Transfer</button>
            </div>` : ''}

            <div class="cand-skills-box">
                <strong>Primary Skills:</strong> ${escapeHtml(c.primary_skills || 'Full Stack Engineering, Cloud Services')}
            </div>

            <div class="cand-integrations">
                <div class="integration-item">
                    <span><strong>Gmail Mailbox:</strong></span>
                    ${gmailConnected ? 
                        `<span style="color: #34d399; font-weight: 600;">✓ Connected (${escapeHtml(c.gmail_account || 'Active')})</span> ${gmailManageButtons(c)}` : 
                        `<div>
                            <button class="btn btn-success btn-xs btn-open-app-pass" data-id="${c.id}" data-name="${escapeHtml(c.name)}" data-email="${escapeHtml(c.email || '')}">🔑 App Password</button>
                            ${window.HAS_GOOGLE_OAUTH ? `<a href="/api/consultants/${c.id}/connect-gmail" class="btn btn-outline-primary btn-xs" style="margin-left:4px;">OAuth</a>` : ''}
                         </div>`
                    }
                </div>
                <div class="integration-item">
                    <span><strong>Master Resume:</strong></span>
                    ${hasResume ? 
                        `<span style="color: #a5b4fc;">📄 ${escapeHtml(c.resume_filename || 'Resume Attached')}</span>` : 
                        `<button class="btn btn-secondary btn-xs btn-upload-cand-resume" data-id="${c.id}" data-name="${escapeHtml(c.name)}">Upload .docx</button>`
                    }
                </div>
            </div>

            <div class="cand-card-actions">
                <a href="${liUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="display:inline-flex; align-items:center; gap:4px; text-decoration:none; background:rgba(14, 118, 168, 0.2); color:#38bdf8; border:1px solid rgba(56, 189, 248, 0.35);" onclick="event.stopPropagation(); window.open('${liUrl}', '_blank'); return true;">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19 3a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h14m-.5 15.5v-5.3a3.26 3.26 0 0 0-3.26-3.26c-.85 0-1.84.52-2.28 1.3v-1.11h-2.79v8.37h2.79v-4.93c0-.77.62-1.4 1.39-1.4a1.4 1.4 0 0 1 1.4 1.4v4.93h2.75M6.46 10.9v8.37H9.2V10.9H6.46M7.83 6.27a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2Z"/></svg>
                    LinkedIn ↗
                </a>
                <button class="btn btn-success btn-sm btn-paste-draft-cand" data-id="${c.id}" data-name="${escapeHtml(c.name)}">
                    ✉️ Paste JD & Draft
                </button>
                <button class="btn btn-primary btn-sm btn-find-jobs-cand" data-id="${c.id}" data-skills="${escapeHtml(c.primary_skills || '')}">
                    🎯 Find 24h Jobs
                </button>
                <button class="btn btn-secondary btn-sm btn-edit-cand" data-id="${c.id}">
                    Edit Profile
                </button>
            </div>
        </div>
        `;
    }).join('');

    // Attach card event listeners
    container.querySelectorAll('.btn-paste-draft-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            openPasteDraftModal(candId);
        });
    });

    container.querySelectorAll('.btn-find-jobs-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const skills = btn.getAttribute('data-skills') || '';
            state.activeConsultantId = candId;
            updateActiveConsultantUI();
            
            const firstSkill = skills.split(',')[0].trim() || 'Software Engineer';
            const searchInput = document.getElementById('filter-query');
            if (searchInput) searchInput.value = firstSkill;
            
            // Switch to jobs tab and trigger search
            switchTab('jobs');
            setTimeout(() => {
                searchJobs(false);
            }, 100);
        });
    });

    container.querySelectorAll('.btn-edit-cand').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const c = state.consultants.find(cand => cand.id === candId);
            if (c) openConsultantModal(c);
        });
    });

    container.querySelectorAll('.btn-upload-cand-resume').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            openUploadResumeModal(candId, name);
        });
    });

    container.querySelectorAll('.btn-open-app-pass').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            const email = btn.getAttribute('data-email');
            openAppPasswordModal(candId, name, email);
        });
    });
}

// US-based consultants: one option per real US status (H1B and H1B Transfer, OPT, STEM OPT and CPT
// are different statuses). India-based consultants have no visa question at all - the field is
// hidden and nothing is saved for it. Older saved values (e.g. "OPT/CPT") stay selectable as
// "(previously saved)" instead of being dropped.
const US_VISA_OPTIONS = ["US Citizen", "Green Card", "H1B", "H1B Transfer", "H4 EAD", "L1", "L2", "OPT", "STEM OPT", "CPT", "TN", "B1/B2"];

function syncVisaOptionsForCountry(currentValue = '') {
    const countrySelect = document.getElementById('c-country');
    const visaSelect = document.getElementById('c-visa');
    const visaLabel = document.getElementById('c-visa-label');
    if (!visaSelect) return;
    const country = countrySelect ? countrySelect.value : '';

    const visaGroup = document.getElementById('c-visa-group');
    const rateLabel = document.getElementById('c-rate-label');
    const rateInput = document.getElementById('c-rate');
    const india = country === 'India';
    // India: salaries, not $/hr C2C rates; and no visa / relocation question.
    if (rateLabel) rateLabel.innerText = india ? 'Expected Salary (CTC)' : 'Target Rate ($/hr) *';
    if (rateInput) rateInput.placeholder = india ? 'e.g. ₹12 LPA' : 'e.g. $65/hr (C2C)';
    if (visaGroup) visaGroup.style.display = india ? 'none' : '';
    visaSelect.required = !india;
    if (india) {
        visaSelect.innerHTML = '<option value="" selected></option>';
        return;
    }

    if (!country) {
        visaSelect.innerHTML = `<option value="" disabled selected>-- Select country first --</option>`;
        if (visaLabel) visaLabel.innerText = 'Visa / Work Authorization *';
        return;
    }

    const options = US_VISA_OPTIONS;
    if (visaLabel) visaLabel.innerText = 'Visa / Work Authorization *';

    let html = `<option value="" disabled ${!currentValue ? 'selected' : ''}>-- Select visa / work authorization --</option>`;
    html += options.map(o => `<option value="${escapeHtml(o)}" ${o === currentValue ? 'selected' : ''}>${escapeHtml(o)}</option>`).join('');
    // A saved value from before this consultant's Country was last changed (or from the old
    // removed "Outside USA" option) won't match either list - keep it as its own option
    // instead of silently discarding real, already-entered data.
    if (currentValue && !options.includes(currentValue)) {
        html += `<option value="${escapeHtml(currentValue)}" selected>${escapeHtml(currentValue)} (previously saved)</option>`;
    }
    visaSelect.innerHTML = html;
}

function openConsultantModal(cand = null) {
    const modal = document.getElementById('modal-consultant');
    const form = document.getElementById('form-consultant');
    const title = document.getElementById('modal-consultant-title');
    const editIdInput = document.getElementById('consultant-edit-id');

    if (form) form.reset();

    if (cand) {
        if (title) title.innerText = `Edit Profile: ${cand.name}`;
        if (editIdInput) editIdInput.value = cand.id;
        document.getElementById('c-name').value = cand.name || '';
        document.getElementById('c-email').value = cand.email || '';
        document.getElementById('c-phone').value = cand.phone || '';
        document.getElementById('c-title').value = cand.title || '';
        document.getElementById('c-skills').value = cand.primary_skills || '';
        document.getElementById('c-exp').value = cand.experience_years || '';
        document.getElementById('c-rate').value = cand.target_rate || '';
        document.getElementById('c-location').value = cand.location || 'United States';
        document.getElementById('c-country').value = cand.country || 'United States';
        syncVisaOptionsForCountry(cand.visa_status || '');
        document.getElementById('c-summary').value = cand.resume_summary || cand.summary || '';
    } else {
        if (title) title.innerText = 'Add New US Bench Consultant';
        if (editIdInput) editIdInput.value = '';
        syncVisaOptionsForCountry('');
    }

    if (modal) modal.style.display = 'flex';
}

function closeConsultantModal() {
    const modal = document.getElementById('modal-consultant');
    if (modal) modal.style.display = 'none';
}

async function deleteConsultant(candId, candName) {
    if (!confirm(`Are you sure you want to delete "${candName}"?\n\nThis removes their profile, resume, and pipeline history. This cannot be undone.`)) {
        return;
    }
    try {
        const res = await fetch(`/api/consultants/${candId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`${candName} deleted.`, 'success');
            await fetchConsultants();
        } else {
            showToast(data.error || 'Failed to delete consultant.', 'error');
        }
    } catch (err) {
        showToast('Error deleting consultant: ' + err.message, 'error');
    }
}

function openUploadResumeModal(candId, candName) {
    document.getElementById('upload-candidate-id').value = candId;
    document.getElementById('upload-candidate-name-label').innerText = `Uploading resume for ${candName}`;
    const modal = document.getElementById('modal-upload-resume');
    if (modal) modal.style.display = 'flex';
}

function closeUploadResumeModal() {
    const modal = document.getElementById('modal-upload-resume');
    if (modal) modal.style.display = 'none';
}

// A connected Gmail can be changed (connect another one - it replaces the old one once Google
// accepts the new App Password) or disconnected (forgets the address, App Password and OAuth token).
function gmailManageButtons(c) {
    return `<button type="button" class="btn-change-gmail" title="Connect a different Gmail for this consultant" onclick="changeConsultantGmail(${c.id})" style="display:inline-flex; align-items:center; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; cursor:pointer; background:#fff; white-space:nowrap; color:#2563eb; border:1px solid #bfdbfe;">Change Gmail</button>` +
        `<button type="button" class="btn-disconnect-gmail" title="Remove the connected Gmail" onclick="disconnectConsultantGmail(${c.id})" style="display:inline-flex; align-items:center; padding:2px 8px; border-radius:6px; font-size:0.72rem; font-weight:600; cursor:pointer; background:#fff; white-space:nowrap; color:#b91c1c; border:1px solid #fecaca;">Disconnect</button>`;
}

function changeConsultantGmail(candId) {
    const c = (state.consultants || []).find(x => x.id === candId);
    openAppPasswordModal(candId, c ? c.name : 'this consultant', '');   // empty: type the NEW Gmail
}

async function disconnectConsultantGmail(candId) {
    const c = (state.consultants || []).find(x => x.id === candId);
    const who = c ? c.name : 'this consultant';
    const mail = c && c.gmail_account ? ` (${c.gmail_account})` : '';
    if (!confirm(`Disconnect the Gmail${mail} from ${who}?\n\nDrafts already in that Gmail stay there. You can then connect a different Gmail.`)) return;
    const res = await fetch(`/api/consultants/${candId}/disconnect-gmail`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || 'Could not disconnect the Gmail.', 'error'); return; }
    showToast(`Gmail disconnected from ${who}. Click "App Password" to connect the new Gmail.`, 'success', 6000);
    await fetchConsultants();
}

function openAppPasswordModal(candId, candName, candEmail) {
    document.getElementById('app-pass-cand-id').value = candId;
    document.getElementById('app-pass-cand-name').innerText = candName;
    document.getElementById('app-pass-email').value = candEmail || '';
    document.getElementById('app-pass-key').value = '';
    const modal = document.getElementById('modal-app-password');
    if (modal) modal.style.display = 'flex';
}

function closeAppPasswordModal() {
    const modal = document.getElementById('modal-app-password');
    if (modal) modal.style.display = 'none';
}

function openPasteDraftModal(candId = null) {
    const modal = document.getElementById('modal-paste-draft');
    const form = document.getElementById('form-paste-draft');
    if (form) form.reset();
    pdVendorReset();
    const cloudNote = document.getElementById('pd-cloud-note');
    if (cloudNote) { cloudNote.style.display = 'none'; cloudNote.innerHTML = ''; }
    pdResumeReset();
    pdResetAi();

    const select = document.getElementById('pd-consultant-select');
    if (select && candId) {
        select.value = candId;
    } else if (select && state.activeConsultantId) {
        select.value = state.activeConsultantId;
    }

    if (modal) modal.style.display = 'flex';
}

function closePasteDraftModal() {
    const modal = document.getElementById('modal-paste-draft');
    if (modal) modal.style.display = 'none';
}

// =========================================================================
// 3. Live 24-Hour USA IT Jobs & 1-Click Outreach Table
// =========================================================================
// Market = which country's job boards this tab is pointed at. Each consultant is matched
// only to their own market's jobs (set via browseJobsForCandidate); manual browsing can
// switch it with the Job Market dropdown.
function syncJobsMarketUi() {
    const country = document.getElementById('filter-country')?.value || 'United States';
    const isIndia = country === 'India';
    const locLabel = document.getElementById('filter-location-label');
    const locInput = document.getElementById('filter-location');
    const sourceSelect = document.getElementById('filter-source');
    const descEl = document.getElementById('jobs-tab-source-desc');

    if (locLabel) locLabel.innerText = isIndia ? 'India Location' : 'US Location';
    if (locInput) {
        locInput.placeholder = isIndia ? 'India, Bangalore, Hyderabad, Remote...' : 'United States, Dallas TX, Remote...';
        if (!locInput.value || locInput.value === 'United States' || locInput.value === 'India') {
            locInput.value = country;
        }
    }
    if (sourceSelect) {
        sourceSelect.innerHTML = isIndia
            ? `<option value="All">All India Portals (Naukri, Foundit, LinkedIn)</option>
               <option value="Naukri">Naukri.com</option>
               <option value="Foundit">Foundit / Monster India</option>
               <option value="LinkedIn">LinkedIn (Live)</option>`
            : `<option value="All">All US Portals (LinkedIn, Dice)</option>
               <option value="LinkedIn">LinkedIn (Live 24h)</option>
               <option value="Dice">Dice.com</option>`;
    }
    if (descEl) {
        descEl.innerText = isIndia
            ? 'Scraped fresh requisitions from Naukri, Foundit (Monster India), and LinkedIn India. Edit recruiter emails directly in the table and create 1-click Gmail drafts with attached .docx resumes.'
            : 'Scraped fresh C2C contract requisitions from LinkedIn US and Dice.com. Edit recruiter emails directly in the table and create 1-click Gmail drafts with attached .docx resumes.';
    }
}

function initJobsTable() {
    const btnSearch = document.getElementById('btn-search-jobs');
    const btnLiveScrape = document.getElementById('btn-live-scrape-trigger');
    const queryInput = document.getElementById('filter-query');
    const locInput = document.getElementById('filter-location');
    const sourceSelect = document.getElementById('filter-source');
    const countrySelect = document.getElementById('filter-country');

    if (countrySelect) {
        countrySelect.addEventListener('change', () => { syncJobsMarketUi(); searchJobs(false); });
    }

    if (btnSearch) {
        btnSearch.addEventListener('click', () => searchJobs(false));
    }

    if (btnLiveScrape) {
        btnLiveScrape.addEventListener('click', () => triggerJobScrape());
    }

    if (queryInput) {
        queryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                searchJobs(false);
            }
        });
    }

    if (sourceSelect) {
        sourceSelect.addEventListener('change', () => searchJobs(false));
    }

    // Universal quick filter & navigation in top header
    const quickFilterInput = document.getElementById('quick-job-filter-input');
    if (quickFilterInput) {
        quickFilterInput.addEventListener('input', (e) => {
            const term = e.target.value.toLowerCase().trim();
            // Filter jobs table if on jobs pane
            document.querySelectorAll('#jobs-table-body tr.job-row').forEach(row => {
                const text = row.innerText.toLowerCase();
                row.style.display = (!term || text.includes(term)) ? '' : 'none';
            });
            // Filter candidates table if on candidates pane
            document.querySelectorAll('#tab-consultants tbody tr, #tab-candidates tbody tr').forEach(row => {
                const text = row.innerText.toLowerCase();
                row.style.display = (!term || text.includes(term)) ? '' : 'none';
            });
            // Filter dashboard pipeline if on dashboard
            document.querySelectorAll('#dashboard-pipeline-tbody tr').forEach(row => {
                const text = row.innerText.toLowerCase();
                row.style.display = (!term || text.includes(term)) ? '' : 'none';
            });
        });

        quickFilterInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const term = quickFilterInput.value.trim();
                if (term) {
                    switchTab('jobs');
                    const jobQuery = document.getElementById('filter-query');
                    if (jobQuery) jobQuery.value = term;
                    searchJobs(false);
                }
            }
        });
    }

    // Initial load of jobs
    searchJobs(false);
}

async function searchJobs(liveScrape = false) {
    const tbody = document.getElementById('jobs-table-body');
    const countLabel = document.getElementById('jobs-table-count');
    const query = document.getElementById('filter-query')?.value?.trim() || '';
    const country = document.getElementById('filter-country')?.value || 'United States';
    const location = document.getElementById('filter-location')?.value?.trim() || country;
    const source = document.getElementById('filter-source')?.value || 'All';

    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="loading-cell" style="text-align:center; padding: 30px; color: var(--text-muted);">
                    <div class="spinner" style="display:inline-block; margin-right:8px;"></div>
                    ${liveScrape ? `Scraping fresh 24h ${country} contract jobs across portals...` : `Searching ${country} job requisitions...`}
                </td>
            </tr>`;
    }

    try {
        const res = await fetch('/api/jobs/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                query: query,
                location: location,
                source: source,
                country: country,
                contract_only: true,
                is_24h_only: true,
                live_scrape: liveScrape,
                my_experience: jobsMyExperience(),
                include_unstated: document.getElementById('filter-exp-unstated')?.checked !== false
            })
        });

        const data = await res.json();
        state.jobs = Array.isArray(data) ? data : (data.jobs || data.results || []);

        if (countLabel) {
            countLabel.innerText = `Showing ${state.jobs.length} Fresh ${country} Requisitions`;
        }

        renderJobsTable(state.jobs);
    } catch (err) {
        console.error('Error fetching jobs:', err);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding: 24px; color: #ef4444;">Failed to load jobs. Please try searching again.</td></tr>`;
        }
    }
}

async function triggerUsScrape() {
    showToast('🚀 Running Live 24h US Requisition Scraper (Dice, LinkedIn)...', 'info', 6000);
    const query = document.getElementById('filter-query')?.value?.trim() || 'Software Engineer';
    const location = document.getElementById('filter-location')?.value?.trim() || 'United States';

    try {
        const res = await fetch('/api/jobs/scrape-us', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                keywords: [query],
                location: location,
                contract_only: true
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`✅ Scraped & saved ${data.count || 0} fresh US requisitions!`, 'success');
            searchJobs(false);
        } else {
            showToast(`Scrape completed with notice: ${data.message || 'Ready'}`, 'info');
            searchJobs(false);
        }
    } catch (err) {
        showToast('Error during live scrape: ' + err.message, 'error');
        searchJobs(false);
    }
}

// Respects the Jobs tab's Job Market toggle - India scrapes Naukri/Foundit/LinkedIn,
// anything else scrapes the existing US sources unchanged.
async function triggerJobScrape() {
    const country = document.getElementById('filter-country')?.value || 'United States';
    if (country === 'India') {
        await triggerIndiaScrape();
    } else {
        await triggerUsScrape();
    }
}

async function triggerIndiaScrape() {
    showToast('🚀 Running Live India Requisition Scraper (Naukri, Foundit, LinkedIn)...', 'info', 6000);
    const query = document.getElementById('filter-query')?.value?.trim() || 'Software Engineer';
    const location = document.getElementById('filter-location')?.value?.trim() || 'India';

    try {
        const res = await fetch('/api/jobs/scrape-india', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords: [query], location: location })
        });
        const data = await res.json().catch(() => ({}));
        if (data.success) {
            showToast(`✅ Scraped & saved ${data.count || 0} fresh India requisitions!`, 'success');
        } else {
            showToast(data.error || data.message || 'India scrape did not return results.', data.error ? 'error' : 'info');
        }
    } catch (err) {
        showToast('Error during live India scrape: ' + err.message, 'error');
    } finally {
        searchJobs(false);
    }
}

function renderJobsTable(jobs) {
    const tbody = document.getElementById('jobs-table-body');
    if (!tbody) return;

    if (!jobs || jobs.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" style="text-align:center; padding: 40px; color: var(--text-muted);">
                    No jobs found matching your search. Try adjusting keywords or click <strong>"Live 24h Scrape"</strong> to fetch fresh postings.
                </td>
            </tr>`;
        return;
    }

    const consultantOptions = state.consultants.map(c => 
        `<option value="${c.id}" ${c.id === state.activeConsultantId ? 'selected' : ''}>
            ${escapeHtml(c.name)} (${escapeHtml(c.title || 'Consultant')})
        </option>`
    ).join('');

    tbody.innerHTML = jobs.map(j => {
        const portalClass = (j.source || '').toLowerCase().replace(/[^a-z0-9]/g, '');
        const hasEmail = Boolean(j.recruiter_email);
        const reqUrl = j.url || '#';

        return `
        <tr class="job-row" data-job-id="${j.id}">
            <td>
                <div style="font-weight: 600; color: #0f172a; margin-bottom: 2px;">
                    ${escapeHtml(j.title || 'Untitled requirement')}
                </div>
                <div style="font-size: 0.85rem; color: var(--text-muted); display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
                    ${j.company ? `<span>🏢 ${escapeHtml(j.company)}</span>` : ''}
                    ${j.location ? `<span>📍 ${escapeHtml(j.location)}</span>` : ''}
                    ${j.vendor ? `<span class="vendor-badge" title="${j.vendor.count} of your vendor contacts at ${escapeHtml(j.vendor.company)} will be BCC'd on a draft" style="background:#ecfdf5; color:#065f46; border:1px solid #a7f3d0; border-radius:999px; padding:1px 8px; font-size:0.75rem; font-weight:700;">🤝 Known vendor &middot; ${j.vendor.count}</span>` : ''}
                    ${reqUrl && reqUrl !== '#' ? `<a href="${reqUrl}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8; text-decoration:none;" title="Open original job posting">View Req ↗</a>` : ''}
                </div>
            </td>
            <td>
                <span class="portal-badge badge-${portalClass}">${escapeHtml(j.source || 'Portal')}</span>
            </td>
            <td>
                <span style="color: #34d399; font-weight: 600;">${escapeHtml(j.salary || j.job_type || '-')}</span>
            </td>
            <td>${jobExperienceBadge(j.experience)}</td>
            <td>
                <div style="display:flex; align-items:center; gap:6px;">
                    <input type="email" class="form-control form-control-sm email-inline-input" data-job-id="${j.id}" value="${escapeHtml(j.recruiter_email || '')}" placeholder="Paste recruiter email..." style="min-width: 180px; font-size: 0.85rem;">
                    <button class="btn btn-secondary btn-xs btn-save-email" data-job-id="${j.id}" title="Save Recruiter Email">💾</button>
                </div>
            </td>
            <td>
                <button type="button" class="btn btn-sm btn-optimize-job" data-job-id="${j.id}" title="Tailor the Target Candidate's resume to this job - review, download and create the draft right here" style="background:#f5f3ff; color:#6d28d9; border:1px solid #c4b5fd; font-weight:700; font-size:0.78rem; padding:6px 10px; border-radius:6px; white-space:nowrap;">
                    ⚡ Optimize Resume
                </button>
            </td>
            <td>
                <select class="form-control form-control-sm job-apply-status" data-job-id="${j.id}" title="How this job was applied to" style="font-size:0.8rem; min-width:120px; ${j.apply_status ? 'background:#ecfdf5;' : ''}">
                    ${[['', 'Not applied'], ['portal', 'Applied in portal'], ['email', 'Email sent'], ['both', 'Portal + email']].map(([v, l]) => `<option value="${v}" ${(j.apply_status || '') === v ? 'selected' : ''}>${l}</option>`).join('')}
                </select>
            </td>
            <td>
                <select class="form-control form-control-sm job-consultant-select" data-job-id="${j.id}" style="font-size: 0.85rem;">
                    ${consultantOptions}
                </select>
            </td>
            <td style="text-align: right; white-space: nowrap;">
                <button class="btn btn-primary btn-sm btn-draft-job" data-job-id="${j.id}" style="display:inline-flex; align-items:center; gap:4px;">
                    ✉️ 1-Click Draft
                </button>
                <button class="btn btn-sm btn-copilot-job" data-job-id="${j.id}" style="background: linear-gradient(135deg, #2563eb, #38bdf8); color: #ffffff; border: none; font-weight: 600; font-size: 0.75rem; padding: 6px 10px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px; margin-left: 6px;" title="Personalize email with AI Outreach Copilot">
                    🤖 AI
                </button>
            </td>
        </tr>
        `;
    }).join('');

    // Attach inline email save listeners
    tbody.querySelectorAll('.email-inline-input').forEach(input => {
        input.addEventListener('change', (e) => {
            const jobId = e.target.getAttribute('data-job-id');
            saveRecruiterEmail(jobId, e.target.value);
        });
    });

    tbody.querySelectorAll('.btn-save-email').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const jobId = btn.getAttribute('data-job-id');
            const row = btn.closest('tr');
            const input = row.querySelector('.email-inline-input');
            if (input) {
                saveRecruiterEmail(jobId, input.value);
            }
        });
    });

    tbody.querySelectorAll('.job-apply-status').forEach(sel => {
        sel.addEventListener('change', () => {
            const cand = sel.closest('tr').querySelector('.job-consultant-select');
            setJobApplyStatus(parseInt(sel.getAttribute('data-job-id')), sel.value, sel, cand ? parseInt(cand.value) : null);
        });
    });

    // Attach 1-Click Draft listeners
    tbody.querySelectorAll('.btn-draft-job').forEach(btn => {
        btn.addEventListener('click', () => {
            const jobId = btn.getAttribute('data-job-id');
            const row = btn.closest('tr');
            const candSelect = row.querySelector('.job-consultant-select');
            const emailInput = row.querySelector('.email-inline-input');
            const candId = candSelect ? parseInt(candSelect.value) : state.activeConsultantId;
            const recruiterEmail = emailInput ? emailInput.value.trim() : '';

            createJobDraft(jobId, candId, recruiterEmail, btn);
        });
    });

    // Resume Optimizer for this job + the row's Target Candidate
    tbody.querySelectorAll('.btn-optimize-job').forEach(btn => {
        btn.addEventListener('click', () => {
            const row = btn.closest('tr');
            const candSelect = row.querySelector('.job-consultant-select');
            const candId = candSelect ? parseInt(candSelect.value) : state.activeConsultantId;
            if (!candId) {
                showToast('Please select a Target Candidate first', 'warning');
                return;
            }
            jobOptimizeOpen(parseInt(btn.getAttribute('data-job-id')), candId, row);
        });
    });

    // Attach AI Outreach Copilot listeners (personalize the pitch for this job+candidate)
    tbody.querySelectorAll('.btn-copilot-job').forEach(btn => {
        btn.addEventListener('click', () => {
            const jobId = parseInt(btn.getAttribute('data-job-id'));
            const row = btn.closest('tr');
            const candSelect = row.querySelector('.job-consultant-select');
            const candId = candSelect ? parseInt(candSelect.value) : state.activeConsultantId;
            if (!candId) {
                showToast('Please select a consultant first', 'warning');
                return;
            }
            openAiCopilotModal(candId, jobId);
        });
    });
}

async function saveRecruiterEmail(jobId, email) {
    if (!email) return;
    try {
        const res = await fetch('/api/jobs/update-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                recruiter_email: email.trim()
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast('Recruiter email saved!', 'success', 2000);
        }
    } catch (err) {
        console.error('Error updating email:', err);
    }
}

async function createJobDraft(jobId, candId, customToEmail = '', btnElement = null, optimizedResumeId = null, custom = {}) {
    if (!candId) {
        showToast('Please select a consultant first', 'warning');
        return;
    }

    if (btnElement) {
        btnElement.disabled = true;
        btnElement.innerText = 'Creating Draft...';
    }

    try {
        const res = await fetch('/api/outreach/create-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: jobId,
                candidate_id: candId,
                custom_to_email: customToEmail,
                optimized_resume_id: optimizedResumeId,
                custom_subject: custom.subject || '',
                custom_body: custom.body || ''
            })
        });

        const data = await res.json();
        if (data.success) {
            const bccNote = (data.bcc && data.bcc.length) ? ` BCC: ${data.bcc.length} vendor contact${data.bcc.length === 1 ? '' : 's'}.` : '';
            const attNote = data.resume_filename ? ` Attached: ${data.resume_filename}.` : '';
            showToast(`✉️ Gmail Draft Created for ${data.candidate_name}! To: ${data.to_email || data.recruiter_email || 'Recruiter'}.${attNote}${bccNote}`, 'success', 7000);
            if (!data.resume_attached) {
                showToast('⚠️ ' + (data.resume_note || 'No resume was attached - none is on file for this consultant.'), 'warning', 9000);
            }

            // Update stats
            const statDrafts = document.getElementById('stat-drafted-count');
            if (statDrafts) {
                const cur = parseInt(statDrafts.innerText) || 0;
                statDrafts.innerText = cur + 1;
            }
        } else {
            showToast(data.error || 'Failed to create Gmail draft. Please ensure Gmail is connected.', 'error', 6000);
        }
    } catch (err) {
        showToast('Draft error: ' + err.message, 'error');
    } finally {
        if (btnElement) {
            btnElement.disabled = false;
            btnElement.innerHTML = '✉️ 1-Click Draft';
        }
    }
}

// =========================================================================
// 4. Outbound Pipeline / Drafts Kanban
// =========================================================================
async function loadPipeline() {
    const container = document.getElementById('pipeline-container');
    if (!container) return;

    try {
        const res = await fetch('/api/pipeline');
        const data = await res.json();
        state.pipeline = data;
        renderPipeline(data);
    } catch (err) {
        console.error('Error loading pipeline:', err);
    }
}

function renderPipeline(pipeline) {
    const stages = ['Drafted', 'Applied', 'Interviewing', 'Offer'];
    
    stages.forEach(stage => {
        const colContainer = document.getElementById(`col-${stage}`);
        const countSpan = document.getElementById(`count-${stage}`);
        const items = pipeline[stage] || [];

        if (countSpan) countSpan.innerText = items.length;

        if (colContainer) {
            if (items.length === 0) {
                colContainer.innerHTML = `<div style="text-align:center; padding: 24px; color: var(--text-muted); font-size: 0.85rem;">No applications in ${stage}</div>`;
                return;
            }

            colContainer.innerHTML = items.map(app => {
                const dateStr = app.created_at ? new Date(app.created_at).toLocaleDateString() : 'Recent';
                return `
                <div class="pipeline-card" data-app-id="${app.id}">
                    <div style="font-weight: 600; color: #0f172a; margin-bottom: 4px;">${escapeHtml(app.job_title || 'Software Engineering Role')}</div>
                    <div style="font-size: 0.8rem; color: #38bdf8; margin-bottom: 2px;">👤 ${escapeHtml(app.candidate_name || 'Consultant')}</div>
                    <div style="font-size: 0.8rem; color: var(--text-muted); margin-bottom: 6px;">🏢 ${escapeHtml(app.company || 'Client')} (${escapeHtml(app.recruiter_email || 'No email')})</div>
                    <div style="display:flex; justify-content:space-between; align-items:center; border-top: 1px solid rgba(255,255,255,0.06); padding-top: 6px; margin-top: 6px;">
                        <span style="font-size: 0.75rem; color: var(--text-muted);">${dateStr}</span>
                        <div class="stage-actions">
                            ${stage === 'Drafted' ? `<button class="btn btn-xs btn-outline-primary" onclick="updatePipelineStage(${app.id}, 'Applied')">➔ Applied</button>` : ''}
                            ${stage === 'Applied' ? `<button class="btn btn-xs btn-outline-primary" onclick="updatePipelineStage(${app.id}, 'Interviewing')">➔ Interview</button>` : ''}
                            ${stage === 'Interviewing' ? `<button class="btn btn-xs btn-success" onclick="updatePipelineStage(${app.id}, 'Offer')">🎉 Offer</button>` : ''}
                        </div>
                    </div>
                </div>
                `;
            }).join('');
        }
    });
}

async function updatePipelineStage(appId, newStage) {
    try {
        const res = await fetch('/api/pipeline/update-stage', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                application_id: appId,
                stage: newStage
            })
        });
        const data = await res.json();
        if (data.success) {
            showToast(`Application moved to ${newStage}!`, 'success');
            loadPipeline();
        }
    } catch (err) {
        showToast('Error updating stage: ' + err.message, 'error');
    }
}
window.updatePipelineStage = updatePipelineStage;

// =========================================================================
// 5. AI Master Resume Optimization Bot
// =========================================================================
function initResumeBot() {
    const btnOptimize = document.getElementById('btn-run-resume-optimization');
    const btnDownload = document.getElementById('btn-download-optimized-docx');
    const candSelect = document.getElementById('resumebot-consultant-select');
    const fileInput = document.getElementById('resumebot-file-input');
    const jdTextarea = document.getElementById('resumebot-jd-text');
    const resumeTextarea = document.getElementById('resumebot-resume-text');
    const notesInput = document.getElementById('resumebot-custom-notes');
    const sourceNote = document.getElementById('resumebot-source-note');

    // Where the text in the resume box came from: 'stored' (the selected consultant's saved
    // resume), 'file' (an attached file), or 'typed' (pasted/edited by hand). Tracked so opening
    // the tab never overwrites something the recruiter attached or typed.
    let sourceMode = 'stored';
    // For an exact-format result the ORIGINAL .docx is what gets edited: the attached File (kept
    // in memory here, never uploaded until Optimize is clicked), or the consultant's stored
    // original Word file (the server holds it). Anything typed/edited falls back to plain text.
    let attachedDocx = null;
    let storedHasDocx = false;
    // Which consultant the stored resume now in the box belongs to, and a counter so only the
    // LATEST load may fill the box. Without it a slow earlier load (e.g. the previously selected
    // consultant's PDF being read for the first time) finished last and replaced the newly
    // selected consultant's resume - the dropdown said Praveen while the box held Sai Teja's.
    let loadedForCandId = null;
    let loadSeq = 0;

    const setSourceNote = (html, tone) => {
        if (!sourceNote) return;
        const colors = { ok: '#047857', warn: '#b45309', error: '#b91c1c', muted: '#64748b' };
        sourceNote.style.color = colors[tone] || colors.muted;
        sourceNote.innerHTML = html;
    };

    // The optimizer only ever works on the text visible in the resume box. Selecting a
    // consultant loads THEIR saved resume there (or says plainly that none is on file) - it is
    // never silently swapped in behind the scenes, which is what used to happen when the box
    // was empty and a different attached resume was ignored.
    async function loadStoredResume(onlyIfPristine = false) {
        if (onlyIfPristine && sourceMode !== 'stored') return;
        const candId = candSelect ? parseInt(candSelect.value) : NaN;
        const seq = ++loadSeq;
        if (fileInput) fileInput.value = '';
        sourceMode = 'stored';
        attachedDocx = null;
        storedHasDocx = false;
        loadedForCandId = null;
        if (resumeTextarea) resumeTextarea.value = '';   // never show the previous person's resume while loading
        if (!candId) {
            if (resumeTextarea) resumeTextarea.value = '';
            setSourceNote('Select a consultant, attach a file, or paste a resume below.', 'muted');
            return;
        }
        setSourceNote('Loading the resume on file...', 'muted');
        try {
            const res = await fetch(`/api/consultants/${candId}`);
            if (res.status === 401) { window.location.href = '/login'; return; }
            const cand = await res.json().catch(() => ({}));
            // A newer selection (or an attached/typed resume) has taken over: drop this stale result.
            if (seq !== loadSeq || sourceMode !== 'stored' || (candSelect && parseInt(candSelect.value) !== candId)) return;
            const text = (cand.resume_text || '').trim();
            if (resumeTextarea) resumeTextarea.value = text;
            loadedForCandId = text ? candId : null;
            storedHasDocx = !!text && /\.docx$/i.test(cand.resume_filename || '');
            const who = escapeHtml(cand.name || 'this consultant');
            setSourceNote(text
                ? `Using the resume on file for <b>${who}</b> (${text.length.toLocaleString()} characters). `
                    + (storedHasDocx
                        ? 'It is a Word file, so the optimized version will be that same file with only the changes edited in (original formatting kept). '
                        : 'It is not a Word file, so the download will be a clean Word file built from the text. ')
                    + 'To optimize a different resume, attach a file or paste text below.'
                : `No resume is on file for <b>${who}</b>. Attach a .docx / .pdf / .txt file or paste the resume text below.`,
                text ? 'ok' : 'warn');
        } catch (err) {
            if (seq !== loadSeq) return;
            setSourceNote('Could not load that consultant\'s resume: ' + escapeHtml(err.message), 'error');
        }
    }
    window.refreshResumeBotSource = loadStoredResume;

    if (candSelect) candSelect.addEventListener('change', () => loadStoredResume(false));
    if (resumeTextarea) resumeTextarea.addEventListener('input', () => {
        loadSeq++;
        sourceMode = 'typed';
        attachedDocx = null;
        storedHasDocx = false;
        setSourceNote('Using the text in the box (edited or pasted by hand). The Word download will be rebuilt from this text - to keep a resume\'s exact original formatting, attach its .docx file instead of editing the text.', 'muted');
    });

    if (fileInput) {
        fileInput.addEventListener('change', async () => {
            const file = fileInput.files && fileInput.files[0];
            if (!file) return;
            loadSeq++;   // an attached file wins over any stored-resume load still in flight
            setSourceNote(`Reading <b>${escapeHtml(file.name)}</b>...`, 'muted');
            const formData = new FormData();
            formData.append('resume_file', file);
            try {
                const res = await fetch('/api/resume-bot/extract-text', { method: 'POST', body: formData });
                if (res.status === 401) { window.location.href = '/login'; return; }
                const data = await res.json().catch(() => ({}));
                if (!res.ok) {
                    fileInput.value = '';
                    setSourceNote(escapeHtml(data.error || 'Could not read that file.'), 'error');
                    showToast(data.error || 'Could not read that file.', 'error', 6000);
                    return;
                }
                if (resumeTextarea) resumeTextarea.value = data.text;
                sourceMode = 'file';
                storedHasDocx = false;
                attachedDocx = /\.docx$/i.test(data.filename || file.name) ? file : null;
                setSourceNote(`Using the attached file <b>${escapeHtml(data.filename)}</b> (${Number(data.chars).toLocaleString()} characters). It is used only for this optimization - it is not saved to any consultant. `
                    + (attachedDocx
                        ? 'Your Word file will be edited in place, so its original formatting is kept.'
                        : 'Only a .docx can keep its original formatting; for this file the download will be a clean Word file built from the text.'), 'ok');
            } catch (err) {
                fileInput.value = '';
                setSourceNote('Could not read that file: ' + escapeHtml(err.message), 'error');
            }
        });
    }

    // Show the selected consultant's saved resume as soon as the page is ready.
    loadStoredResume(true);

    const setList = (id, items, cssClass, prefix, emptyMsg) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.innerHTML = (items && items.length)
            ? items.map(s => `<span class="skill-tag ${cssClass}">${prefix}${escapeHtml(s)}</span>`).join(' ')
            : `<span style="color:#94a3b8; font-size:0.85rem;">${emptyMsg}</span>`;
    };

    if (btnOptimize) {
        btnOptimize.addEventListener('click', async () => {
            const jd = jdTextarea ? jdTextarea.value.trim() : '';
            const resume = resumeTextarea ? resumeTextarea.value.trim() : '';
            const notes = notesInput ? notesInput.value.trim() : '';

            const selectedId = candSelect ? parseInt(candSelect.value) : NaN;
            if (sourceMode === 'stored' && resume && loadedForCandId !== selectedId) {
                showToast('The resume in the box does not belong to the selected consultant - reloading it. Check it, then click Optimize again.', 'warning', 6000);
                loadStoredResume(false);
                return;
            }
            if (!resume) {
                showToast('There is no resume to optimize. Attach a file or paste the resume text first.', 'warning');
                if (resumeTextarea) resumeTextarea.focus();
                return;
            }
            if (!jd) {
                showToast('Please paste the client Job Description (JD) to optimize against.', 'warning');
                if (jdTextarea) jdTextarea.focus();
                return;
            }

            btnOptimize.disabled = true;
            // Progress: a live timer (the server works in one request, so the page shows elapsed time and
            // what happens in order - it does not pretend to know which step the server is on).
            const optStarted = Date.now();
            const progressEl = document.getElementById('resumebot-progress');
            const tick = () => {
                const s = Math.round((Date.now() - optStarted) / 1000);
                btnOptimize.innerHTML = `<span class="spinner" style="display:inline-block; width:14px; height:14px; margin-right:6px; vertical-align:-2px;"></span>Optimizing... ${s}s`;
                if (progressEl) {
                    progressEl.style.display = 'block';
                    progressEl.innerHTML = `<b>Working - ${s}s.</b> 1) The AI checks the match against the JD, 2) writes only the changes, 3) the changes go into the Word file. Usually under a minute.`;
                }
            };
            tick();
            const optTimer = setInterval(tick, 1000);

            try {
                // What is sent is always the resume the box says is in use:
                //  - an attached .docx  -> the file itself (edited in place, formatting kept)
                //  - the loaded stored resume, if it is a Word file -> the consultant id + a flag so
                //    the server edits that stored original (it never substitutes a different person)
                //  - anything else (PDF/txt/pasted/edited) -> just the visible text
                let fetchOpts;
                const storedCandId = candSelect ? parseInt(candSelect.value) : NaN;
                if (sourceMode === 'file' && attachedDocx) {
                    const fd = new FormData();
                    fd.append('resume_file', attachedDocx);
                    fd.append('jd_text', jd);
                    fd.append('custom_instructions', notes);
                    fetchOpts = { method: 'POST', body: fd };
                } else {
                    const payload = { jd_text: jd, resume_text: resume, custom_instructions: notes };
                    if (sourceMode === 'stored' && storedHasDocx && storedCandId) {
                        payload.candidate_id = storedCandId;
                        payload.use_stored_file = true;
                    }
                    fetchOpts = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) };
                }
                const res = await fetch('/api/resume-bot/optimize', fetchOpts);
                if (res.status === 401) { window.location.href = '/login'; return; }
                const data = await res.json().catch(() => ({}));
                if (!res.ok || data.error) {
                    showToast(data.error || 'Optimization failed.', 'error', 6000);
                    return;
                }

                const selectedCand = state.consultants.find(c => c.id === parseInt(candSelect ? candSelect.value : NaN));
                // The Word heading uses the name found in THIS resume (a different person's file
                // must not be headed with the selected consultant's name); the consultant's name
                // is only the fallback when the resume doesn't start with a name.
                state.lastOptimizedCandidateName = data.candidate_name_detected
                    ? data.candidate_name
                    : (selectedCand ? selectedCand.name : (data.candidate_name || 'Consultant'));
                state.lastOptimizedResumeText = data.optimized ? (data.updated_resume_text || '') : '';
                // The edited ORIGINAL file, when the server could edit it in place.
                state.lastOptimizedDocxBase64 = (data.optimized && data.format_preserved) ? (data.docx_base64 || '') : '';

                const resultsCard = document.getElementById('resumebot-results-card');
                if (resultsCard) resultsCard.style.display = 'block';

                const byId = (id) => document.getElementById(id);
                if (byId('result-title')) byId('result-title').innerText = data.match_decision || 'Resume Optimization Complete';
                // When the AI did not run, the only number available is a crude keyword overlap. Show it as
                // exactly that (or not at all) - never as an "ATS match" a recruiter could act on.
                const keywordOnly = data.analysis_source === 'keyword-scan';
                const pct = data.initial_match_percentage;
                if (byId('score-initial')) byId('score-initial').innerText = keywordOnly
                    ? (pct == null ? 'not scored (JD names too few known technologies)' : `${pct}% keyword overlap only - NOT an AI match score`)
                    : `${pct ?? '?'}%`;
                if (byId('score-target')) byId('score-target').innerText = keywordOnly ? 'not calculated' : `${data.target_match_percentage ?? '?'}%`;
                if (byId('score-progress-bar')) byId('score-progress-bar').style.width = keywordOnly ? '0%' : `${data.target_match_percentage ?? 0}%`;
                if (byId('result-domain')) byId('result-domain').innerText = `Domain: ${data.domain_detected || 'Not detected'}`;

                // Which engine produced this - shown per result, so a quota problem is never hidden.
                const aiBadge = byId('result-ai-badge');
                if (aiBadge) {
                    let badge;
                    // Never say "Gemini" here specifically - ai_model already names whichever provider actually
                    // answered (Gemini normally, or the Grok backup on the rare day Gemini can't).
                    const modelTag = data.ai_model ? ` <span style="font-weight:400;">[${escapeHtml(data.ai_model)}]</span>` : '';
                    if (data.ai_powered && data.optimized) badge = `<span class="badge" style="background:#ecfdf5; color:#059669; border:1px solid #a7f3d0;">✨ AI analysis + rewrite (your master prompt)${modelTag}</span>`;
                    else if (data.ai_powered) badge = `<span class="badge" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe;">✨ AI analysis - resume left unchanged${modelTag}</span>`;
                    else badge = `<span class="badge" style="background:#f1f5f9; color:#475569; border:1px solid #e2e8f0;">⚙️ Keyword scan only (no AI)</span>`
                        + (data.ai_unavailable_reason ? `<div style="font-size:0.78rem; color:#b45309; margin-top:4px;">AI skipped: ${escapeHtml(data.ai_unavailable_reason)}</div>` : '');
                    aiBadge.innerHTML = badge + optimizerTimingsHtml(data.timings);
                }

                renderCloudCheck(byId('result-cloud-check'), data.cloud_check);

                const banner = byId('result-not-optimized');
                if (banner) {
                    banner.style.display = data.optimized ? 'none' : 'block';
                    banner.innerText = data.optimized ? '' : (data.not_optimized_reason || 'The resume was not changed.');
                }

                const bd = data.match_breakdown || {};
                if (byId('result-breakdown')) {
                    const labels = { mandatory_skills: ['Mandatory skills', 40], recent_project_relevance: ['Recent project', 25], domain_experience: ['Domain', 15], tools_frameworks_cloud: ['Tools/cloud', 10], certifications_education: ['Certs/education', 5], location_work_authorization: ['Location/work auth', 5] };
                    byId('result-breakdown').innerText = Object.keys(labels).every(k => bd[k] !== undefined)
                        ? 'Weighted match: ' + Object.entries(labels).map(([k, [name, cap]]) => `${name} ${Math.round(bd[k])}/${cap}`).join(' · ')
                        : '';
                }

                setList('result-matched-skills', (data.mandatory_matched_skills || []).map(s => s).concat((data.partial_match_skills || []).map(s => `${s} (partial)`)), 'green', '✓ ', 'No JD skills already present in this resume.');
                setList('result-missing-skills', (data.mandatory_missing_skills || []).map(s => s).concat((data.preferred_missing_skills || []).map(s => `${s} (preferred)`)), 'blue', '− ', 'No missing skills found.');
                setList('result-risky-skills', data.risky_skills_avoided || [], 'blue', '', 'None flagged.');

                const added = data.skills_added || {};
                const addedEl = byId('result-added-skills');
                if (addedEl) {
                    const groups = [['Summary', added.summary], ['Technical Skills', added.technical_skills], ['Projects', added.recent_projects], ['Environment', added.environment]]
                        .filter(([, list]) => list && list.length);
                    addedEl.innerHTML = groups.length
                        ? groups.map(([name, list]) => `<div style="margin-bottom:6px;"><b style="font-size:0.78rem; color:#475569;">${name}:</b> ${list.map(s => `<span class="skill-tag blue">+ ${escapeHtml(s)}</span>`).join(' ')}</div>`).join('')
                        : `<span style="color:#94a3b8; font-size:0.85rem;">${data.optimized ? 'No skills needed adding.' : 'Nothing was added - the resume was not changed.'}</span>`;
                }

                const notesEl = byId('result-ats-notes');
                if (notesEl) notesEl.innerHTML = (data.ats_optimization_notes || []).map(n => `<li>${escapeHtml(n)}</li>`).join('') || '<li style="color:#94a3b8;">No notes.</li>';

                // Format status + a precise change list, so the recruiter can verify every edit.
                const fmtEl = byId('result-format-note');
                if (fmtEl) {
                    const preserved = !!(data.optimized && data.format_preserved);
                    const note = preserved
                        ? `✓ Original formatting preserved - the download is your own Word file with only the ${(data.changes || []).length} change(s) below edited in. Everything else (fonts, layout, tables, section order, bullets) is untouched.`
                        : (data.optimized ? (data.format_note || '') : '');
                    fmtEl.style.display = note ? 'block' : 'none';
                    fmtEl.innerText = note;
                    fmtEl.style.background = preserved ? '#ecfdf5' : '#fffbeb';
                    fmtEl.style.border = preserved ? '1px solid #a7f3d0' : '1px solid #fde68a';
                    fmtEl.style.color = preserved ? '#047857' : '#92400e';
                }
                const changesBox = byId('result-changes-box'), changesEl = byId('result-changes');
                if (changesBox && changesEl) {
                    const changes = data.changes || [], skipped = data.skipped_edits || [];
                    const clip = (s, n) => (s && s.length > n) ? s.slice(0, n) + '…' : (s || '');
                    const rows = changes.map(c => c.op === 'insert_after'
                        ? `<div style="margin:0 0 10px; padding:8px 10px; border-left:3px solid #10b981; background:#f0fdf4;"><span style="color:#059669; font-weight:600;">+ New line</span> <span style="color:#64748b;">(added after “${escapeHtml(clip(c.before, 70))}”)</span><div>${escapeHtml(c.after)}</div></div>`
                        : `<div style="margin:0 0 10px; padding:8px 10px; border-left:3px solid #3b82f6; background:#eff6ff;"><span style="color:#1d4ed8; font-weight:600;">Edited line</span><div style="color:#64748b; text-decoration:line-through;">${escapeHtml(clip(c.before, 300))}</div><div>${escapeHtml(c.after)}</div></div>`);
                    const skippedRows = skipped.length
                        ? [`<div style="margin-top:8px; color:#92400e;"><b>${skipped.length} proposed edit(s) were NOT applied</b> (safety checks):</div>`]
                            .concat(skipped.map(s => `<div style="margin:4px 0; color:#92400e; font-size:0.8rem;">• ${escapeHtml(clip(s.new_text, 90))} - <i>${escapeHtml(s.reason)}</i></div>`))
                        : [];
                    changesBox.style.display = (rows.length || skippedRows.length) ? '' : 'none';
                    changesEl.innerHTML = rows.concat(skippedRows).join('');
                }

                if (byId('result-preview-label')) byId('result-preview-label').innerText = data.optimized
                    ? (data.format_preserved ? 'Optimized Resume - text preview (the download keeps your original layout)' : 'Optimized Resume Preview')
                    : 'Resume (unchanged - original text)';
                if (byId('result-preview-text')) {
                    byId('result-preview-text').value = data.updated_resume_text || '';
                    byId('result-preview-text').readOnly = !data.optimized;   // editable once optimized; edits are kept on Save
                }
                if (btnDownload) btnDownload.style.display = data.optimized ? '' : 'none';
                state.lastOptimized = data.optimized ? {
                    candidateId: candSelect ? parseInt(candSelect.value) : null, jdText: jd, aiText: data.updated_resume_text || '',
                    docxB64: data.format_preserved ? (data.docx_base64 || '') : '', primarySkill: data.primary_skill || '',
                    filename: data.suggested_filename || '' } : null;
                const saveBtn = byId('btn-save-optimized-version');
                if (saveBtn) saveBtn.style.display = data.optimized ? '' : 'none';
                if (byId('save-version-status')) byId('save-version-status').innerText = data.optimized && data.suggested_filename
                    ? `Saves as ${data.suggested_filename}. Edit the preview first if needed - nothing is saved until you click Save.` : '';

                showToast(data.optimized ? '✨ Resume optimized against the JD with your master prompt.' : 'Analysis done - the resume was not changed (see the note in the results).', data.optimized ? 'success' : 'info', 5000);
                if (resultsCard) resultsCard.scrollIntoView({ behavior: 'smooth' });

            } catch (err) {
                showToast('Optimization error: ' + err.message, 'error');
            } finally {
                clearInterval(optTimer);
                const progressDone = document.getElementById('resumebot-progress');
                if (progressDone) progressDone.style.display = 'none';
                btnOptimize.disabled = false;
                btnOptimize.innerHTML = '⚡ Optimize & Calculate Match %';
            }
        });
    }

    if (btnDownload) {
        btnDownload.addEventListener('click', async () => {
            if (!state.lastOptimizedResumeText) {
                showToast('Please run optimization first before downloading.', 'warning');
                return;
            }

            try {
                const safeName = state.lastOptimizedCandidateName.replace(/[^a-zA-Z0-9_-]/g, '_');
                // Exact-format path: the server already edited the original .docx - save it as is.
                if (state.lastOptimizedDocxBase64) {
                    const bin = atob(state.lastOptimizedDocxBase64);
                    const bytes = new Uint8Array(bin.length);
                    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                    const fileBlob = new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
                    const fileUrl = window.URL.createObjectURL(fileBlob);
                    const link = document.createElement('a');
                    link.href = fileUrl;
                    link.download = (state.lastOptimized && state.lastOptimized.filename) || `${safeName}_ATS_Tailored_Resume.docx`;
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    setTimeout(() => window.URL.revokeObjectURL(fileUrl), 10000);
                    showToast('📥 Your original Word file, with the tailored changes, downloaded (formatting preserved).', 'success');
                    return;
                }

                const res = await fetch('/api/resume-bot/download-docx', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        resume_text: state.lastOptimizedResumeText,
                        candidate_name: state.lastOptimizedCandidateName
                    })
                });

                if (res.status === 401) { window.location.href = '/login'; return; }
                if (!res.ok) {
                    showToast('Failed to generate .docx resume.', 'error');
                    return;
                }

                const blob = await res.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = (state.lastOptimized && state.lastOptimized.filename) || `${safeName}_ATS_Tailored_Resume.docx`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                showToast('📥 Word resume (.docx) downloaded successfully!', 'success');
            } catch (err) {
                showToast('Download error: ' + err.message, 'error');
            }
        });
    }
}

// =========================================================================
// 6. Modals Controller & Form Submissions
// =========================================================================
function initModals() {
    // 1. Consultant Modal Close / Cancel
    const btnCloseCand = document.getElementById('btn-close-consultant-modal');
    const btnCancelCand = document.getElementById('btn-cancel-consultant');
    if (btnCloseCand) btnCloseCand.addEventListener('click', closeConsultantModal);
    if (btnCancelCand) btnCancelCand.addEventListener('click', closeConsultantModal);

    // Visa Status options depend on Country: a US-based consultant's real work-authorization
    // categories don't apply to someone still in India who hasn't started any US visa
    // process - showing them anyway would force a misleading answer, same problem as the
    // old "Outside USA" catch-all this replaced.
    const countrySelect = document.getElementById('c-country');
    if (countrySelect) {
        countrySelect.addEventListener('change', () => syncVisaOptionsForCountry());
    }

    // 2. Upload Resume Modal Close / Cancel
    const btnCloseUpload = document.getElementById('btn-close-upload-modal');
    const btnCancelUpload = document.getElementById('btn-cancel-upload');
    if (btnCloseUpload) btnCloseUpload.addEventListener('click', closeUploadResumeModal);
    if (btnCancelUpload) btnCancelUpload.addEventListener('click', closeUploadResumeModal);

    // 3. App Password Modal Close / Cancel
    const btnCloseAppPass = document.getElementById('btn-close-app-pass-modal');
    const btnCancelAppPass = document.getElementById('btn-cancel-app-pass');
    if (btnCloseAppPass) btnCloseAppPass.addEventListener('click', closeAppPasswordModal);
    if (btnCancelAppPass) btnCancelAppPass.addEventListener('click', closeAppPasswordModal);

    // 4. Paste & Draft Modal Close / Cancel
    const btnClosePasteDraft = document.getElementById('btn-close-paste-draft-modal');
    const btnCancelPasteDraft = document.getElementById('btn-cancel-paste-draft');
    if (btnClosePasteDraft) btnClosePasteDraft.addEventListener('click', closePasteDraftModal);
    if (btnCancelPasteDraft) btnCancelPasteDraft.addEventListener('click', closePasteDraftModal);

    // Close on backdrop click for all modals
    document.querySelectorAll('.modal-backdrop, .modal-overlay').forEach(modal => {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        });
    });

    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal-backdrop, .modal-overlay').forEach(m => m.style.display = 'none');
        }
    });

    // --- FORM: Save Consultant Profile ---
    const formConsultant = document.getElementById('form-consultant');
    if (formConsultant) {
        formConsultant.addEventListener('submit', async (e) => {
            e.preventDefault();
            const editId = document.getElementById('consultant-edit-id')?.value;
            const submitBtn = document.getElementById('btn-save-consultant');

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Saving...';
            }

            try {
                if (editId) {
                    // Update via PUT
                    const payload = {
                        name: document.getElementById('c-name').value.trim(),
                        email: document.getElementById('c-email').value.trim(),
                        phone: document.getElementById('c-phone').value.trim(),
                        title: document.getElementById('c-title').value.trim(),
                        primary_skills: document.getElementById('c-skills').value.trim(),
                        experience_years: parseInt(document.getElementById('c-exp').value) || null,
                        target_rate: document.getElementById('c-rate').value.trim(),
                        visa_status: document.getElementById('c-visa').value.trim(),
                        location: document.getElementById('c-location').value.trim(),
                        country: document.getElementById('c-country').value.trim(),
                        // the server field is resume_summary ("summary" was silently ignored)
                        resume_summary: document.getElementById('c-summary').value.trim()
                    };

                    const res = await fetch(`/api/consultants/${editId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok) throw new Error(data.error || 'Could not update the consultant.');
                    // A resume chosen while EDITING used to be ignored (only the create form sent the
                    // file), so drafts had no resume to attach. Upload it to the consultant now.
                    const resumeInput = document.getElementById('c-resume-file');
                    const file = resumeInput && resumeInput.files && resumeInput.files[0];
                    if (file) {
                        const fd = new FormData();
                        fd.append('resume_file', file);
                        const up = await fetch(`/api/consultants/${editId}/upload-resume`, { method: 'POST', body: fd });
                        const upData = await up.json().catch(() => ({}));
                        if (!up.ok) throw new Error('Profile saved, but the resume upload failed: ' + (upData.error || up.status));
                        showToast(`Consultant profile updated - resume "${upData.filename || file.name}" saved.`, 'success');
                    } else {
                        showToast('Consultant profile updated!', 'success');
                    }
                } else {
                    // Create via POST (multipart/form-data to support resume upload)
                    const formData = new FormData(formConsultant);
                    const res = await fetch('/api/consultants', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await res.json();
                    showToast('New US Bench Consultant created!', 'success');
                }

                closeConsultantModal();
                await fetchConsultants();
            } catch (err) {
                showToast('Error saving consultant: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Save Consultant';
                }
            }
        });
    }

    // --- FORM: Upload Resume Only ---
    const formUploadResume = document.getElementById('form-upload-resume-only');
    if (formUploadResume) {
        formUploadResume.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('upload-candidate-id')?.value;
            const fileInput = document.getElementById('quick-resume-file');

            if (!candId || !fileInput || !fileInput.files[0]) {
                showToast('Please select a resume file to upload.', 'warning');
                return;
            }

            const formData = new FormData();
            formData.append('resume_file', fileInput.files[0]);

            try {
                const res = await fetch(`/api/consultants/${candId}/upload-resume`, {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (data.success) {
                    showToast('Resume uploaded and attached to consultant!', 'success');
                    closeUploadResumeModal();
                    await fetchConsultants();
                } else {
                    showToast(data.error || 'Failed to upload resume', 'error');
                }
            } catch (err) {
                showToast('Upload error: ' + err.message, 'error');
            }
        });
    }

    // --- FORM: App Password Verification ---
    const formAppPass = document.getElementById('form-app-password');
    if (formAppPass) {
        formAppPass.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('app-pass-cand-id')?.value;
            const email = document.getElementById('app-pass-email')?.value?.trim();
            const appPass = document.getElementById('app-pass-key')?.value?.trim();
            const submitBtn = document.getElementById('btn-submit-app-pass');

            if (!candId || !email || !appPass) {
                showToast('Gmail address and 16-character App Password are required.', 'warning');
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Verifying with Gmail...';
            }

            try {
                const res = await fetch(`/api/consultants/${candId}/set-app-password`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        gmail_account: email,
                        app_password: appPass
                    })
                });

                const data = await res.json();
                if (data.error) {
                    showToast(data.error, 'error', 7000);
                } else {
                    showToast('✅ Gmail App Password verified and connected successfully!', 'success', 5000);
                    closeAppPasswordModal();
                    await fetchConsultants();
                }
            } catch (err) {
                showToast('Verification error: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerText = 'Verify & Connect';
                }
            }
        });
    }

    // --- FORM: Paste Requirement ➔ 1-Click Draft ---
    const formPasteDraft = document.getElementById('form-paste-draft');
    const rawTextarea = document.getElementById('pd-raw-text');

    ['pd-raw-text', 'pd-consultant-select'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener(id === 'pd-raw-text' ? 'input' : 'change', pdCloudSchedule);
    });
    ['pd-raw-text', 'pd-email', 'pd-company'].forEach(id => {
        document.getElementById(id)?.addEventListener('input', pdVendorSchedule);
    });

    if (rawTextarea) {
        rawTextarea.addEventListener('input', (e) => {
            const val = e.target.value;
            // Real-time Regex Extraction
            const emailMatch = val.match(/([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/);
            if (emailMatch) {
                const emailInput = document.getElementById('pd-email');
                if (emailInput && !emailInput.value) emailInput.value = emailMatch[1];
            }

            const titleMatch = val.match(/(?:title|role|position|opening)[ \t]*[:\-]?[ \t]*([A-Za-z0-9 \t\/\-#+.]{4,60})/i);
            if (titleMatch) {
                const titleInput = document.getElementById('pd-title');
                if (titleInput && !titleInput.value) titleInput.value = titleMatch[1].trim();
            }

            const rateMatch = val.match(/(\$\s*\d+(?:\.\d+)?\s*(?:\/hr|\/hour|hr|c2c|w2)?)/i);
            if (rateMatch) {
                const rateInput = document.getElementById('pd-rate');
                if (rateInput && !rateInput.value) rateInput.value = rateMatch[1].trim();
            }
        });
    }

    if (formPasteDraft) {
        formPasteDraft.addEventListener('submit', async (e) => {
            e.preventDefault();
            const candId = document.getElementById('pd-consultant-select')?.value;
            const rawText = document.getElementById('pd-raw-text')?.value?.trim();
            const recruiterEmail = document.getElementById('pd-email')?.value?.trim();
            const jobTitle = document.getElementById('pd-title')?.value?.trim();
            const company = document.getElementById('pd-company')?.value?.trim();
            const rate = document.getElementById('pd-rate')?.value?.trim();
            const notes = '';
            const submitBtn = document.getElementById('btn-submit-paste-draft');
            const shown = pdBox.visible() ? pdBox.values() : null;
            if (shown && /\[please (fill|add):/i.test(shown.body) && !confirm('The email still has [please fill / add] parts. Create the draft anyway and fill them in Gmail?')) return;

            if (!candId) {
                showToast('Please select a consultant first', 'warning');
                return;
            }

            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.innerText = 'Creating Gmail Draft with Attached Resume...';
            }

            try {
                const res = await fetch('/api/outreach/paste-and-draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        candidate_id: parseInt(candId),
                        raw_jd_text: rawText,
                        recruiter_email: (shown && shown.to) || recruiterEmail,
                        custom_subject: shown ? shown.subject : '',
                        custom_body: shown ? shown.body : '',
                        job_title: jobTitle,
                        company: company,
                        salary: rate,
                        custom_notes: notes,
                        bcc_contact_ids: pdVendorSelectedIds(),
                        optimized_resume_id: pdResume.versionId || null
                    })
                });

                const data = await res.json();
                if (data.success) {
                    // Never claim the resume was attached unless it really was - resume_attached
                    // reflects whether a real file was actually found and attached.
                    const bccNote = (data.bcc && data.bcc.length) ? ` BCC: ${data.bcc.length} vendor contact${data.bcc.length === 1 ? '' : 's'}.` : '';
                    showToast((data.resume_attached
                        ? `⚡ Draft created in ${data.candidate_name}'s Gmail with attached .docx resume!`
                        : `⚡ Draft created in ${data.candidate_name}'s Gmail.`) + bccNote, 'success', 6000);
                    if (!data.resume_attached) {
                        showToast('⚠️ ' + (data.resume_note || 'No resume was attached - none is on file for this consultant.'), 'warning', 9000);
                    }
                    closePasteDraftModal();

                    // Update stats
                    const statDrafts = document.getElementById('stat-drafted-count');
                    if (statDrafts) {
                        const cur = parseInt(statDrafts.innerText) || 0;
                        statDrafts.innerText = cur + 1;
                    }
                } else {
                    showToast(data.error || 'Failed to create draft.', 'error', 6000);
                }
            } catch (err) {
                showToast('Draft error: ' + err.message, 'error');
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = '⚡ Create Gmail Draft (Attached .docx)';
                }
            }
        });
    }
}

// =========================================================================
// 7. USA Talent Sourcing & Students (2018 - 2026)
// =========================================================================
let studentsLiveSearchRunning = false;
let studentsEmptyMessage = 'No candidates to show yet. Click "Search LinkedIn" to find India-Bachelor\'s + US-Master\'s profiles for the selected year.';

const STUDENT_SKIP_LABELS = {
    wrong_bachelor_year: 'had a different Bachelor\'s year',
    no_bachelor_year: 'had no Bachelor\'s end year on the profile',
    bachelor_not_india: 'had a Bachelor\'s from a non-Indian college',
    no_bachelor: 'listed no Bachelor\'s degree',
    no_us_master: 'had no US Master\'s listed',
    not_in_us: 'are not currently in the USA',
    incomplete: 'had incomplete data',
    // Fully verified people who graduated in ANOTHER year: kept, not lost.
    other_year_banked: 'are verified for a different graduation year and were saved to your team pool for that year (free to view later)'
};

// "2019 x 5 · 2020 x 2" from the pool's per-year counts.
function formatPoolCounts(counts) {
    return Object.entries(counts || {}).filter(([, n]) => n > 0).map(([y, n]) => `${escapeHtml(y)} × ${n}`).join(' · ');
}

function setStudentsSearchStatus(html) {
    const el = document.getElementById('students-search-status');
    if (!el) return;
    el.innerHTML = html || '';
    el.style.display = html ? 'block' : 'none';
}

function summarizeStudentSkips(skipped) {
    return Object.entries(skipped || {})
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => `${n} ${STUDENT_SKIP_LABELS[k] || k}`)
        .join(' · ');
}

// Verified LinkedIn matches are stored server-side in a shared pool (sourcing_store.py)
// so every recruiter on the team sees each other's finds and nobody re-pays Apify to
// re-discover the same person. The Sourcing tab reads that pool for free on open; only
// "Search LinkedIn" spends credits, and even then continues from wherever the TEAM's last
// search left off (see /api/students/sourced-pool and search-start on the server).

function setPresetFilter(keyword, bachelorYear) {
    const byInput = document.getElementById('filter-student-bachelor-year');
    if (byInput && bachelorYear) byInput.value = bachelorYear;
    // A year shortcut only sets the filter. The live LinkedIn search costs
    // Apify credits, so it only runs when the Search button is clicked.
    loadStudents(false);
}

// runLive=false: instant + free (recruiter-added candidates only).
// runLive=true : also starts the paid live LinkedIn search and streams results in.
async function loadStudents(runLive = false) {
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2023';
    const source = document.getElementById('filter-student-source')?.value || '';
    const tbody = document.getElementById('students-table-body');
    const resultsElem = document.getElementById('students-results-wrapper');
    const jsonHeaders = { 'Content-Type': 'application/json' };

    // 1) Instant and free: candidates already on the recruiter's own bench roster.
    let imported = [];
    try {
        const res = await fetch('/api/students/search', { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ bachelor_year: by }) });
        if (res.status === 401) { window.location.href = '/login'; return; }
        const data = await res.json();
        imported = data.students || data.candidates || data.results || [];
    } catch (err) {
        console.error('Error loading bench candidates:', err);
    }

    // 2) Instant and free: everything the WHOLE TEAM has already found for this source
    // + year, read straight from the shared database - no Apify call involved.
    let found = [];
    let poolCounts = {};
    if (source) {
        try {
            const poolRes = await fetch(`/api/students/sourced-pool?source=${encodeURIComponent(source)}&bachelor_year=${encodeURIComponent(by)}`);
            if (poolRes.status === 401) { window.location.href = '/login'; return; }
            const poolData = await poolRes.json().catch(() => ({}));
            found = poolData.matches || [];
            poolCounts = poolData.pool_counts || {};
        } catch (err) {
            console.error('Error loading shared sourcing pool:', err);
        }
    }
    state.students = imported.concat(found);
    state.studentsLoaded = true;
    updateStudentMetrics(state.students);
    if (resultsElem) resultsElem.style.display = 'block';

    if (!runLive) {
        studentsEmptyMessage = 'No candidates to show yet. Click "Search LinkedIn" to find India-Bachelor\'s + US-Master\'s profiles for the selected year.';
        renderStudentsGrid(state.students);
        // Everything the team has already paid for, by graduation year - viewing it is free, so the
        // recruiter can see it BEFORE spending on another search.
        const poolLine = formatPoolCounts(poolCounts)
            ? ` <span style="color:#475569;">Your team's pool by graduation year: <b>${formatPoolCounts(poolCounts)}</b> (change the year to view another - free).</span>`
            : '';
        setStudentsSearchStatus((found.length > 0
            ? `Showing <b>${found.length}</b> verified match(es) your team has already found for ${escapeHtml(by)}${imported.length ? ' plus candidates on your bench' : ''}. Click <b>Search LinkedIn</b> to find more (paid: up to about $1.20 per click).`
            : `Nothing saved yet for this year. Click <b>Search LinkedIn</b> to find candidates (paid: up to about $1.20 per click).`) + poolLine);
        return;
    }

    // 2) Paid live search: start an Apify run, then poll it and stream verified matches in.
    if (studentsLiveSearchRunning) {
        showToast('A LinkedIn search is already running - please wait for it to finish.', 'info');
        return;
    }
    studentsLiveSearchRunning = true;
    const btn = document.getElementById('btn-apply-student-filter');
    const btnHtml = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span>Searching...</span>'; }

    const showSearching = (scanned, found) => {
        if (state.students.length > 0 || !tbody) return;
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 36px; color: var(--text-muted);">
                    <div style="display:inline-block; width:32px; height:32px; border:3px solid rgba(99,102,241,0.2); border-top-color:#6366f1; border-radius:50%; animation: spin 0.8s linear infinite; margin-bottom:12px;"></div>
                    <div style="font-weight:600; color:#0f172a; font-size:1rem;">Searching LinkedIn: India Bachelor's (${by}) + US Master's...</div>
                    <div style="font-size:0.85rem; margin-top:4px; color:#64748b;">Scanned ${scanned} profiles so far, ${found} verified match(es). Each profile's education is checked strictly; results appear here as they are found.</div>
                </td>
            </tr>`;
    };

    let matches = [];
    let scanned = 0;
    let skipped = {};
    let cost = null;
    try {
        if (!source) {
            studentsEmptyMessage = 'LinkedIn search is not set up yet. Add APIFY_API_TOKEN in the server settings.';
            setStudentsSearchStatus(`<span style="color:#b45309;">${escapeHtml(studentsEmptyMessage)}</span>`);
            renderStudentsGrid(state.students);
            return;
        }

        // ---- Apify: scan LinkedIn profiles in parallel one-page runs ----
        setStudentsSearchStatus('Starting live LinkedIn search...');
        if (imported.length > 0) renderStudentsGrid(state.students); else showSearching(0, 0);

        let depthPages = parseInt(document.getElementById('filter-student-depth')?.value || '6', 10) || 6;
        // Stop early (and stop paying) once this many verified matches have been found.
        const targetMatches = depthPages <= 3 ? 8 : (depthPages <= 6 ? 15 : 30);
        // ONE page at a time. Live test (2026-09-28): the LinkedIn data provider (the Apify actor) chokes when
        // several runs hit it together - with 3 runs started at once only 1 returned profiles, the others
        // logged "Acquire timeout - too many queued requests" yet Apify still billed them as "succeeded".
        // A single run worked every time (24-25 profiles for $0.20).
        const WAVE = 1;
        const retryDelayMs = (typeof window.SOURCING_RETRY_DELAY_MS === 'number') ? window.SOURCING_RETRY_DELAY_MS : 20000;   // tests shorten this
        const sleep = (ms) => new Promise(r => setTimeout(r, ms));
        const costByRun = {};
        const harvestByRun = {};   // HarvestAPI direct (SOURCING_PROVIDER=harvestapi): search numbers per run
        let pagesUsed = 0;
        let stopMessage = '';
        const retryPages = [];          // pages whose run failed at the provider: one more try each
        const providerFailures = [];    // pages that failed again
        let planLimitHit = false;       // the Apify account is blocked from this scraper: stop, don't pay for doomed runs
        const startedAt = Date.now();
        const timeLeft = () => Date.now() - startedAt < 16 * 60 * 1000;

        // Depth = several one-page Apify runs (a free Apify plan caps each run at ~25 profiles), each
        // polled to the end before the next starts; then decide whether to continue (more pages allowed
        // and the target number of matches not yet reached).
        while ((pagesUsed < depthPages || retryPages.length) && matches.length < targetMatches && timeLeft() && !planLimitHit) {
            const isRetry = retryPages.length > 0;
            const retryPage = isRetry ? retryPages.shift() : null;
            if (isRetry) {
                setStudentsSearchStatus(`The LinkedIn data provider was busy - retrying page ${retryPage} in a moment (a failed page only costs about $0.004)...`);
                await sleep(retryDelayMs);
            }
            const waveSize = Math.min(WAVE, depthPages - pagesUsed);
            // start_page is decided server-side from the TEAM's shared cursor (sourcing_store),
            // not sent from here, so two recruiters searching at once still get non-overlapping pages.
            // A retry re-asks for that same page (the server does not move the cursor for it).
            const body = isRetry ? { bachelor_year: by, pages: 1, retry_start_page: retryPage } : { bachelor_year: by, pages: waveSize };
            const startRes = await fetch('/api/students/search-start', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) });
            if (startRes.status === 401) { window.location.href = '/login'; return; }
            const start = await startRes.json().catch(() => ({}));
            if (!startRes.ok) { stopMessage = start.error || 'Could not start the LinkedIn search.'; break; }
            const runs = (start.runs || []).map(r => ({ ...r, offset: 0, done: false, failures: 0, retried: isRetry }));
            if (!isRetry) pagesUsed += runs.length;
            if (start.pages_per_search) depthPages = Math.min(depthPages, start.pages_per_search);   // HarvestAPI trial cap per click
            if (start.warning) stopMessage = start.warning;
            if (runs.length === 0) break;

            while (runs.some(r => !r.done) && timeLeft()) {
                await sleep(4000);
                await Promise.all(runs.filter(r => !r.done).map(async (r) => {
                    try {
                        const pollRes = await fetch('/api/students/search-poll', {
                            method: 'POST',
                            headers: jsonHeaders,
                            body: JSON.stringify({ run_id: r.run_id, dataset_id: r.dataset_id, bachelor_year: by, offset: r.offset, matched_so_far: matches.length, target: targetMatches })
                        });
                        const poll = await pollRes.json().catch(() => ({}));
                        if (!pollRes.ok) { r.failures += 1; if (r.failures >= 3) r.done = true; return; }
                        r.failures = 0;
                        r.offset = poll.next_offset;
                        scanned += poll.scanned_new || 0;
                        Object.entries(poll.skipped || {}).forEach(([k, n]) => { skipped[k] = (skipped[k] || 0) + n; });
                        if (poll.cost_usd !== null && poll.cost_usd !== undefined) costByRun[r.run_id] = Number(poll.cost_usd);
                        if (poll.harvest) harvestByRun[r.run_id] = poll.harvest;
                        const knownUrls = new Set(found.map(c => c.profile_url));
                        const fresh = (poll.new_matches || []).filter(c => !knownUrls.has(c.profile_url));
                        fresh.forEach(c => found.push(c));
                        matches = matches.concat(fresh);
                        r.done = !!poll.done;
                        // The run ended but the provider gave nothing (its log says why): try that page once more,
                        // and if it fails again say so - never present it as "scanned 0, none matched".
                        if (poll.done && poll.provider_error) {
                            if (poll.provider_error_kind === 'plan_limit') {
                                // The Apify account itself is blocked (free accounts get only a few runs of this scraper):
                                // retrying or starting more pages would only pay for runs that cannot work.
                                planLimitHit = true;
                                providerFailures.push({ page: r.start_page, message: poll.provider_error });
                            } else if (!r.retried) retryPages.push(r.start_page);
                            else providerFailures.push({ page: r.start_page, message: poll.provider_error });
                        }
                    } catch (e) {
                        r.failures += 1;
                        if (r.failures >= 3) r.done = true;
                    }
                }));
                state.students = imported.concat(found);
                if (matches.length > 0) renderStudentsGrid(state.students); else showSearching(scanned, 0);
                const secs = Math.round((Date.now() - startedAt) / 1000);
                setStudentsSearchStatus(`Searching: scanned ${scanned} profiles (${pagesUsed} of ${depthPages} pages started), <b>${matches.length}</b> new verified match(es) for ${escapeHtml(by)} (${secs}s).`);
            }
        }
        cost = Object.values(costByRun).reduce((a, b) => a + b, 0);
        retryPages.forEach(p => providerFailures.push({ page: p, message: 'the search ran out of time before this page could be retried' }));

        if (pagesUsed === 0) {
            studentsEmptyMessage = stopMessage || 'Could not start the LinkedIn search.';
            setStudentsSearchStatus(`<span style="color:#b91c1c;">${escapeHtml(studentsEmptyMessage)}</span>`);
            renderStudentsGrid(state.students);
            return;
        }
        if (scanned === 0 && providerFailures.length) {
            studentsEmptyMessage = planLimitHit
                ? `The LinkedIn search could not run: ${providerFailures[0].message}. Nothing is wrong with your filters. (A blocked run costs about $0.004.)`
                : `The LinkedIn data provider returned no profiles: ${providerFailures[0].message}. This is a problem on the provider's side, not with your filters or your Apify key - wait a few minutes and click Search LinkedIn again. Each failed run only costs about $0.004.`;
            setStudentsSearchStatus(`<span style="color:#b91c1c;">${escapeHtml(studentsEmptyMessage)}</span>`);
            renderStudentsGrid(state.students);
            return;
        }
        const failNote = providerFailures.length
            ? ` <span style="color:#b45309;">${providerFailures.length} page(s) (${providerFailures.map(f => f.page).join(', ')}) could not be fetched: ${escapeHtml(providerFailures[0].message)}. ${planLimitHit ? 'The search stopped there so no more credits are wasted.' : 'They are skipped for now - click Search LinkedIn again in a few minutes.'}</span>`
            : '';

        // Every verified match was already saved to the shared pool by the server as it
        // was found (see search-poll), so nothing needs to be persisted from here.
        const skipText = summarizeStudentSkips(skipped);
        // Apify finalizes a run's cost slightly after it ends, so only show it when it is a real figure.
        const harvestRuns = Object.values(harvestByRun);
        const costText = harvestRuns.length ? harvestSummary(harvestRuns, cost)
            : ((cost !== null && Number(cost) > 0) ? ` Apify cost for this search: about $${Number(cost).toFixed(2)}.` : '');
        setStudentsSearchStatus(`Finished: scanned <b>${scanned}</b> profiles from Indian colleges, <b>${matches.length}</b> new verified match(es) (<b>${found.length}</b> total for ${escapeHtml(by)} across your team). ${skipText ? 'Not shown: ' + escapeHtml(skipText).replace(/ · /g, '; ') + '.' : ''}${costText} Click Search LinkedIn again to scan the next pages for more.${failNote}${stopMessage ? ' <span style="color:#b45309;">Note: ' + escapeHtml(stopMessage) + '</span>' : ''}`);
        if (found.length === 0) {
            studentsEmptyMessage = `The search finished: none of the ${scanned} profiles scanned had an Indian Bachelor's ending in ${by} together with a US Master's. Click "Search LinkedIn" again to scan the next pages of results (each search moves on to new profiles).`;
            renderStudentsGrid(state.students);
        }
    } catch (err) {
        console.error('Live LinkedIn search error:', err);
        studentsEmptyMessage = 'The LinkedIn search failed: ' + (err.message || 'unknown error') + '. Please try again.';
        setStudentsSearchStatus(`<span style="color:#ef4444;">${escapeHtml(studentsEmptyMessage)}</span>`);
        renderStudentsGrid(state.students);
    } finally {
        studentsLiveSearchRunning = false;
        if (btn) { btn.disabled = false; btn.innerHTML = btnHtml; }
        if (runLive && typeof eduAfterLiveSearch === 'function') eduAfterLiveSearch();
    }
}

function updateStudentMetrics(students) {
    const totalElem = document.getElementById('stat-students-count');
    const optElem = document.getElementById('stat-students-opt');
    const switchersElem = document.getElementById('stat-students-switchers');
    const onboardedElem = document.getElementById('stat-students-onboarded');

    // Real counts only. (These used to fall back to invented numbers - 70% of the total for "OPT",
    // 40% for "switchers", and "3" onboarded - whenever the true count was zero.)
    const total = students.length;
    const optCount = students.filter(s => {
        const tag = (s.status_badge || s.status_tag || '').toLowerCase();
        return tag.includes('opt') || tag.includes('cpt');
    }).length;

    const switchersCount = students.filter(s => {
        const by = parseInt(s.bachelor_year || s.grad_year);
        return by >= 2014 && by <= 2018;
    }).length;

    if (totalElem) totalElem.innerText = total;
    if (optElem) optElem.innerText = optCount;
    if (switchersElem) switchersElem.innerText = switchersCount;
    if (onboardedElem) onboardedElem.innerText = state.consultants.length;
}

// Shared by the LinkedIn column link and the "Message" quick action, so both always agree on
// which URL a candidate's row actually points at.
function cleanCandidateName(c) {
    return (c.name || 'Candidate')
        .replace(/\b(Ph\.?D|CFP|MS|B\.?Tech|Engineer|Developer|Lead|Architect|Senior|Junior|Associate)\b/gi, '')
        .replace(/[,\/()]/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();
}

function resolveLinkedInUrl(c) {
    const cleanName = cleanCandidateName(c);
    let url = (c.linkedin_url || c.profile_url || '').trim();
    const isDirect = !!(url && url.includes('linkedin.com/in/'));
    if (!isDirect && (!url || !url.startsWith('http') || url.includes('search/results'))) {
        url = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}`;
    }
    return { url, isDirect, cleanName };
}

// A short, honest LinkedIn message: only states education facts this candidate's row actually
// shows as verified - nothing invented, no years-of-experience or placement claims. The recruiter
// pastes it into LinkedIn's own chat box and can edit it first (LinkedIn caps a CONNECTION
// REQUEST note at 300 characters, though an ordinary message to someone you're connected to is not
// capped the same way).
function buildLinkedInDmText(c) {
    const firstName = (c.name || '').trim().split(/\s+/)[0] || 'there';
    const yearVerified = c.year_verified !== false;
    const bYear = (c.bachelor_year || c.grad_year || '').trim();
    const bCollege = (c.bachelor_college || '').trim();
    const mUni = (c.master_university || c.university || '').trim();
    const knownCollege = bCollege && bCollege !== 'College not listed';
    const knownUni = mUni && mUni !== 'University not listed';

    let seen = '';
    if (yearVerified && knownCollege && bYear) {
        seen = ` I saw your ${bCollege} background (${bYear})` + (knownUni ? ` and your Master's at ${mUni}.` : '.');
    } else if (knownUni) {
        seen = ` I saw your Master's at ${mUni}.`;
    }

    return `Hi ${firstName},${seen} We work with US IT clients on contract/C2C roles and wanted to reach out directly - are you currently open to hearing about new opportunities? Happy to share details if so.`;
}

async function copyTextSafely(text) {
    try {
        if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            return true;
        }
    } catch (err) { /* fall through to the legacy path below */ }
    try {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        const ok = document.execCommand('copy');
        document.body.removeChild(ta);
        return ok;
    } catch (err) {
        return false;
    }
}

// The "Message" quick action: this is as far as automation goes. It copies a drafted message and
// opens the candidate's LinkedIn profile - the recruiter clicks LinkedIn's own Message button and
// pastes it themselves. Nothing here logs into LinkedIn or sends on the recruiter's behalf:
// automating that step risks LinkedIn restricting the recruiter's own account.
async function onMessageOnLinkedIn(cardObj) {
    const c = typeof cardObj === 'string' ? JSON.parse(cardObj) : cardObj;
    const { url, isDirect } = resolveLinkedInUrl(c);
    window.open(url, '_blank', 'noopener');   // opened synchronously, in the same click, so it isn't popup-blocked
    const text = buildLinkedInDmText(c);
    const copied = await copyTextSafely(text);
    if (!copied) {
        showToast('Could not copy automatically - here is the message to paste yourself: ' + text, 'warning', 15000);
    } else if (isDirect) {
        showToast('💬 Message copied. Paste it into the chat box on the LinkedIn profile that just opened - you send it.', 'success', 6000);
    } else {
        showToast("No direct LinkedIn profile link is on file for this candidate, so a LinkedIn search opened instead. Message copied - once you find them, paste it into the chat.", 'info', 8000);
    }
}
window.onMessageOnLinkedIn = onMessageOnLinkedIn;

function renderStudentsGrid(candidates) {
    const tbody = document.getElementById('students-table-body');
    if (!tbody) return;

    // Strict Double-Lock: ensure only exact target year is rendered, for
    // candidates that actually HAVE year data. Candidates explicitly marked
    // year_verified=false (e.g. the Apify school-search source, which has no
    // year data by design) are exempt from this filter rather than being
    // silently dropped - they're shown with a "verify manually" badge instead.
    const byInput = document.getElementById('filter-student-bachelor-year')?.value?.trim();
    if (byInput && byInput.toLowerCase() !== 'all') {
        const yearMatch = byInput.match(/\b(19\d\d|20\d\d)\b/);
        if (yearMatch && candidates && candidates.length > 0) {
            const targetYear = yearMatch[1];
            candidates = candidates.filter(c => {
                if (c.year_verified === false) return true;
                const candYear = String(c.bachelor_year || c.grad_year || '');
                return candYear === targetYear;
            });
        }
    }

    if (!candidates || candidates.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" style="text-align:center; padding: 40px; color: var(--text-muted);">
                    ${escapeHtml(studentsEmptyMessage)} Nothing is ever substituted with fake data.
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = candidates.map(c => {
        const rawStatus = (c.status_tag || c.status_badge || c.settlement_pathway || '').toLowerCase();
        // Never claim a visa pathway that the source didn't state ("STEM OPT / 3-year authorization" used to be the default).
        let settlementBadge = c.settlement_badge || "Work authorization not verified";
        let settlementSub = c.settlement_sub || "Confirm with the candidate";
        let settlementBadgeStyle = "background:rgba(16,185,129,0.12); color:#047857; border:1px solid rgba(16,185,129,0.4);";

        if (rawStatus.includes('h1b')) {
            settlementBadge = "🇺🇸 H1B Transfer Eligible";
            settlementSub = "Direct Work Visa";
            settlementBadgeStyle = "background:rgba(245,158,11,0.15); color:#b45309; border:1px solid rgba(245,158,11,0.4);";
        } else if (rawStatus.includes('cpt')) {
            settlementBadge = "🇺🇸 Day 1 CPT Worker";
            settlementSub = "Curricular Practical Training";
            settlementBadgeStyle = "background:rgba(2,132,199,0.10); color:#0369a1; border:1px solid rgba(2,132,199,0.35);";
        }
        const { url: targetLiUrl, cleanName } = resolveLinkedInUrl(c);
        const googleLiUrl = `https://www.google.com/search?q=site:linkedin.com/in/+${encodeURIComponent('"' + cleanName + '"')}+USA`;
        const initials = (c.name || 'US').split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();

        // Honesty: only show a year if one was actually verified from the
        // source text. Some sources (e.g. the Apify school-search) genuinely
        // have no year data at all - never default that to a guessed year.
        const yearVerified = c.year_verified !== false;
        const bTechYear = (c.bachelor_year || c.grad_year || '').trim();
        const bTechYearDisplay = yearVerified && bTechYear ? bTechYear : '⚠️ Verify';
        // Unknown stays "not listed" - it is never filled in with a plausible-sounding guess.
        const bTechCollege = c.bachelor_college || 'College not listed';
        const bTechDegree = c.bachelor_degree || "Bachelor's degree";

        const mDegree = c.master_degree || c.degree || "Master's: not listed";
        const mUni = c.master_university || c.university || 'University not listed';

        return `
        <tr style="border-bottom: 1px solid #e2e8f0; transition: background 0.15s ease;" onmouseover="this.style.background='rgba(37,99,235,0.05)'" onmouseout="this.style.background='transparent'">
            <!-- 1. Candidate Name -->
            <td style="padding: 14px 16px;">
                <div style="display:flex; align-items:center; gap:12px;">
                    <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" style="text-decoration:none;" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return true;">
                        <div style="width:38px; height:38px; border-radius:50%; background:linear-gradient(135deg, #4f46e5, #06b6d4); color:#fff; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:13px; cursor:pointer;" title="View LinkedIn Profile">
                            ${initials}
                        </div>
                    </a>
                    <div>
                        <div style="font-weight:600; color:#0f172a; font-size:0.95rem;">
                            <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" style="color:inherit; text-decoration:none; display:inline-flex; align-items:center; gap:6px;" onmouseover="this.style.color='#2563eb'" onmouseout="this.style.color='inherit'" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return true;">
                                <span>${escapeHtml(c.name || 'US Candidate')}</span>
                                <span style="color:#0a66c2; font-size:11px; font-weight:700; background:rgba(10,102,194,0.18); padding:1px 6px; border-radius:4px; border:1px solid rgba(10,102,194,0.4);">in &#x2197;</span>
                            </a>
                        </div>
                        <div style="font-size:0.8rem; color:#475569; margin-top:2px; max-width:280px;">
                            ${escapeHtml(c.headline || c.skills || 'Software Engineer')}
                        </div>
                    </div>
                </div>
            </td>

            <!-- 2. India Bachelor's & US Master's Journey -->
            <td style="padding: 14px 16px; font-size:0.85rem;">
                <div style="font-weight:600; color:#1e293b; display:flex; align-items:center; gap:5px;">
                    <span>🇮🇳</span> <span>${escapeHtml(bTechDegree)} (${escapeHtml(bTechYearDisplay)})</span>
                </div>
                <div style="font-size:0.75rem; color:#64748b; margin-bottom:4px;">
                    ${escapeHtml(bTechCollege)}
                </div>
                <div style="font-weight:500; color:#0369a1; display:flex; align-items:center; gap:5px; font-size:0.8rem;">
                    <span>🇺🇸</span> <span>${escapeHtml(mDegree)}</span>
                </div>
                ${!yearVerified ? '<div style="font-size:0.72rem; color:#f59e0b; margin-top:3px;">⚠️ Real profile match, but this source has no graduation year data — confirm on their profile.</div>' : ''}
            </td>

            <!-- 3. Primary Filter: India B.Tech Year (<=2020) -->
            <td style="padding: 14px 16px; text-align:center;">
                <span style="display:inline-block; padding:4px 10px; border-radius:8px; font-size:0.85rem; font-weight:700; ${yearVerified ? 'background:rgba(2,132,199,0.10); color:#0369a1; border:1px solid rgba(2,132,199,0.35);' : 'background:rgba(245,158,11,0.15); color:#b45309; border:1px solid rgba(245,158,11,0.45);'}" title="${yearVerified ? "Bachelor's year confirmed from source text" : 'Year not available from this source — verify manually'}">
                    🎓 ${escapeHtml(bTechYearDisplay)}
                </span>
            </td>

            <!-- 4. US University (Higher Education) -->
            <td style="padding: 14px 16px; color:#334155; font-size:0.85rem;">
                <div style="font-weight:600;">${escapeHtml(mUni)}</div>
                <div style="font-size:0.75rem; color:#64748b;">Master's school (confirm it is in the USA)</div>
            </td>

            <!-- 5. How Settled in USA -->
            <td style="padding: 14px 16px;">
                <div style="display:inline-flex; flex-direction:column; gap:3px;">
                    <span style="display:inline-block; padding:4px 10px; border-radius:8px; font-size:0.78rem; font-weight:700; ${settlementBadgeStyle}">
                        ${escapeHtml(settlementBadge)}
                    </span>
                    <span style="font-size:0.72rem; color:#64748b; font-weight:500;">
                        ${escapeHtml(settlementSub)}
                    </span>
                </div>
            </td>

            <!-- 6. US Location -->
            <td style="padding: 14px 16px; color:#334155; font-size:0.85rem;">
                &#x1F4CD; ${escapeHtml(c.location || 'United States')}
            </td>

            <!-- 7. LinkedIn Profile Direct Link -->
            <td style="padding: 14px 16px;">
                <div style="display:inline-flex; gap:5px; align-items:center;">
                    <a href="${targetLiUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-xs" style="text-decoration:none; display:inline-flex; align-items:center; gap:5px; background:rgba(10,102,194,0.10); border:1px solid #0a66c2; color:#0a66c2; font-weight:600; padding:4px 9px;" title="Direct LinkedIn Profile" onclick="event.stopPropagation(); window.open('${targetLiUrl}', '_blank'); return false;">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19 0h-14c-2.761 0-5 2.239-5 5v14c0 2.761 2.239 5 5 5h14c2.762 0 5-2.239 5-5v-14c0-2.761-2.238-5-5-5zm-11 19h-3v-11h3v11zm-1.5-12.268c-.966 0-1.75-.79-1.75-1.764s.784-1.764 1.75-1.764 1.75.79 1.75 1.764-.783 1.764-1.75 1.764zm13.5 12.268h-3v-5.604c0-3.368-4-3.113-4 0v5.604h-3v-11h3v1.765c1.396-2.586 7-2.777 7 2.476v6.759z"/></svg>
                        LinkedIn &#x2197;
                    </a>
                    <a href="${googleLiUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-xs" style="text-decoration:none; display:inline-flex; align-items:center; gap:3px; background:#f8fafc; border:1px solid #cbd5e1; color:#475569; font-weight:500; padding:4px 7px;" title="Find Exact Profile via Google" onclick="event.stopPropagation(); window.open('${googleLiUrl}', '_blank'); return false;">
                        G &#x2197;
                    </a>
                </div>
            </td>

            <!-- 8. Quick Actions -->
            <td style="padding: 14px 16px; text-align:right;">
                <div style="display:inline-flex; gap:6px; align-items:center;">
                    <button class="btn btn-secondary btn-xs" onclick='onMessageOnLinkedIn(${JSON.stringify(c).replace(/'/g, "&apos;")})' style="background:rgba(10,102,194,0.10); color:#0a66c2; border:1px solid rgba(10,102,194,0.35);" title="Copies a drafted message and opens their LinkedIn profile - you paste and send it yourself">
                        💬 Message
                    </button>
                    <button class="btn btn-secondary btn-xs" onclick='onOpenStudentPitch(${JSON.stringify(c).replace(/'/g, "&apos;")})' style="background:rgba(99,102,241,0.10); color:#4338ca; border:1px solid rgba(99,102,241,0.35);">
                        Pitch
                    </button>
                    <button class="btn btn-success btn-xs" onclick='onAddStudentToBench(${JSON.stringify(c).replace(/'/g, "&apos;")})' style="box-shadow: 0 0 10px rgba(16, 185, 129, 0.3);">
                        + Bench
                    </button>
                </div>
            </td>
        </tr>
        `;
    }).join('');
}

async function onAddStudentToBench(cardObj) {
    const c = typeof cardObj === 'string' ? JSON.parse(cardObj) : cardObj;
    try {
        const res = await fetch('/api/students/add-to-bench', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: c.name,
                email: c.email || '',
                phone: c.phone || '',
                headline: c.headline || `${c.skills || 'Software Engineer'}`,
                location: c.location || 'United States',
                skills: c.skills || 'Full Stack, Cloud',
                grad_year: c.grad_year || '2024',
                university: c.university || 'US University',
                profile_url: c.profile_url || c.linkedin_url || ''
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast(`🎉 ${c.name} added to US Bench Consultants!`, 'success');
            await fetchConsultants();
        } else {
            showToast(data.error || 'Failed to add to bench.', 'error');
        }
    } catch (err) {
        showToast('Error adding to bench: ' + err.message, 'error');
    }
}
window.onAddStudentToBench = onAddStudentToBench;

async function onOpenStudentPitch(cardObj) {
    const c = typeof cardObj === 'string' ? JSON.parse(cardObj) : cardObj;
    try {
        const res = await fetch('/api/students/generate-pitch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: c.name,
                headline: c.headline,
                university: c.university,
                skills: c.skills,
                grad_year: c.grad_year
            })
        });

        const data = await res.json();
        const subjInput = document.getElementById('pitch-modal-subject');
        const bodyTextarea = document.getElementById('pitch-modal-body');
        const modal = document.getElementById('modal-student-pitch');

        if (subjInput) subjInput.value = data.subject || `Exclusive Bench Placement & Marketing Opportunity - Adroit ATS`;
        if (bodyTextarea) bodyTextarea.value = data.pitch_body || '';

        if (modal) {
            modal.style.display = 'flex';
        }
    } catch (err) {
        showToast('Error generating pitch: ' + err.message, 'error');
    }
}
window.onOpenStudentPitch = onOpenStudentPitch;

function closeStudentPitchModal() {
    const modal = document.getElementById('modal-student-pitch');
    if (modal) modal.style.display = 'none';
}
window.closeStudentPitchModal = closeStudentPitchModal;

function exportStudentsCSV() {
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2023';

    fetch('/api/students/export-csv', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            bachelor_year: by,
            candidates: state.students || []
        })
    })
    .then(res => res.blob())
    .then(blob => {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `Adroit_US_Candidates_${new Date().toISOString().slice(0,10)}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        showToast('Candidates exported to CSV successfully!', 'success');
    })
    .catch(err => {
        showToast('Failed to export CSV: ' + err.message, 'error');
    });
}
window.exportStudentsCSV = exportStudentsCSV;


// =========================================================================
// Path A: Google X-Ray & Live Recruiter Search Engine
// =========================================================================
function buildXRayQuery() {
    const by = document.getElementById('filter-student-bachelor-year')?.value?.trim() || '2023';

    const yearMatch = by.match(/\b(19\d\d|20\d\d)\b/);
    const targetYear = yearMatch ? yearMatch[1] : '2023';

    const parts = [
        'site:linkedin.com/in/',
        '-site:in.linkedin.com',
        '-Hyderabad', '-Bengaluru', '-Bangalore', '-Pune', '-Chennai', '-Mumbai', '-Noida', '-Gurgaon', '-"India"',
        '"United States"',
        '("B.Tech" OR "B.E.")',
        `"${targetYear}"`,
        '("Master" OR "MS" OR "M.S.")'
    ];

    const queryStr = parts.join(' ');
    const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(queryStr)}`;
    const linkedinUrl = `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent('("B.Tech" OR "B.E.") "' + targetYear + '" ("Master" OR "MS")')}&geoUrn=%5B%22103644278%22%5D&origin=FACETED_SEARCH`;

    return { queryStr, googleUrl, linkedinUrl };
}

function launchLiveXRaySearch() {
    const { queryStr, googleUrl } = buildXRayQuery();
    showToast('Opening Google X-Ray for the selected passout year...', 'info');
    window.open(googleUrl, '_blank');
}

function launchLiveLinkedInSearch() {
    const { linkedinUrl } = buildXRayQuery();
    showToast(`Launching Direct LinkedIn Talent Search...`, 'info');
    window.open(linkedinUrl, '_blank');
}

function openQuickImportModal() {
    const modal = document.getElementById('modal-quick-import-candidate');
    if (modal) {
        modal.style.display = 'flex';
        const urlInput = document.getElementById('import-cand-linkedin');
        if (urlInput) urlInput.focus();
    }
}

function closeQuickImportModal() {
    const modal = document.getElementById('modal-quick-import-candidate');
    if (modal) modal.style.display = 'none';
}

async function submitQuickImport(e) {
    e.preventDefault();
    const linkedinUrl = document.getElementById('import-cand-linkedin')?.value?.trim();
    const name = document.getElementById('import-cand-name')?.value?.trim();
    const year = document.getElementById('import-cand-year')?.value?.trim() || '2020';
    const headline = document.getElementById('import-cand-headline')?.value?.trim() || 'Technical Consultant';
    const location = document.getElementById('import-cand-location')?.value?.trim() || 'United States';
    const education = document.getElementById('import-cand-education')?.value?.trim() || 'B.Tech India -> MS USA';

    if (!name || !linkedinUrl) {
        showToast('Name and LinkedIn URL are required.', 'error');
        return;
    }

    try {
        const res = await fetch('/api/students/add-to-bench', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                headline: headline,
                resume_filename: linkedinUrl,
                linkedin_url: linkedinUrl,
                grad_year: year,
                bachelor_year: year,
                location: location,
                university: education,
                summary: `Imported from LinkedIn (${linkedinUrl}). India B.Tech Passout ${year}, Master's in USA.`
            })
        });

        if (res.ok) {
            showToast(`Candidate ${name} successfully added to Active Bench!`, 'success');
            closeQuickImportModal();
            document.getElementById('form-quick-import-candidate')?.reset();
            // Reload candidates table so newly imported profile appears at top
            loadStudents(false);
        } else {
            showToast('Failed to save candidate to bench.', 'error');
        }
    } catch (err) {
        console.error('Import error:', err);
        showToast('Error importing candidate: ' + err.message, 'error');
    }
}

function initStudentsTab() {
    const btnFilter = document.getElementById('btn-apply-student-filter');
    const formSearch = document.getElementById('form-student-search');
    const btnExport = document.getElementById('btn-export-students-csv');
    const btnClosePitch = document.getElementById('btn-close-pitch');
    const btnClosePitchX = document.getElementById('btn-close-pitch-modal');
    const btnCopyPitch = document.getElementById('btn-copy-pitch');

    const btnXRay = document.getElementById('btn-open-live-xray');
    if (btnXRay) {
        btnXRay.addEventListener('click', launchLiveXRaySearch);
    }
    const btnLiveLi = document.getElementById('btn-open-live-linkedin');
    if (btnLiveLi) {
        btnLiveLi.addEventListener('click', launchLiveLinkedInSearch);
    }
    const btnQuickImport = document.getElementById('btn-open-quick-import');
    if (btnQuickImport) {
        btnQuickImport.addEventListener('click', openQuickImportModal);
    }
    const btnCloseImport = document.getElementById('btn-close-import-modal');
    if (btnCloseImport) {
        btnCloseImport.addEventListener('click', closeQuickImportModal);
    }
    const btnCancelImport = document.getElementById('btn-cancel-import');
    if (btnCancelImport) {
        btnCancelImport.addEventListener('click', closeQuickImportModal);
    }
    const formImport = document.getElementById('form-quick-import-candidate');
    if (formImport) {
        formImport.addEventListener('submit', submitQuickImport);
    }

    // Only an explicit Search click starts the paid live LinkedIn search.
    // Changing the year dropdown just refreshes the free bench-roster view.
    if (formSearch) {
        formSearch.addEventListener('submit', (e) => {
            e.preventDefault();
            loadStudents(true);
        });
    }

    if (btnFilter) {
        btnFilter.addEventListener('click', () => {
            if (typeof eduState !== 'undefined' && eduState.filter !== 'P') eduLiveSearch();
            else loadStudents(true);
        });
    }
    if (btnExport) {
        btnExport.addEventListener('click', () => exportStudentsCSV());
    }
    if (btnClosePitch) {
        btnClosePitch.addEventListener('click', () => closeStudentPitchModal());
    }
    if (btnClosePitchX) {
        btnClosePitchX.addEventListener('click', () => closeStudentPitchModal());
    }
    if (btnCopyPitch) {
        btnCopyPitch.addEventListener('click', () => {
            const bodyText = document.getElementById('pitch-modal-body').value;
            navigator.clipboard.writeText(bodyText);
            showToast('Outreach pitch copied to clipboard!', 'success');
        });
    }

    // Explicitly expose on window
    window.setPresetFilter = setPresetFilter;
    window.loadStudents = loadStudents;
    window.renderStudentsGrid = renderStudentsGrid;
    window.onAddStudentToBench = onAddStudentToBench;
    window.onOpenStudentPitch = onOpenStudentPitch;
    window.closeStudentPitchModal = closeStudentPitchModal;
    window.exportStudentsCSV = exportStudentsCSV;

}

// =========================================================================
// 8. Utility Functions
// =========================================================================
function showToast(message, type = 'info', duration = 4000) {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerText = message;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(-10px)';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

window.buildXRayQuery = buildXRayQuery;
window.launchLiveXRaySearch = launchLiveXRaySearch;
window.launchLiveLinkedInSearch = launchLiveLinkedInSearch;
window.openQuickImportModal = openQuickImportModal;
window.closeQuickImportModal = closeQuickImportModal;
window.submitQuickImport = submitQuickImport;


// =========================================================================
// 9. Recruiter Team & Data Isolation Management (Admin Only)
// =========================================================================

async function loadRecruiters() {
    try {
        const res = await fetch('/api/admin/recruiters');
        if (!res.ok) return;
        const recruiters = await res.json();
        
        const countEl = document.getElementById('stat-team-recruiter-count');
        if (countEl) countEl.innerText = recruiters.length;

        const tbody = document.getElementById('recruiters-table-body');
        if (tbody) {
            tbody.innerHTML = recruiters.map(r => `
                <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);" id="recruiter-row-${r.id}">
                    <td style="padding: 14px 18px; display: flex; align-items: center; gap: 12px;">
                        <img src="${r.avatar_url || 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&auto=format&fit=crop&q=80'}" style="width: 34px; height: 34px; border-radius: 50%; object-fit: cover; border: 1px solid rgba(255,255,255,0.2);">
                        <span style="font-weight: 600; color: #0f172a;">${escapeHtml(r.name)}</span>
                    </td>
                    <td style="padding: 14px 18px; color: #cbd5e1;">${escapeHtml(r.email)}</td>
                    <td style="padding: 14px 18px;">
                        <span style="padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; ${r.role && r.role.includes('Admin') ? 'background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3);' : 'background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3);'}">
                            ${escapeHtml(r.role || 'Recruiter')}
                        </span>
                    </td>
                    <td style="padding: 14px 18px;">
                        <select class="form-control recruiter-region" data-id="${r.id}" style="width:auto; font-size:0.8rem;" onchange="setRecruiterRegion(${r.id}, this.value)">
                            <option value="USA" ${(r.region || 'USA') === 'USA' ? 'selected' : ''}>🇺🇸 USA</option>
                            <option value="India" ${r.region === 'India' ? 'selected' : ''}>🇮🇳 India</option>
                        </select>
                    </td>
                    <td style="padding: 14px 18px; font-weight: 600; color: #38bdf8;">${r.consultant_count || 0} consultants</td>
                    <td style="padding: 14px 18px;">
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-secondary btn-sm" onclick="window.onResetRecruiterPassword(${r.id}, '${escapeHtml(r.name)}')">Reset Password</button>
                            <button class="btn btn-danger btn-sm" style="background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3);" onclick="window.onDeleteRecruiter(${r.id}, '${escapeHtml(r.name)}')">Delete</button>
                        </div>
                    </td>
                </tr>
            `).join('');
        }

        const filterSelect = document.getElementById('select-consultant-recruiter-filter');
        const reassignSelect = document.getElementById('reassign-target-user-id');
        if (filterSelect) {
            const currentVal = filterSelect.value;
            filterSelect.innerHTML = `<option value="">All Recruiters (Agency Wide)</option>` + 
                recruiters.map(r => `<option value="${r.id}" ${currentVal == r.id ? 'selected' : ''}>${escapeHtml(r.name)} (${r.consultant_count} consultants)</option>`).join('');
        }
        if (reassignSelect) {
            reassignSelect.innerHTML = recruiters.map(r => `<option value="${r.id}">${escapeHtml(r.name)} (${escapeHtml(r.email)})</option>`).join('');
        }
    } catch (err) {
        console.error('Error loading recruiters:', err);
    }
}
window.loadRecruiters = loadRecruiters;

function openAddRecruiterModal() {
    const modal = document.getElementById('modal-add-recruiter');
    if (modal) {
        modal.style.display = 'flex';
        const nameInp = document.getElementById('rec-name');
        if (nameInp) nameInp.focus();
    }
}
window.openAddRecruiterModal = openAddRecruiterModal;

function closeAddRecruiterModal() {
    const modal = document.getElementById('modal-add-recruiter');
    if (modal) modal.style.display = 'none';
}
window.closeAddRecruiterModal = closeAddRecruiterModal;

async function submitAddRecruiter(e) {
    e.preventDefault();
    const name = document.getElementById('rec-name')?.value?.trim();
    const email = document.getElementById('rec-email')?.value?.trim();
    const password = document.getElementById('rec-password')?.value?.trim();
    const role = document.getElementById('rec-role')?.value || 'Recruiter';
    const region = document.getElementById('rec-region')?.value || 'USA';

    if (!name || !email || !password) {
        showToast('Name, email, and password are required.', 'error');
        return;
    }

    try {
        const res = await fetch('/api/admin/recruiters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, password, role, region })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Recruiter ${name} created successfully!`, 'success');
            closeAddRecruiterModal();
            document.getElementById('form-add-recruiter')?.reset();
            await loadRecruiters();
        } else {
            showToast(data.error || 'Failed to create recruiter.', 'error');
        }
    } catch (err) {
        showToast('Error creating recruiter: ' + err.message, 'error');
    }
}
window.submitAddRecruiter = submitAddRecruiter;

async function onResetRecruiterPassword(recId, recName) {
    const newPass = prompt(`Enter new password for recruiter ${recName}:`);
    if (!newPass) return;

    try {
        const res = await fetch(`/api/admin/recruiters/${recId}/reset-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ new_password: newPass })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Password updated successfully for ${recName}!`, 'success');
        } else {
            showToast(data.error || 'Failed to update password.', 'error');
        }
    } catch (err) {
        showToast('Error updating password: ' + err.message, 'error');
    }
}
window.onResetRecruiterPassword = onResetRecruiterPassword;

async function onDeleteRecruiter(recId, recName) {
    if (!confirm(`Are you sure you want to delete recruiter "${recName}"?\n\nAll of their candidates will be safely reassigned to Admin.`)) {
        return;
    }

    try {
        const res = await fetch(`/api/admin/recruiters/${recId}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Recruiter ${recName} deleted. Candidates transferred to Admin.`, 'success');
            await loadRecruiters();
            await fetchConsultants();
        } else {
            showToast(data.error || 'Failed to delete recruiter.', 'error');
        }
    } catch (err) {
        showToast('Error deleting recruiter: ' + err.message, 'error');
    }
}
window.onDeleteRecruiter = onDeleteRecruiter;

function openReassignModal(candId, candName) {
    const modal = document.getElementById('modal-reassign-consultant');
    const idInput = document.getElementById('reassign-cand-id');
    const nameEl = document.getElementById('reassign-cand-name');
    if (modal && idInput && nameEl) {
        idInput.value = candId;
        nameEl.innerText = candName;
        modal.style.display = 'flex';
    }
}
window.openReassignModal = openReassignModal;

function closeReassignModal() {
    const modal = document.getElementById('modal-reassign-consultant');
    if (modal) modal.style.display = 'none';
}
window.closeReassignModal = closeReassignModal;

async function submitReassign(e) {
    e.preventDefault();
    const candId = document.getElementById('reassign-cand-id')?.value;
    const targetUserId = document.getElementById('reassign-target-user-id')?.value;

    if (!candId || !targetUserId) {
        showToast('Candidate and target recruiter are required.', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/admin/candidates/${candId}/reassign`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ assigned_user_id: targetUserId })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast('Consultant transferred successfully!', 'success');
            closeReassignModal();
            await fetchConsultants();
            await loadRecruiters();
        } else {
            showToast(data.error || 'Failed to reassign candidate.', 'error');
        }
    } catch (err) {
        showToast('Error reassigning candidate: ' + err.message, 'error');
    }
}
window.submitReassign = submitReassign;

// Delegate click on transfer buttons
document.addEventListener('click', (e) => {
    const btn = e.target.closest('.btn-trigger-reassign');
    if (btn) {
        const candId = btn.getAttribute('data-id');
        const candName = btn.getAttribute('data-name');
        openReassignModal(candId, candName);
    }
});


// =========================================================================
// Executive Candidates Table View (Row layout with Head Titles)
// =========================================================================
function renderConsultantsTable() {
    const tbody = document.getElementById('consultants-table-body');
    if (!tbody) {
        renderConsultantsGrid();
        return;
    }

    if (!state.consultants || state.consultants.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" style="text-align: center; padding: 40px; color: #64748b;">
                    No candidates found. Click <strong>"Add New Candidate Profile"</strong> to add your first bench candidate.
                </td>
            </tr>`;
        return;
    }

    tbody.innerHTML = state.consultants.map(c => {
        const initials = (c.name || "C").split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const hasResume = Boolean(c.resume_filename || c.resume_path);
        const gmailConnected = Boolean(c.gmail_connected);
        const cleanName = (c.name || "Consultant").replace(/[,\/]/g, '').trim();
        const liUrl = (c.linkedin_url && c.linkedin_url.startsWith('http') && !c.linkedin_url.endsWith('-devops/') && !c.linkedin_url.endsWith('-data-analyst/'))
            ? c.linkedin_url
            : `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(cleanName)}&origin=GLOBAL_SEARCH_HEADER`;

        return `
        <tr style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'">
            <td style="padding: 14px 18px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 36px; height: 36px; border-radius: 50%; background: #eff6ff; color: #2563eb; font-weight: 700; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; border: 1px solid #bfdbfe; flex-shrink: 0;">
                        ${initials}
                    </div>
                    <div>
                        <div style="font-weight: 700; color: #0f172a; display: flex; align-items: center; gap: 6px;">
                            <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color: inherit; text-decoration: none;" onmouseover="this.style.color='#2563eb'" onmouseout="this.style.color='inherit'">
                                ${escapeHtml(c.name)}
                            </a>
                            <a href="${liUrl}" target="_blank" rel="noopener noreferrer" style="color: #0284c7; font-size: 0.75rem; text-decoration: none;" title="LinkedIn Profile">↗</a>
                        </div>
                        <div style="font-size: 0.78rem; color: #64748b; margin-top: 2px;">${escapeHtml(c.title || 'Technical Consultant')}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 14px 18px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.78rem; font-weight: 700; background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0;">
                    ${escapeHtml(c.target_rate || '—')}
                </span>
            </td>
            <td style="padding: 14px 18px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.78rem; font-weight: 600; background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;">
                    ${escapeHtml(c.visa_status || (c.country === 'India' ? 'India-based' : 'Visa not set'))}
                </span>
            </td>
            <td style="padding: 14px 18px; font-weight: 600; color: #334155;">
                ${c.experience_years ? c.experience_years + '+ Yrs' : '—'}
            </td>
            <td style="padding: 14px 18px; color: #64748b; font-size: 0.85rem;">
                <span title="${(c.country || 'United States') === 'India' ? 'India market' : 'US market'}">${(c.country || 'United States') === 'India' ? '🇮🇳' : '🇺🇸'}</span>
                ${escapeHtml(c.location || 'United States')}
            </td>
            <td style="padding: 14px 18px;">
                <span style="font-weight: 600; color: #0284c7; font-size: 0.85rem;">
                    👤 ${escapeHtml(c.recruiter_name || 'Assigned')}
                </span>
            </td>
            <td style="padding: 14px 18px;">
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    <button
                        onclick="browseJobsForCandidate(${c.id}, '${escapeHtml(c.title || '').replace(/'/g, '')}', '${escapeHtml(c.country || 'United States').replace(/'/g, '')}')"
                        style="display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer; white-space: nowrap; transition: background 0.15s ease;"
                        onmouseover="this.style.background='#dbeafe'" onmouseout="this.style.background='#eff6ff'"
                        title="Find matching 24h jobs for this candidate">
                        🔍 Browse Jobs
                    </button>
                    <button type="button" class="btn-open-tracker" onclick="openTracker(${c.id})"
                        style="display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; background: #f5f3ff; color: #6d28d9; border: 1px solid #c4b5fd; border-radius: 6px; font-size: 0.75rem; font-weight: 600; cursor: pointer; white-space: nowrap;"
                        title="Every application of this consultant: status, follow-ups, interviews, history">
                        📋 Applications
                    </button>
                    <span id="job-match-count-${c.id}" style="font-size: 0.72rem; color: #64748b; font-weight: 500; padding-left: 2px;"></span>
                </div>
            </td>
            <td style="padding: 14px 18px;">
                ${hasResume ? `
                    <span style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0;">
                        📄 .DOCX Ready
                    </span>
                ` : `
                    <button class="btn btn-xs btn-upload-cand-resume" data-id="${c.id}" data-name="${escapeHtml(c.name)}" style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #fffbeb; color: #b45309; border: 1px solid #fde68a; cursor:pointer;">
                        ⚠️ Upload Resume
                    </button>
                `}
            </td>
            <td style="padding: 14px 18px;">
                <div style="display:flex; flex-direction:column; gap:6px; align-items:flex-start;">
                    ${gmailConnected ? `
                        <span style="display: inline-flex; align-items: center; gap: 6px; font-size: 0.78rem; font-weight: 600; color: #059669;">
                            <span style="width: 7px; height: 7px; border-radius: 50%; background: #059669;"></span> Connected
                        </span>
                        ${c.gmail_account ? `<span style="font-size:0.72rem; color:#475569;">${escapeHtml(c.gmail_account)}</span>` : ''}
                        <div style="display:flex; gap:4px;">${gmailManageButtons(c)}</div>
                    ` : `
                        <div style="display:flex; align-items:center; gap:4px; flex-wrap:wrap;">
                            <button class="btn btn-xs btn-open-app-pass" data-id="${c.id}" data-name="${escapeHtml(c.name)}" data-email="${escapeHtml(c.email || '')}" style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0; cursor:pointer;">
                                🔑 App Password
                            </button>
                            ${window.HAS_GOOGLE_OAUTH ? `
                            <a href="/api/consultants/${c.id}/connect-gmail" style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #eff6ff; color: #2563eb; border: 1px solid #bfdbfe; text-decoration: none;">
                                OAuth
                            </a>` : ''}
                        </div>
                    `}
                    <button class="btn btn-xs btn-paste-draft-for-candidate" data-id="${c.id}" style="display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 600; background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; cursor:pointer; white-space: nowrap;" title="Paste a job requirement and create a Gmail draft applying ${escapeHtml(c.name)} for it">
                        📋 Paste Req &amp; Draft
                    </button>
                </div>
            </td>
            <td style="padding: 14px 18px; text-align: right;">
                <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px;">
                    <button class="btn btn-sm" onclick="openAiCopilotModal(${c.id})" style="background: linear-gradient(135deg, #2563eb, #38bdf8); color: #ffffff; border: none; font-weight: 600; font-size: 0.75rem; padding: 5px 12px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px;" title="Open AI Personalization Chatbot">
                        <span>🤖 AI Outreach</span>
                    </button>
                    <button class="btn btn-secondary btn-sm btn-edit-consultant" data-id="${c.id}" style="padding: 5px 10px; font-size: 0.75rem;" title="Edit Profile">
                        ✏️
                    </button>
                    <button class="btn btn-secondary btn-sm btn-delete-consultant" data-id="${c.id}" data-name="${escapeHtml(c.name)}" style="padding: 5px 10px; font-size: 0.75rem; color: #ef4444;" title="Delete Candidate">
                        🗑️
                    </button>
                </div>
            </td>
        </tr>`;
    }).join('');

    // Attach row events
    tbody.querySelectorAll('.btn-edit-consultant').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const c = state.consultants.find(item => item.id === candId);
            if (c) openConsultantModal(c);
        });
    });

    tbody.querySelectorAll('.btn-delete-consultant').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const candName = btn.getAttribute('data-name');
            deleteConsultant(candId, candName);
        });
    });

    tbody.querySelectorAll('.btn-open-app-pass').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            const email = btn.getAttribute('data-email');
            openAppPasswordModal(candId, name, email);
        });
    });

    tbody.querySelectorAll('.btn-upload-cand-resume').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            const name = btn.getAttribute('data-name');
            openUploadResumeModal(candId, name);
        });
    });

    // Per-consultant "Paste Req & Draft": opens the SAME paste-requirement modal the header
    // button uses, pre-selected to this row's consultant - no need to also find them in the
    // dropdown. Works whether or not Gmail shows Connected yet; submitting still requires it,
    // same as every other draft path, and reports a clear error if it isn't connected.
    tbody.querySelectorAll('.btn-paste-draft-for-candidate').forEach(btn => {
        btn.addEventListener('click', () => {
            const candId = parseInt(btn.getAttribute('data-id'));
            openPasteDraftModal(candId);
        });
    });

    const filterInput = document.getElementById('filter-consultants-search');
    if (filterInput && filterInput.value.trim()) {
        const q = filterInput.value.toLowerCase().trim();
        tbody.querySelectorAll('tr').forEach(row => {
            row.style.display = row.innerText.toLowerCase().includes(q) ? '' : 'none';
        });
    }
}

// =========================================================================
// Dashboard Candidate Pipeline Table View
// =========================================================================
function renderDashboardPipeline() {
    const tbody = document.getElementById('dashboard-pipeline-tbody');
    if (!tbody) return;

    const cands = state.consultants || [];
    if (cands.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 30px; color: #64748b;">No candidate applications found yet.</td></tr>`;
        return;
    }

    const stages = [
        { stage: "Tech Interview", color: "#2563eb", bg: "#eff6ff", border: "#bfdbfe", status: "Active" },
        { stage: "Offer Sent", color: "#d97706", bg: "#fffbeb", border: "#fde68a", status: "Offer" },
        { stage: "Sourcing", color: "#059669", bg: "#ecfdf5", border: "#a7f3d0", status: "Sourcing" },
        { stage: "Client Review", color: "#7c3aed", bg: "#f5f3ff", border: "#ddd6fe", status: "Active" },
        { stage: "Tech Interview", color: "#2563eb", bg: "#eff6ff", border: "#bfdbfe", status: "Active" }
    ];

    const targetRoles = [
        "Applied for Senior Cloud / AWS Developer",
        "Lead Full-Stack Java Engineer (Remote)",
        "Principal AI & Automation Architect",
        "Senior DevOps / Kubernetes Engineer",
        "Data Platform & Snowflake Specialist"
    ];

    const dates = ["09/20/26", "09/19/26", "09/18/26", "09/16/26", "09/15/26"];

    tbody.innerHTML = cands.slice(0, 8).map((c, idx) => {
        const initials = (c.name || "C").split(' ').map(n => n[0]).join('').substring(0, 2).toUpperCase();
        const stageInfo = stages[idx % stages.length];
        const roleStr = targetRoles[idx % targetRoles.length];
        const dateStr = dates[idx % dates.length];
        const recruiterName = c.recruiter_name || "Praveen Valipireddy";

        return `
        <tr style="border-bottom: 1px solid #f1f5f9; transition: background 0.15s ease;" onmouseover="this.style.background='#f8fafc'" onmouseout="this.style.background='transparent'">
            <td style="padding: 14px 20px;">
                <div style="display: flex; align-items: center; gap: 12px;">
                    <div style="width: 36px; height: 36px; border-radius: 50%; background: #f1f5f9; color: #1e293b; font-weight: 700; font-size: 0.85rem; display: flex; align-items: center; justify-content: center; border: 1px solid #cbd5e1; flex-shrink: 0;">
                        ${initials}
                    </div>
                    <div>
                        <div style="font-weight: 700; color: #0f172a;">${escapeHtml(c.name)}</div>
                        <div style="font-size: 0.78rem; color: #64748b; margin-top: 2px;">${escapeHtml(c.title || 'Technical Consultant')}</div>
                    </div>
                </div>
            </td>
            <td style="padding: 14px 20px; font-weight: 600; color: #334155; font-size: 0.85rem;">
                ${escapeHtml(roleStr)}
            </td>
            <td style="padding: 14px 20px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; background: ${stageInfo.bg}; color: ${stageInfo.color}; border: 1px solid ${stageInfo.border};">
                    ${stageInfo.stage}
                </span>
            </td>
            <td style="padding: 14px 20px; color: #64748b; font-size: 0.85rem;">
                ${dateStr}
            </td>
            <td style="padding: 14px 20px; font-size: 0.85rem; color: #0f172a; font-weight: 600;">
                ${escapeHtml(recruiterName)}
            </td>
            <td style="padding: 14px 20px;">
                <span style="display: inline-block; padding: 3px 10px; border-radius: 9999px; font-size: 0.75rem; font-weight: 700; background: ${stageInfo.bg}; color: ${stageInfo.color}; border: 1px solid ${stageInfo.border};">
                    ${stageInfo.status}
                </span>
            </td>
        </tr>`;
    }).join('');
}

// =========================================================================
// Recruiter AI Outreach Personalization Chatbot
// =========================================================================
let currentCopilotCandId = null;
let currentCopilotJobId = null;

let currentCopilotVersionId = null;

async function openAiCopilotModal(candId, jobId = null, opts = {}) {
    currentCopilotVersionId = opts.versionId || null;
    currentCopilotCandId = candId || (state.consultants && state.consultants.length > 0 ? state.consultants[0].id : 1);
    
    if (!jobId && state.jobs && state.jobs.length > 0) {
        currentCopilotJobId = state.jobs[0].id;
    } else {
        currentCopilotJobId = jobId || 1;
    }

    const cand = (state.consultants || []).find(c => c.id === currentCopilotCandId) || {};
    const job = (state.jobs || []).find(j => j.id === currentCopilotJobId) || {};

    const modal = document.getElementById('modal-ai-copilot');
    if (!modal) return;

    const subEl = document.getElementById('copilot-context-subtitle');
    if (subEl) {
        subEl.innerText = `Personalizing pitch for ${cand.name || 'Candidate'} → ${job.title || 'Technical Role'} (${job.company || 'Hiring Team'})`;
    }
    const toEmailEl = document.getElementById('copilot-to-email');
    if (toEmailEl) toEmailEl.value = job.recruiter_email || '';
    copilotUndoStack = [];
    copilotSyncUndo();
    const changedNote = document.getElementById('copilot-changed-note');
    if (changedNote) changedNote.textContent = '';

    const msgContainer = document.getElementById('copilot-chat-messages');
    if (msgContainer) {
        msgContainer.innerHTML = `
            <div style="display: flex; gap: 10px; align-items: flex-start;">
                <div style="width: 28px; height: 28px; border-radius: 50%; background: #eff6ff; color: #2563eb; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; flex-shrink: 0;">🤖</div>
                <div style="background: #f1f5f9; color: #1e293b; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 88%; line-height: 1.5;" id="copilot-welcome">
                    Here is the standard email for <strong>${escapeHtml(cand.name || 'Candidate')}</strong>. Tell me what to change - tone, length, availability, skills to highlight. I only use facts saved on the consultant's profile; if something is missing I'll ask you instead of making it up.
                </div>
            </div>
        `;
    }

    try {
        const res = await fetch('/api/ai/personalize-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                instruction: '',
                current_subject: '',
                current_body: '',
                jd_text: opts.jdText || '',
                to_email: opts.toEmail || '',
                optimized_resume_id: currentCopilotVersionId
            })
        });
        const data = await res.json();
        if (data.success) {
            const subjEl = document.getElementById('copilot-subject');
            if (subjEl) subjEl.value = data.subject || '';
            const bodyEl = document.getElementById('copilot-body');
            if (bodyEl) bodyEl.value = data.body || '';
            if (data.to_email && toEmailEl && !toEmailEl.value) toEmailEl.value = data.to_email;
            const resName = document.getElementById('copilot-resume-name');
            if (resName) resName.textContent = data.attachment ? `${data.attachment} (${data.attachment_kind})` : 'No resume on file - the email will go without an attachment';
            const bccInfo = document.getElementById('copilot-bcc-info');
            if (bccInfo) bccInfo.textContent = (data.bcc || []).length ? `BCC (your vendor contacts at this company): ${data.bcc.join(', ')}` : '';
            const rateChip = document.getElementById('copilot-chip-rate');
            if (rateChip) rateChip.style.display = data.rate_on_file ? '' : 'none';
            if (data.to_email && toEmailEl) toEmailEl.value = data.to_email;
            const cl = document.getElementById('copilot-checklist');
            if (cl) {
                const items = data.checklist || [];
                cl.innerHTML = items.length ? '<b style="color:#334155;">What this job asks</b><br>' + items.map(i =>
                    `<span style="color:${i.ok ? '#059669' : '#b45309'};">${i.ok ? '✓' : '⚠'} ${escapeHtml(i.text)}</span>`).join('<br>') : '';
                cl.style.display = items.length ? '' : 'none';
                const welcome = document.getElementById('copilot-welcome');
                if (welcome && items.length) welcome.innerHTML = `Here is the application email for <strong>${escapeHtml(cand.name || 'the consultant')}</strong>, written from the job description and the resume that will be attached. Check the ⚠ items above the email (and any [please fill] parts) before saving. Tell me what to change.`;
            }
        }
    } catch (e) {
        console.error('Error fetching baseline pitch:', e);
    }

    modal.style.display = 'flex';
    const input = document.getElementById('copilot-user-input');
    if (input) input.focus();
}

let copilotUndoStack = [];

function copilotSyncUndo() {
    const b = document.getElementById('btn-copilot-undo');
    if (b) b.disabled = !copilotUndoStack.length;
}

function undoCopilotChange() {
    const prev = copilotUndoStack.pop();
    if (!prev) return;
    document.getElementById('copilot-subject').value = prev.subject;
    document.getElementById('copilot-body').value = prev.body;
    const note = document.getElementById('copilot-changed-note');
    if (note) note.textContent = '↶ Undone';
    copilotSyncUndo();
}

function closeAiCopilotModal() {
    const modal = document.getElementById('modal-ai-copilot');
    if (modal) modal.style.display = 'none';
}

function applyCopilotQuickPrompt(promptText) {
    const input = document.getElementById('copilot-user-input');
    if (input) {
        input.value = promptText;
        sendCopilotInstruction();
    }
}

async function sendCopilotInstruction() {
    const input = document.getElementById('copilot-user-input');
    const instruction = (input.value || '').trim();
    if (!instruction) return;

    input.value = '';
    const msgContainer = document.getElementById('copilot-chat-messages');
    if (msgContainer) {
        msgContainer.innerHTML += `
            <div style="display: flex; gap: 10px; align-items: flex-start; justify-content: flex-end;">
                <div style="background: #2563eb; color: #ffffff; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 85%; line-height: 1.5;">
                    ${escapeHtml(instruction)}
                </div>
                <div style="width: 28px; height: 28px; border-radius: 50%; background: #e2e8f0; color: #475569; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; flex-shrink: 0;">YOU</div>
            </div>
        `;
        msgContainer.scrollTop = msgContainer.scrollHeight;
    }

    const sendBtn = document.getElementById('btn-copilot-send');
    if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerText = 'Thinking...';
    }

    try {
        const curSubject = document.getElementById('copilot-subject') ? document.getElementById('copilot-subject').value : '';
        const curBody = document.getElementById('copilot-body') ? document.getElementById('copilot-body').value : '';

        const res = await fetch('/api/ai/personalize-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                instruction: instruction,
                current_subject: curSubject,
                current_body: curBody
            })
        });

        const data = await res.json();
        if (data.success) {
            const subjEl = document.getElementById('copilot-subject');
            const bodyEl = document.getElementById('copilot-body');
            const note = document.getElementById('copilot-changed-note');
            if (data.applied) {
                copilotUndoStack.push({ subject: curSubject, body: curBody });
                copilotSyncUndo();
                if (subjEl) subjEl.value = data.subject;
                if (bodyEl) {
                    bodyEl.value = data.body;
                    bodyEl.style.boxShadow = '0 0 0 3px #bbf7d0';
                    setTimeout(() => { bodyEl.style.boxShadow = ''; }, 1500);
                }
                if (note) note.textContent = `✓ ${data.lines_changed} line${data.lines_changed === 1 ? '' : 's'} changed`;
            } else if (note) {
                note.textContent = '';
            }
            const changes = (data.changes || []).length ? `<ul style="margin:6px 0 0 18px; padding:0;">${data.changes.map(c => `<li>${escapeHtml(c)}</li>`).join('')}</ul>` : '';
            const needs = (data.needs_info || []).length ? `<div style="margin-top:6px; padding:6px 8px; border-radius:8px; background:#fffbeb; border:1px solid #fde68a; color:#92400e;">❓ ${data.needs_info.map(escapeHtml).join('<br>❓ ')}</div>` : '';
            const tone = data.applied ? '#f1f5f9' : '#fef2f2';
            if (msgContainer) {
                msgContainer.innerHTML += `
                    <div class="copilot-reply" style="display: flex; gap: 10px; align-items: flex-start;">
                        <div style="width: 28px; height: 28px; border-radius: 50%; background: #eff6ff; color: #2563eb; display: flex; align-items: center; justify-content: center; font-size: 0.9rem; flex-shrink: 0;">🤖</div>
                        <div style="background: ${tone}; color: #1e293b; padding: 10px 14px; border-radius: 12px; font-size: 0.85rem; max-width: 88%; line-height: 1.5;">
                            ${escapeHtml(data.reply || '')}${changes}${needs}
                        </div>
                    </div>
                `;
                msgContainer.scrollTop = msgContainer.scrollHeight;
            }
        } else {
            showToast(data.error || 'Failed to personalize pitch', 'error');
        }
    } catch (e) {
        showToast('Error communicating with AI assistant: ' + e.message, 'error');
    } finally {
        if (sendBtn) {
            sendBtn.disabled = false;
            sendBtn.innerText = 'Send';
        }
    }
}

async function submitCopilotDraftToGmail() {
    const toEmailEl = document.getElementById('copilot-to-email');
    const toEmail = toEmailEl ? toEmailEl.value.trim() : '';
    const subjectEl = document.getElementById('copilot-subject');
    const subject = subjectEl ? subjectEl.value.trim() : '';
    const bodyEl = document.getElementById('copilot-body');
    const body = bodyEl ? bodyEl.value.trim() : '';
    const saveBtn = document.getElementById('btn-copilot-save-draft');

    if (/\[please (fill|add):/i.test(body) && !confirm('The email still has [please fill / add] parts. Save it to Gmail anyway and fill them there?')) return;
    if (!toEmail || !toEmail.includes('@')) {
        showToast('Please enter a valid recipient recruiter email', 'warning');
        if (toEmailEl) toEmailEl.focus();
        return;
    }

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = 'Saving to Gmail...';
    }

    try {
        const res = await fetch('/api/outreach/create-draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                candidate_id: currentCopilotCandId,
                job_id: currentCopilotJobId,
                custom_to_email: toEmail,
                custom_subject: subject,
                custom_body: body,
                optimized_resume_id: currentCopilotVersionId
            })
        });

        const data = await res.json();
        if (data.success) {
            showToast(`✉️ Personalized Gmail Draft successfully saved for ${data.candidate_name}!`, 'success', 6000);
            if (!data.resume_attached) {
                showToast('⚠️ ' + (data.resume_note || 'No resume was attached - none is on file for this consultant.'), 'warning', 9000);
            }
            closeAiCopilotModal();
            const statDrafts = document.getElementById('stat-drafted-count');
            if (statDrafts) statDrafts.innerText = (parseInt(statDrafts.innerText) || 0) + 1;
            const statKpiDrafts = document.getElementById('stat-kpi-drafts');
            if (statKpiDrafts) statKpiDrafts.innerText = (parseInt(statKpiDrafts.innerText) || 0) + 1;
        } else {
            showToast(data.error || 'Failed to save draft in Gmail', 'error', 6000);
        }
    } catch (e) {
        showToast('Error creating draft: ' + e.message, 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerText = '🚀 Save to Gmail Draft';
        }
    }
}
// =========================================================================
// Candidate -> Jobs: Browse matching live jobs for a specific candidate
// =========================================================================
// A pasted requirement ("Manual Paste") keeps the full job description. Scraped postings only keep a
// one-line summary, so for those the recruiter is asked to paste the full description from the posting.
const FULL_JD_MIN_CHARS = 400;
// LinkedIn / Dice postings: the server reads the full description from the posting (free).
const JD_AUTO_FETCH_RE = /^https?:\/\/([a-z0-9-]+\.)*(linkedin\.com\/jobs|dice\.com\/job-detail)\//i;

function setActiveConsultant(candidateId) {
    const id = parseInt(candidateId);
    if (!id || !(state.consultants || []).some(c => c.id === id)) return;
    state.activeConsultantId = id;
    ['global-active-consultant', 'pd-consultant-select'].forEach(selId => {
        const sel = document.getElementById(selId);
        if (sel) sel.value = String(id);
    });
    updateActiveConsultantUI();
    updateTableConsultantSelects();
}

async function browseJobsForCandidate(candidateId, candidateTitle, candidateCountry) {
    // The consultant's experience drives the Jobs experience filter (Any when not on file).
    const consultant = (state.consultants || []).find(c => c.id === parseInt(candidateId));
    setJobsExperience(consultant && consultant.experience_years ? consultant.experience_years : null);

    // 0. This consultant becomes the Target Candidate on every job row (it used to stay on
    //    whoever was selected before, e.g. the first consultant, instead of the one clicked).
    setActiveConsultant(candidateId);

    // 1. Switch to the Jobs tab first
    switchTab('jobs');

    // 2. Wait a tick for the tab pane to become visible, then set the
    //    search query to the candidate title and trigger the job search
    setTimeout(async () => {
        const queryInput = document.getElementById('filter-query');
        if (queryInput && candidateTitle) {
            // Use first 2-3 words of title for a broad but relevant search
            const coreTitle = candidateTitle
                .replace(/^(senior|lead|principal|staff|junior|sr|jr|associate)\s+/i, '')
                .replace(/[|&,]/g, ' ')
                .trim()
                .split(/\s+/)
                .slice(0, 3)
                .join(' ');
            queryInput.value = coreTitle || candidateTitle;
        }

        // Match this candidate to THEIR OWN market: a consultant based in India gets India
        // job postings (Naukri/Foundit/LinkedIn), a US-based consultant gets US postings.
        const countrySelect = document.getElementById('filter-country');
        const country = candidateCountry === 'India' ? 'India' : 'United States';
        if (countrySelect) countrySelect.value = country;
        syncJobsMarketUi();

        // Reset location to the candidate's country so we get results
        const locInput = document.getElementById('filter-location');
        if (locInput && !locInput.value.trim()) locInput.value = country;

        // Trigger the job search
        await searchJobs(false);
        updateTableConsultantSelects();
        autoVendorDrafts(parseInt(candidateId));

        // After results load, update the match-count badge on the candidate row
        setTimeout(() => {
            const matchSpan = document.getElementById('job-match-count-' + candidateId);
            if (matchSpan) {
                const count = (state.jobs || []).length;
                matchSpan.textContent = count > 0
                    ? count + ' job' + (count === 1 ? '' : 's') + ' found'
                    : 'No matches yet';
                matchSpan.style.color = count > 0 ? '#059669' : '#94a3b8';
            }
        }, 3000);
    }, 200);
}

// =========================================================================
// Education Filters (Sourcing): Filter A = Indian college -> US Master's,
// Filter B = US university -> Indian undergrad. Reads the team's saved
// LinkedIn profiles via /api/education/* - free, never calls Apify.
// =========================================================================
const eduState = {
    filter: 'P',
    searchedOnce: false,
    institutions: { A: null, B: null },
    selected: { A: null, B: null },
    page: 1,
    pages: 1,
    lastParams: null,
};

const EDU_TEXT = {
    A: { label: "Indian college (Bachelor's)", placeholder: 'Type a college, e.g. JNTUH, Osmania, VIT',
         hint: "Year range applies to the Bachelor's at the chosen college. With a range set, entries with no year are left out." },
    B: { label: "US university (Master's)", placeholder: 'Type a university, e.g. UT Dallas, UNT, NJIT',
         hint: "Year range applies to the Master's at the chosen university. With a range set, entries with no year are left out." },
};

async function eduLoadInstitutions(filter) {
    if (eduState.institutions[filter]) return eduState.institutions[filter];
    const res = await fetch(`/api/education/institutions?filter=${filter}`);
    if (res.status === 401) { window.location.href = '/login'; return []; }
    const data = await res.json().catch(() => ({}));
    eduState.institutions[filter] = data.institutions || [];
    return eduState.institutions[filter];
}

function eduRenderInstList(query) {
    const list = document.getElementById('edu-inst-list');
    if (!list) return;
    const insts = eduState.institutions[eduState.filter] || [];
    const q = (query || '').trim().toLowerCase();
    // Look-alike spellings match too ("srinidhi" finds "Sreenidhi", "sri"/"sree", single/double letters).
    // Generic words ("college of engineering") are ignored, so the distinctive name decides.
    const keyWords = q.split(/[^a-z0-9]+/).filter(w => w.length >= 3 && !EDU_GENERIC_WORDS.has(w)).map(eduSoundsLike).filter(w => w.length >= 3);
    const soundsMatch = i => keyWords.length > 0 && [i.name, ...(i.aliases || [])].some(a => {
        const sa = eduSoundsLike(a);
        return keyWords.every(w => sa.includes(w));
    });
    const hits = insts.filter(i => !q
        || i.name.toLowerCase().includes(q)
        || (i.city || '').toLowerCase().includes(q)
        || (i.aliases || []).some(a => a.toLowerCase().includes(q))
        || soundsMatch(i)).slice(0, 15);
    if (!hits.length) {
        const how = (window.currentUserRole || '').includes('Admin') || document.getElementById('add-college-panel')
            ? 'Add it in Admin &amp; Settings &rarr; "Add a college / university".' : 'Ask an admin to add it in Admin &amp; Settings.';
        list.innerHTML = `<div style="padding:10px 12px; color:#64748b; font-size:13px;">No college in the list matches "${escapeHtml(query)}". ${how}</div>`;
    } else {
        list.innerHTML = hits.map(i => `
            <div class="edu-inst-option" role="option" data-id="${escapeHtml(i.id)}" style="padding:8px 12px; cursor:pointer; border-bottom:1px solid #f1f5f9; display:flex; justify-content:space-between; gap:10px;">
                <span><b style="color:#0f172a;">${escapeHtml(i.name)}</b>${i.group ? ' <span style="font-size:10px; font-weight:800; color:#1d4ed8; background:#eff6ff; border:1px solid #bfdbfe; border-radius:4px; padding:1px 5px;">ALL CAMPUSES</span>' : ''}${i.city ? `<span style="color:#64748b;"> - ${escapeHtml(i.city)}</span>` : ''}${i.group
                    ? `<br><span style="color:#94a3b8; font-size:11px;">Includes: ${escapeHtml((i.members || []).join('; '))}</span>`
                    : ((i.aliases || []).length ? `<br><span style="color:#94a3b8; font-size:11px;">${escapeHtml(i.aliases.slice(0, 4).join(', '))}</span>` : '')}</span>
                <span style="color:${i.profiles ? '#047857' : '#94a3b8'}; font-size:12px; white-space:nowrap;">${i.profiles} candidate${i.profiles === 1 ? '' : 's'}</span>
            </div>`).join('');
    }
    list.style.display = 'block';
}

function eduSelectInstitution(id) {
    const inst = (eduState.institutions[eduState.filter] || []).find(i => i.id === id);
    if (!inst) return;
    // A different college: the previous search's summary no longer describes what's on screen.
    const prev = eduState.selected[eduState.filter];
    if ((!prev || prev.id !== inst.id) && !eduLiveRunning && !studentsLiveSearchRunning) setStudentsSearchStatus('');
    eduState.selected[eduState.filter] = inst;
    const input = document.getElementById('edu-inst-input');
    if (input) input.value = inst.name;
    const list = document.getElementById('edu-inst-list');
    if (list) list.style.display = 'none';
    eduSearch(1);
}

async function eduSwitchFilter(filter) {
    eduState.filter = filter;
    document.querySelectorAll('.edu-tab').forEach(btn => {
        const on = btn.getAttribute('data-filter') === filter;
        btn.style.background = on ? '#2563eb' : '#ffffff';
        btn.style.color = on ? '#ffffff' : '#334155';
        btn.style.borderColor = on ? '#2563eb' : '#cbd5e1';
    });
    const isP = filter === 'P';
    const isAll = filter === 'ALL';
    document.querySelectorAll('.edu-only-p').forEach(el => { el.style.display = isP ? '' : 'none'; });
    if (!eduLiveRunning && !studentsLiveSearchRunning) setStudentsSearchStatus('');
    document.querySelectorAll('.edu-only-ab').forEach(el => { el.style.display = (isP || isAll) ? 'none' : ''; });
    // The paid LinkedIn search needs a passout year or a college, so it isn't offered on "All saved profiles".
    const live = document.getElementById('btn-apply-student-filter');
    if (live) live.style.display = isAll ? 'none' : '';
    if (!isP && !isAll) {
        const t = EDU_TEXT[filter];
        const label = document.getElementById('edu-inst-label');
        const input = document.getElementById('edu-inst-input');
        if (label) label.textContent = t.label;
        const yl = document.getElementById('edu-year-label');
        if (yl) yl.textContent = filter === 'A' ? "Bachelor's passout year" : "Master's passout year";
        if (input) { input.placeholder = t.placeholder; input.value = eduState.selected[filter] ? eduState.selected[filter].name : ''; }
    }
    document.getElementById('edu-results-wrap').style.display = 'none';
    document.getElementById('edu-pager').style.display = 'none';
    document.getElementById('btn-edu-export').disabled = true;
    eduSetStatus('');
    if (isP || isAll) { eduSearch(1); return; }
    await eduLoadInstitutions(filter);
    if (eduState.selected[filter]) eduSearch(1);
}

// Paid LinkedIn search for the college / university chosen in Filter A or B (Apify, same caps as
// the Passout search: one page = 25 profiles, up to 6 pages / about $1.20, stops at 15 matches).
// Every scanned profile is saved; verified people appear in the table when it finishes.
let eduLiveRunning = false;
const EDU_LIVE_MAX_PAGES = 6;
const EDU_LIVE_TARGET = 15;

async function eduLiveSearch() {
    const f = eduState.filter;
    const inst = eduState.selected[f];
    if (!inst) {
        eduSetStatus(`<span style="color:#b45309;">Pick ${f === 'A' ? 'an Indian college' : 'a US university'} first, then click Search LinkedIn.</span>`);
        return;
    }
    const passoutYear = document.getElementById('edu-passout-year')?.value || '';
    if (!passoutYear) {
        eduSetStatus(`<span style="color:#b45309;">Choose the ${f === 'A' ? "Bachelor's" : "Master's"} passout year first, then click Search LinkedIn - only people who passed out in exactly that year are kept.</span>`);
        return;
    }
    if (eduLiveRunning || studentsLiveSearchRunning) {
        showToast('A LinkedIn search is already running - please wait for it to finish.', 'info');
        return;
    }
    eduLiveRunning = true;
    const btn = document.getElementById('btn-apply-student-filter');
    const btnHtml = btn ? btn.innerHTML : '';
    if (btn) { btn.disabled = true; btn.innerHTML = '<span>Searching LinkedIn...</span>'; }
    const jsonHeaders = { 'Content-Type': 'application/json' };
    const body = { filter: f, institution_id: inst.id };
    body.year_from = passoutYear;
    body.year_to = passoutYear;
    const pollMs = (typeof window.EDU_LIVE_POLL_MS === 'number') ? window.EDU_LIVE_POLL_MS : 4000;   // tests shorten this
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const what = f === 'A' ? `a Bachelor's from <b>${escapeHtml(inst.name)}</b> and a US Master's`
        : `a Master's from <b>${escapeHtml(inst.name)}</b> and an Indian Bachelor's`;
    let pages = 0, scanned = 0, found = 0, banked = 0, cost = 0, stopMsg = '', exhausted = false;
    const harvestRuns = [];
    const progress = () => setStudentsSearchStatus(`Searching LinkedIn for people with ${what}: scanned <b>${scanned}</b> profiles, <b>${found}</b> verified so far...`);
    try {
        let maxPages = EDU_LIVE_MAX_PAGES;
        while (pages < maxPages && found < EDU_LIVE_TARGET) {
            progress();
            const sr = await fetch('/api/education/live-start', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) });
            if (sr.status === 401) { window.location.href = '/login'; return; }
            const sd = await sr.json().catch(() => ({}));
            if (!sr.ok) { stopMsg = sd.error || 'Could not start the LinkedIn search.'; break; }
            const run = (sd.runs || [])[0];
            if (!run) { stopMsg = 'Could not start the LinkedIn search.'; break; }
            pages++;
            if (sd.pages_per_search) maxPages = Math.min(maxPages, sd.pages_per_search);   // HarvestAPI trial cap per click
            let offset = 0, done = false, runCost = 0, runScanned = 0;
            const startedAt = Date.now();
            while (!done && Date.now() - startedAt < 6 * 60 * 1000) {
                await sleep(pollMs);
                const pr = await fetch('/api/education/live-poll', {
                    method: 'POST', headers: jsonHeaders,
                    body: JSON.stringify({ ...body, run_id: run.run_id, dataset_id: run.dataset_id, offset, matched_so_far: found }),
                });
                const pd = await pr.json().catch(() => ({}));
                if (!pr.ok) { stopMsg = pd.error || 'Lost contact with the LinkedIn search.'; break; }
                offset = (typeof pd.next_offset === 'number') ? pd.next_offset : offset;
                runScanned += pd.scanned_new || 0;
                scanned += pd.scanned_new || 0;
                found += pd.new_matches || 0;
                banked += pd.passout_banked || 0;
                if (typeof pd.cost_usd === 'number') runCost = pd.cost_usd;
                if (pd.harvest && pd.done) harvestRuns.push(pd.harvest);
                if (pd.provider_error) stopMsg = pd.provider_error;
                done = !!pd.done;
                progress();
            }
            cost += runCost;
            if (stopMsg) break;
            if (runScanned === 0) { exhausted = true; break; }   // LinkedIn has no more profiles for this school
        }
    } catch (err) {
        stopMsg = 'The LinkedIn search failed: ' + (err.message || 'unknown error');
    } finally {
        eduLiveRunning = false;
        if (btn) { btn.disabled = false; btn.innerHTML = btnHtml; }
        let summary = `Finished: scanned <b>${scanned}</b> LinkedIn profiles, <b>${found}</b> new verified ${found === 1 ? 'person' : 'people'} with ${what}.`;
        if (banked) summary += ` ${banked} also saved under their Passout year.`;
        summary += harvestRuns.length ? harvestSummary(harvestRuns, cost) : ` Apify cost: about $${cost.toFixed(2)}.`;
        if (stopMsg) summary += ` <span style="color:#b91c1c;">${escapeHtml(stopMsg)}</span>`;
        else if (exhausted) summary += ' LinkedIn has no more profiles for this school.';
        else summary += ' Click Search LinkedIn again to scan the next profiles.';
        setStudentsSearchStatus(summary);
        eduState.institutions = { A: null, B: null };   // candidate counts changed
        if (eduState.filter === f && eduState.selected[f] && eduState.selected[f].id === inst.id) eduSearch(1);
    }
}

// Opening the Sourcing tab shows the current tab's results (free - saved profiles only).
function eduOnTabOpen() {
    if (!document.getElementById('edu-panel')) return;
    if (!eduState.searchedOnce) eduSwitchFilter(eduState.filter);
}

// After a paid LinkedIn search finishes, its profiles are in the database: refresh what's shown.
function eduAfterLiveSearch() {
    eduState.institutions = { A: null, B: null };
    if (eduState.filter === 'P' || eduState.filter === 'ALL') eduSearch(1);
}

function eduSetStatus(html) {
    const el = document.getElementById('edu-status');
    if (el) el.innerHTML = html;
}

function eduParams(page) {
    const p = new URLSearchParams({ filter: eduState.filter, page: String(page || 1), page_size: '25' });
    const fields = [['location', 'edu-location'], ['keyword', 'edu-keyword'], ['status', 'edu-status-filter']];
    for (const [key, id] of [['mine', 'edu-f-mine'], ['followup_due', 'edu-f-followup'], ['has_contact', 'edu-f-contact']]) {
        if (document.getElementById(id)?.checked) p.set(key, '1');
    }
    document.querySelectorAll('.edu-work-f').forEach(cb => { if (cb.checked) p.set(cb.getAttribute('data-key'), '1'); });
    for (const key of ['colleges', 'years', 'states']) {
        if (eduFacetSel[key].length) p.set(key, eduFacetSel[key].join(','));
    }
    if (eduState.filter === 'P') {
        p.set('passout_year', document.getElementById('filter-student-bachelor-year')?.value || 'All');
    } else {
        const inst = eduState.selected[eduState.filter];
        p.set('institution_id', inst ? inst.id : '');
        // One passout year = exactly that year (the server treats year_from = year_to as exact).
        const py = document.getElementById('edu-passout-year')?.value || '';
        if (py) { p.set('year_from', py); p.set('year_to', py); }
    }
    for (const [key, id] of fields) {
        const v = (document.getElementById(id)?.value || '').trim();
        if (v) p.set(key, v);
    }
    return p;
}

async function eduSearch(page) {
    const isP = eduState.filter === 'P';
    const isAll = eduState.filter === 'ALL';
    const inst = eduState.selected[eduState.filter];
    if (!isP && !isAll && !inst) {
        eduSetStatus(`<span style="color:#b45309;">Pick ${eduState.filter === 'A' ? 'an Indian college' : 'a US university'} from the list first.</span>`);
        return;
    }
    const params = eduParams(page);
    eduState.searchedOnce = true;
    eduSetStatus('Searching...');
    const res = await fetch('/api/education/search?' + params.toString());
    if (res.status === 401) { window.location.href = '/login'; return; }
    const data = await res.json().catch(() => ({}));
    const wrap = document.getElementById('edu-results-wrap');
    const pager = document.getElementById('edu-pager');
    const exportBtn = document.getElementById('btn-edu-export');
    if (!res.ok) {
        eduSetStatus(`<span style="color:#b91c1c;">${escapeHtml(data.error || 'Search failed.')}</span>`);
        wrap.style.display = 'none'; pager.style.display = 'none'; exportBtn.disabled = true;
        return;
    }
    eduState.page = data.page; eduState.pages = data.pages; eduState.lastParams = params;
    eduRenderCounts(data.counts, data.total);
    eduRenderFacets(data.facets);
    const other = eduState.filter === 'B' ? "an Indian Bachelor's" : "a US Master's";
    const py = document.getElementById('filter-student-bachelor-year')?.value || 'All';
    const what = isAll ? 'saved LinkedIn profiles (all of them, no education rule)' : isP ? `an Indian Bachelor's (${py === 'All' ? '2015 - 2023' : escapeHtml(py)}) and a US Master's`
        : `${eduState.filter === 'A' ? "a Bachelor's" : "a Master's"} from <b>${escapeHtml(inst.name)}</b> and ${other}`;
    if (!data.total) {
        eduSetStatus(isAll ? 'No saved profiles match these filters.' : isP
            ? `No saved candidates with ${what} yet. Click <b>Search LinkedIn</b> to find some.`
            : `No saved profiles match <b>${escapeHtml(inst.name)}</b> + ${other} with these filters.`);
        wrap.style.display = 'none'; pager.style.display = 'none'; exportBtn.disabled = true;
        return;
    }
    eduSetStatus(isAll ? `<b>${data.total}</b> ${what}.` : `<b>${data.total}</b> candidate${data.total === 1 ? '' : 's'} with ${what}.`);
    const td = 'padding:10px 12px; border-bottom:1px solid #f1f5f9; vertical-align:top; color:#0f172a;';
    const statusOptions = (cur) => TRK_STATUSES.map(s => `<option${s === cur ? ' selected' : ''}>${escapeHtml(s)}</option>`).join('');
    const icon = (on, ch, title) => `<span title="${title}${on ? '' : ' - not saved yet'}" style="font-size:15px; opacity:${on ? 1 : 0.25};">${ch}</span>`;
    document.getElementById('edu-results').innerHTML = data.results.map(r => `
        <tr data-pid="${r.id}" class="trk-row" style="cursor:pointer;" title="Click to open the tracking card">
            <td style="${td} font-weight:600;"><a href="${escapeHtml(r.linkedin_url)}" target="_blank" rel="noopener noreferrer" title="Open LinkedIn profile" style="color:#1d4ed8; text-decoration:none;">${escapeHtml(r.name)} <span style="font-size:11px;">↗</span></a>
                <div style="margin-top:4px;"><button type="button" class="trk-card-open" data-id="${r.id}" style="padding:2px 8px; font-size:11px; font-weight:700; border:1px solid #cbd5e1; border-radius:6px; background:#ffffff; color:#1d4ed8; cursor:pointer;">📋 Card${r.comment_count ? ` · 💬 ${r.comment_count}` : ''}</button></div></td>
            <td style="${td}">${escapeHtml(r.headline)}${r.current_company ? `<div style="color:#64748b; font-size:12px;">${escapeHtml(r.current_company)}</div>` : ''}${eduWorkBadges(r)}</td>
            <td style="${td}">${escapeHtml(r.current_location || r.location)}</td>
            <td style="${td}">${escapeHtml(r.indian_college)}</td>
            <td style="${td}">${escapeHtml(r.us_masters)}</td>
            <td style="${td}"><select class="trk-status" data-id="${r.id}" data-current="${escapeHtml(r.status)}" style="${trkStatusStyle(r.status)}">${statusOptions(r.status)}</select></td>
            <td style="${td} white-space:nowrap;">${icon(r.has_email, '✉️', 'Email')} ${icon(r.has_phone, '📞', 'Phone')}</td>
            <td style="${td}">${escapeHtml(r.visa || '–')}</td>
            <td style="${td}">${escapeHtml(r.owner_name || '–')}</td>
            <td style="${td} white-space:nowrap;">${trkFollowUp(r.follow_up_date)}</td>
        </tr>`).join('');
    wrap.style.display = 'block';
    pager.style.display = data.pages > 1 ? 'flex' : 'none';
    document.getElementById('edu-page-info').textContent = `Page ${data.page} of ${data.pages}`;
    document.getElementById('btn-edu-prev').disabled = data.page <= 1;
    document.getElementById('btn-edu-next').disabled = data.page >= data.pages;
    exportBtn.disabled = false;
}

// "From LinkedIn" badges on a row: Open to work, employment type, workplace type (blank = LinkedIn shows none).
function eduWorkBadges(r) {
    const chip = (text, bg, fg) => `<span style="display:inline-block; margin:4px 4px 0 0; padding:1px 7px; border-radius:999px; font-size:11px; font-weight:700; background:${bg}; color:${fg};">${escapeHtml(text)}</span>`;
    const out = [];
    if (r.open_to_work === 1) out.push(chip('🟢 Open to work', '#dcfce7', '#166534'));
    if (r.employment_type) out.push(chip(r.employment_type, '#ede9fe', '#5b21b6'));
    if (r.workplace_type) out.push(chip(r.workplace_type, '#e0f2fe', '#075985'));
    return out.length ? `<div class="edu-work-badges">${out.join('')}</div>` : '';
}

// Always-visible numbers: everything scraped, this week, matches; counts beside each LinkedIn checkbox.
function eduRenderCounts(c, shown) {
    const box = document.getElementById('edu-counter');
    if (!c || !box) return;
    const n = (v) => Number(v || 0).toLocaleString();
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('edu-st-total', n(c.total_scraped)); set('edu-st-total-sub', 'all LinkedIn profiles saved');
    set('edu-st-week', n(c.added_this_week)); set('edu-st-week-sub', 'last 7 days');
    set('edu-st-match', n(c.matching)); set('edu-st-match-sub', 'with the filters below');
    set('edu-st-otw', n(c.open_to_work)); set('edu-st-otw-sub', `in this search (${n(c.before_work)})`);
    set('edu-st-contact', n(c.has_contact)); set('edu-st-contact-sub', 'email or phone saved');
    box.innerHTML = c.no_work_data ? `${n(c.no_work_data)} of these were scraped before Open-to-work / job type was saved, so the LinkedIn checkboxes skip them until they are scraped again.`
        + ((window.CURRENT_USER || {}).admin ? ` <button type="button" id="btn-fill-work-info" title="Reads the stored results of past LinkedIn searches again - no new scraping, costs well under 1 cent" style="margin-left:6px; padding:2px 10px; font-size:12px; font-weight:700; border:1px solid #93c5fd; border-radius:6px; background:#eff6ff; color:#1d4ed8; cursor:pointer;">↻ Fill from past searches (free)</button>` : '') : '';
    box.style.display = c.no_work_data ? 'block' : 'none';
    document.querySelectorAll('.edu-wc').forEach(s => {
        s.textContent = `(${n(c[s.getAttribute('data-key')])})`;
        s.style.cssText = 'color:#64748b; font-size:12px;';
    });
}

// ---- "Filter saved profiles" checkbox dropdowns: College / Passout year / Location ----
const eduFacetSel = { colleges: [], years: [], states: [] };
const EDU_FACET_EMPTY = { colleges: 'Any college', years: 'Any year', states: 'Any location' };
let eduFacetOpts = { colleges: [], years: [], states: [] };
let eduFacetOpen = null;

function eduFacetLabel(key, value) {
    const o = (eduFacetOpts[key] || []).find(x => String(x.value) === String(value));
    return o ? o.label : String(value);
}

function eduRenderFacetPanel(key) {
    const panel = document.querySelector(`.edu-dd-panel[data-facet="${key}"]`);
    if (!panel) return;
    const q = (panel.querySelector('.edu-dd-search')?.value || '').trim().toLowerCase();
    const opts = (eduFacetOpts[key] || []).filter(o => !q || o.label.toLowerCase().includes(q));
    const chosen = new Set(eduFacetSel[key].map(String));
    const search = key === 'colleges' ? `<input type="text" class="edu-dd-search" data-facet="${key}" placeholder="🔍 Type a college name..." value="${escapeHtml(q)}" style="width:100%; height:34px; box-sizing:border-box; border:1px solid #cbd5e1; border-radius:7px; padding:0 10px; font-size:13px; margin-bottom:8px;">` : '';
    const list = opts.length ? opts.map(o => `
        <label style="display:flex; align-items:center; gap:8px; padding:6px; font-size:13px; border-radius:6px; cursor:pointer;">
            <input type="checkbox" class="edu-dd-opt" data-facet="${key}" value="${escapeHtml(String(o.value))}"${chosen.has(String(o.value)) ? ' checked' : ''} style="width:15px; height:15px;">
            <span style="flex:1;">${escapeHtml(o.label)}</span><span style="color:#64748b; font-size:12px; font-weight:600;">${Number(o.count).toLocaleString()}</span>
        </label>`).join('') : `<div style="padding:8px 6px; font-size:12px; color:#64748b;">${q ? 'No match.' : 'No saved profiles to pick from yet.'}</div>`;
    const extra = key === 'states' && eduFacetOpts.location_not_listed
        ? `<div style="font-size:11px; color:#94a3b8; padding:4px 6px;">${Number(eduFacetOpts.location_not_listed).toLocaleString()} profile(s) have no US state or country in their location.</div>` : '';
    const keepFocus = document.activeElement && document.activeElement.classList.contains('edu-dd-search');
    panel.innerHTML = `${search}<div style="max-height:260px; overflow-y:auto;">${list}</div>${extra}
        <div style="display:flex; justify-content:space-between; border-top:1px solid #e2e8f0; margin-top:6px; padding-top:8px; font-size:12px;">
            <button type="button" class="edu-dd-clear" data-facet="${key}" style="border:none; background:none; color:#2563eb; font-weight:700; cursor:pointer; padding:0;">Clear</button>
            <span style="color:#64748b;">numbers = saved profiles</span></div>`;
    if (keepFocus) { const s = panel.querySelector('.edu-dd-search'); if (s) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); } }
}

function eduRenderFacets(f) {
    if (f) eduFacetOpts = f;
    for (const key of ['colleges', 'years', 'states']) {
        const btn = document.querySelector(`.edu-dd-btn[data-facet="${key}"]`);
        if (!btn) continue;
        const sel = eduFacetSel[key];
        const label = sel.length ? sel.map(v => eduFacetLabel(key, v)).join(', ') : EDU_FACET_EMPTY[key];
        btn.querySelector('.edu-dd-label').innerHTML = escapeHtml(label) + (sel.length ? ` <span style="background:#2563eb; color:#ffffff; border-radius:999px; font-size:11px; padding:1px 7px;">${sel.length}</span>` : '');
        btn.style.borderColor = sel.length ? '#2563eb' : '#cbd5e1';
        if (eduFacetOpen === key) eduRenderFacetPanel(key);
    }
    const chips = document.getElementById('edu-facet-chips');
    if (!chips) return;
    const all = ['colleges', 'years', 'states'].flatMap(key => eduFacetSel[key].map(v => ({ key, v })));
    chips.innerHTML = all.length ? `<span style="font-weight:700; color:#475569;">Selected:</span>` + all.map(({ key, v }) =>
        `<span style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; border-radius:999px; padding:3px 6px 3px 10px; font-weight:600;">${escapeHtml(eduFacetLabel(key, v))}<button type="button" class="edu-chip-x" data-facet="${key}" data-value="${escapeHtml(String(v))}" title="Remove" style="border:none; background:none; color:#64748b; cursor:pointer; font-weight:800; margin-left:4px;">×</button></span>`).join('')
        + `<button type="button" id="edu-facet-clear-all" style="border:none; background:none; color:#2563eb; font-weight:700; cursor:pointer;">Clear all</button>` : '';
    chips.style.display = all.length ? 'flex' : 'none';
}

function eduFacetToggle(key) {
    eduFacetOpen = eduFacetOpen === key ? null : key;
    document.querySelectorAll('.edu-dd-panel').forEach(p => { p.style.display = p.getAttribute('data-facet') === eduFacetOpen ? 'block' : 'none'; });
    if (eduFacetOpen) eduRenderFacetPanel(eduFacetOpen);
}

function eduFacetChanged() {
    eduRenderFacets(null);
    if (eduState.filter === 'P' || eduState.filter === 'ALL' || eduState.selected[eduState.filter]) eduSearch(1);
}

function initEduFacets() {
    const wrap = document.getElementById('edu-facets');
    if (!wrap) return;
    wrap.addEventListener('click', (e) => {
        const btn = e.target.closest('.edu-dd-btn');
        if (btn) { eduFacetToggle(btn.getAttribute('data-facet')); return; }
        const clr = e.target.closest('.edu-dd-clear');
        if (clr) { eduFacetSel[clr.getAttribute('data-facet')] = []; eduFacetChanged(); }
    });
    wrap.addEventListener('change', (e) => {
        if (!e.target.classList.contains('edu-dd-opt')) return;
        const key = e.target.getAttribute('data-facet');
        const v = key === 'years' ? parseInt(e.target.value) : e.target.value;
        eduFacetSel[key] = e.target.checked ? [...eduFacetSel[key], v] : eduFacetSel[key].filter(x => String(x) !== String(v));
        eduFacetChanged();
    });
    wrap.addEventListener('input', (e) => { if (e.target.classList.contains('edu-dd-search')) eduRenderFacetPanel(e.target.getAttribute('data-facet')); });
    document.getElementById('edu-facet-chips')?.addEventListener('click', (e) => {
        const x = e.target.closest('.edu-chip-x');
        if (x) {
            const key = x.getAttribute('data-facet');
            eduFacetSel[key] = eduFacetSel[key].filter(v => String(v) !== x.getAttribute('data-value'));
            eduFacetChanged();
        } else if (e.target.closest('#edu-facet-clear-all')) {
            for (const key of ['colleges', 'years', 'states']) eduFacetSel[key] = [];
            eduFacetChanged();
        }
    });
    document.addEventListener('click', (e) => {
        if (eduFacetOpen && !e.target.closest('.edu-dd')) eduFacetToggle(eduFacetOpen);
    });
}

async function eduFillWorkInfo(btn) {
    btn.disabled = true;
    btn.textContent = 'Reading past searches... (up to 2-3 min)';
    try {
        const res = await fetch('/api/admin/sourcing/fill-work-info', { method: 'POST' });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(d.error || 'Could not fill the LinkedIn details.', 'error', 7000); return; }
        let msg = `Filled ${d.filled} profile${d.filled === 1 ? '' : 's'} from ${d.searches_read} past search${d.searches_read === 1 ? '' : 'es'}.`;
        if (d.still_missing) msg += ` ${d.still_missing} still have no data (their searches have expired at Apify - they fill in when found again).`;
        if (d.partial) msg += ' Not finished in time - click again to continue.';
        if (d.error) msg += ' ' + d.error;
        showToast(msg, d.filled ? 'success' : 'info', 10000);
    } finally {
        trkRefreshTable();
    }
}

function eduExportExcel() {
    if (!eduState.lastParams) return;
    const p = new URLSearchParams(eduState.lastParams);
    p.delete('page'); p.delete('page_size');
    const a = document.createElement('a');
    a.href = '/api/education/export-xlsx?' + p.toString();
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    a.remove();
    showToast('Preparing Excel file...', 'info');
}

function initEducationFilters() {
    if (!document.getElementById('edu-panel')) return;
    document.querySelectorAll('.edu-tab').forEach(btn => btn.addEventListener('click', () => eduSwitchFilter(btn.getAttribute('data-filter'))));
    const input = document.getElementById('edu-inst-input');
    const list = document.getElementById('edu-inst-list');
    input.addEventListener('focus', async () => {
        await eduLoadInstitutions(eduState.filter);
        const sel = eduState.selected[eduState.filter];
        eduRenderInstList(sel && input.value === sel.name ? '' : input.value);
    });
    input.addEventListener('input', async () => { await eduLoadInstitutions(eduState.filter); eduRenderInstList(input.value); });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const first = list.querySelector('.edu-inst-option');
            if (first) { e.preventDefault(); eduSelectInstitution(first.getAttribute('data-id')); }
        } else if (e.key === 'Escape') {
            list.style.display = 'none';
        }
    });
    list.addEventListener('mousedown', (e) => {
        const opt = e.target.closest('.edu-inst-option');
        if (opt) { e.preventDefault(); eduSelectInstitution(opt.getAttribute('data-id')); }
    });
    input.addEventListener('blur', () => setTimeout(() => { list.style.display = 'none'; }, 150));
    document.getElementById('btn-edu-search').addEventListener('click', () => eduSearch(1));
    document.getElementById('btn-edu-export').addEventListener('click', eduExportExcel);
    document.getElementById('btn-edu-prev').addEventListener('click', () => eduSearch(Math.max(1, eduState.page - 1)));
    document.getElementById('btn-edu-next').addEventListener('click', () => eduSearch(Math.min(eduState.pages, eduState.page + 1)));
    document.getElementById('filter-student-bachelor-year')?.addEventListener('change', () => { if (eduState.filter === 'P') eduSearch(1); });
    document.getElementById('edu-passout-year')?.addEventListener('change', () => { if (eduState.filter !== 'P' && eduState.selected[eduState.filter]) eduSearch(1); });
    ['edu-location', 'edu-keyword'].forEach(id => {
        document.getElementById(id).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); eduSearch(1); } });
    });
}

// ---- Admin: school names that didn't match the college list ----
const EDU_GENERIC_WORDS = new Set(['college', 'colleges', 'engineering', 'engg', 'institute', 'institution', 'university', 'univ',
    'technology', 'technological', 'science', 'sciences', 'and', 'the', 'for', 'women', 'school', 'campus', 'group', 'institutions']);

function eduSoundsLike(text) {
    return (text || '').toLowerCase().replace(/&/g, 'and').replace(/[^a-z]/g, '')
        .replace(/ee|ea|ie|ii|y/g, 'i').replace(/oo|ou/g, 'u').replace(/ph/g, 'f').replace(/w/g, 'v')
        .replace(/([bcdfgjklmnpqrstvxz])h/g, '$1').replace(/(.)\1+/g, '$1');
}

async function addCollege() {
    const v = id => (document.getElementById(id)?.value || '').trim();
    const status = document.getElementById('ac-status');
    const res = await fetch('/api/education/institutions/add', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: v('ac-name'), country: v('ac-country'), city: v('ac-city'), aliases: v('ac-aliases') })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { status.innerHTML = `<span style="color:#b91c1c;">${escapeHtml(data.error || 'Could not add the college.')}</span>`; return; }
    status.innerHTML = `<span style="color:#059669;">Added <b>${escapeHtml(data.name)}</b>. It is now in the Filter ${v('ac-country') === 'USA' ? 'B' : 'A'} list` +
        (data.entries_linked ? `; ${data.entries_linked} saved education entr${data.entries_linked === 1 ? 'y' : 'ies'} linked to it.` : '.') + '</span>';
    ['ac-name', 'ac-city', 'ac-aliases'].forEach(id => { document.getElementById(id).value = ''; });
    eduState.institutions = { A: null, B: null };   // reload the autocomplete lists
    loadUnmappedInstitutions();
}

async function loadUnmappedInstitutions() {
    const addBtn = document.getElementById('ac-add');
    if (addBtn && !addBtn.dataset.wired) { addBtn.dataset.wired = '1'; addBtn.addEventListener('click', addCollege); }
    const body = document.getElementById('unmapped-body');
    const status = document.getElementById('unmapped-status');
    if (!body) return;
    const res = await fetch('/api/education/unmapped');
    if (!res.ok) { status.textContent = 'Could not load the list.'; return; }
    const data = await res.json().catch(() => ({}));
    const items = data.items || [];
    const options = (data.institutions || []).map(i => `<option value="${escapeHtml(i.canonical_id)}">${escapeHtml(i.country)}: ${escapeHtml(i.name)}</option>`).join('');
    status.textContent = items.length
        ? `${items.length} school name(s) need a decision.`
        : 'Nothing to review - every school name on saved profiles is recognised.';
    const td = 'padding:10px 16px; border-bottom:1px solid #f1f5f9; vertical-align:top; color:#0f172a;';
    body.innerHTML = items.map(it => `
        <tr data-id="${it.id}">
            <td style="${td} font-weight:600;">${escapeHtml(it.name)}</td>
            <td style="${td}">${String(it.profiles || 0)}</td>
            <td style="${td} color:#64748b;">${it.suggested_name ? escapeHtml(it.suggested_name) : '-'}</td>
            <td style="${td}">
                <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
                    <select class="unmapped-pick" style="height:34px; max-width:330px; border:1px solid #cbd5e1; border-radius:6px; padding:0 6px; background:#fff; color:#0f172a;">
                        <option value="">Existing college...</option>${options}
                    </select>
                    <button type="button" class="btn btn-primary unmapped-map" style="padding:6px 10px;">Link</button>
                    <span style="color:#94a3b8;">or new:</span>
                    <input type="text" class="unmapped-new-name" value="${escapeHtml(it.name)}" style="height:34px; width:220px; border:1px solid #cbd5e1; border-radius:6px; padding:0 8px; color:#0f172a;">
                    <select class="unmapped-new-country" style="height:34px; border:1px solid #cbd5e1; border-radius:6px; background:#fff; color:#0f172a;">
                        <option value="">Country...</option><option>India</option><option>USA</option><option>Other</option>
                    </select>
                    <input type="text" class="unmapped-new-city" placeholder="City" style="height:34px; width:110px; border:1px solid #cbd5e1; border-radius:6px; padding:0 8px; color:#0f172a;">
                    <button type="button" class="btn btn-secondary unmapped-create" style="padding:6px 10px;">Add new</button>
                </div>
            </td>
        </tr>`).join('');
    body.querySelectorAll('tr').forEach(row => {
        const id = row.getAttribute('data-id');
        row.querySelector('.unmapped-map').addEventListener('click', () => {
            const cid = row.querySelector('.unmapped-pick').value;
            if (!cid) { showToast('Pick a college from the list first.', 'error'); return; }
            mapUnmappedInstitution(id, { canonical_id: cid });
        });
        row.querySelector('.unmapped-create').addEventListener('click', () => {
            const name = row.querySelector('.unmapped-new-name').value.trim();
            const country = row.querySelector('.unmapped-new-country').value;
            if (!name || !country) { showToast('A new college needs a name and a country.', 'error'); return; }
            mapUnmappedInstitution(id, { new_name: name, country, city: row.querySelector('.unmapped-new-city').value.trim() });
        });
    });
}

async function mapUnmappedInstitution(id, payload) {
    const res = await fetch(`/api/education/unmapped/${id}/map`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || 'Could not save.', 'error'); return; }
    showToast(`Linked to ${data.canonical_name} - ${data.entries_updated} education entr${data.entries_updated === 1 ? 'y' : 'ies'} updated.`, 'success');
    eduState.institutions = { A: null, B: null };   // counts changed
    loadUnmappedInstitutions();
}

// ---- Sourcing tracker: per-candidate card (status, contact details, owner, activity) ----
const TRK_STATUSES = ['New', 'Contacted', 'No response', 'Interested', 'Not interested', 'Added to bench'];
const TRK_COLORS = {
    'New': ['#f1f5f9', '#334155'], 'Contacted': ['#eff6ff', '#1d4ed8'], 'No response': ['#fff7ed', '#c2410c'],
    'Interested': ['#ecfdf5', '#047857'], 'Not interested': ['#fef2f2', '#b91c1c'], 'Added to bench': ['#f5f3ff', '#6d28d9'],
};
let trkCurrentId = null;
let trkRecruiters = null;

function trkStatusStyle(status) {
    const [bg, fg] = TRK_COLORS[status] || TRK_COLORS['New'];
    return `background:${bg}; color:${fg}; border:1px solid ${fg}33; border-radius:6px; padding:4px 6px; font-size:12px; font-weight:700; cursor:pointer;`;
}

function trkToday() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function trkFollowUp(dateStr) {
    if (!dateStr) return '<span style="color:#94a3b8;">–</span>';
    const today = trkToday();
    if (dateStr < today) return `<span style="color:#b91c1c; font-weight:700;">⚠ Overdue (${escapeHtml(dateStr)})</span>`;
    if (dateStr === today) return '<span style="color:#b91c1c; font-weight:700;">🔔 Today</span>';
    return `<span style="color:#334155;">${escapeHtml(dateStr)}</span>`;
}

function trkActivityHtml(thread) {
    const me = window.CURRENT_USER || {};
    if (!thread.length) return '<div style="color:#64748b; font-size:12px;">No activity yet.</div>';
    const iconFor = { status: '🔄', field: '✏️', owner: '👤', bench: '➕' };
    return thread.map(c => c.kind === 'comment'
        ? `<div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:8px; padding:8px 10px; margin:6px 0;">
               <div style="font-size:12px; color:#64748b; margin-bottom:3px;"><b style="color:#0f172a;">💬 ${escapeHtml(c.author)}</b> · ${escapeHtml(c.at)}
               ${(me.admin || me.id === c.user_id) ? `<button type="button" class="trk-del" data-cid="${c.id}" title="Delete comment" style="float:right; border:none; background:none; color:#b91c1c; cursor:pointer; font-size:12px;">Delete</button>` : ''}</div>
               <div style="font-size:13px; color:#0f172a; white-space:pre-wrap;">${escapeHtml(c.text)}</div>
           </div>`
        : `<div style="font-size:12px; color:#475569; margin:5px 0;">${iconFor[c.kind] || '•'} ${escapeHtml(c.text)} <span style="color:#94a3b8;">- ${escapeHtml(c.author)}, ${escapeHtml(c.at)}</span></div>`).join('');
}

// '+14695550100' -> '+1 469 555 0100', '+919876543210' -> '+91 98765 43210' (display only)
function trkPhoneDisplay(p) {
    const d = String(p || '').replace(/\D/g, '');
    if (!p) return '';
    if (p.startsWith('+1') && d.length === 11) return `+1 ${d.slice(1, 4)} ${d.slice(4, 7)} ${d.slice(7)}`;
    if (p.startsWith('+91') && d.length === 12) return `+91 ${d.slice(2, 7)} ${d.slice(7)}`;
    return p;
}

function trkSelect(id, options, value, placeholder) {
    return `<select id="${id}" class="trk-input">${placeholder !== null ? `<option value="">${escapeHtml(placeholder)}</option>` : ''}${options.map(o => `<option${o === value ? ' selected' : ''}>${escapeHtml(o)}</option>`).join('')}</select>`;
}

function trkRenderCard(c, warnings) {
    const drawer = document.getElementById('trk-drawer');
    if (!drawer) return;
    const me = window.CURRENT_USER || {};
    const f = c.fields || {};
    const o = c.options || {};
    const lbl = 'display:block; font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.4px; margin:10px 0 4px;';
    const edu = (c.education || []).filter(e => e.degree_level === 'Bachelors' || e.degree_level === 'Masters')
        .map(e => `${escapeHtml(e.institution_name)}${e.degree ? ' - ' + escapeHtml(e.degree) : ''}${e.end_year ? ' (' + e.end_year + ')' : ''}`).join(' → ');
    const ownerNote = c.owner_user_id && c.owner_user_id !== me.id
        ? `<div style="margin-top:8px; padding:8px 10px; border-radius:8px; background:#fff7ed; border:1px solid #fed7aa; color:#9a3412; font-size:12px;">👤 <b>${escapeHtml(c.owner_name)}</b> is working with this candidate. You can still view, update and comment.</div>` : '';
    const ownerCtl = me.admin
        ? `<select id="trk-owner" class="trk-input" style="width:auto;"><option value="">– No owner –</option>${(trkRecruiters || []).map(u => `<option value="${u.id}"${u.id === c.owner_user_id ? ' selected' : ''}>${escapeHtml(u.name)}</option>`).join('')}</select>`
        : `<b>${escapeHtml(c.owner_name || '–')}</b>`;
    const warn = (warnings || []).length
        ? `<div style="margin-top:10px; padding:8px 10px; border-radius:8px; background:#fef2f2; border:1px solid #fecaca; color:#991b1b; font-size:12px;">⚠ ${warnings.map(escapeHtml).join('<br>⚠ ')}</div>` : '';
    drawer.innerHTML = `
      <div style="padding:18px 20px; border-bottom:1px solid #e2e8f0; position:sticky; top:0; background:#ffffff; z-index:1;">
        <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
          <div>
            <div style="font-size:1.1rem; font-weight:800; color:#0f172a;">${escapeHtml(c.name)} <a href="${escapeHtml(c.linkedin_url)}" target="_blank" rel="noopener noreferrer" style="font-size:12px; color:#1d4ed8;">LinkedIn ↗</a></div>
            <div style="font-size:13px; color:#475569; margin-top:2px;">${escapeHtml([c.headline, c.company, c.linkedin_location].filter(Boolean).join(' · '))}</div>
            ${edu ? `<div style="font-size:12px; color:#64748b; margin-top:4px;">🎓 ${edu}</div>` : ''}
          </div>
          <button type="button" id="trk-close" aria-label="Close" style="border:none; background:none; font-size:22px; color:#64748b; cursor:pointer; line-height:1;">×</button>
        </div>
      </div>
      <div style="padding:6px 20px 20px;">
        <div style="display:flex; gap:12px; align-items:flex-end; flex-wrap:wrap;">
          <div><label style="${lbl}">Status</label><select id="trk-card-status" style="${trkStatusStyle(c.status)} padding:7px 8px;">${(o.statuses || TRK_STATUSES).map(s => `<option${s === c.status ? ' selected' : ''}>${escapeHtml(s)}</option>`).join('')}</select></div>
          <div><label style="${lbl}">Owner</label><div style="font-size:13px; color:#0f172a; padding:6px 0;">${ownerCtl}</div></div>
        </div>
        ${ownerNote}
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:0 12px; margin-top:6px;">
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-email">Email</label><input id="trk-email" class="trk-input" type="email" value="${escapeHtml(f.contact_email)}" placeholder="name@example.com"></div>
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-phone">Phone (with country code)</label><input id="trk-phone" class="trk-input" type="tel" value="${escapeHtml(trkPhoneDisplay(f.contact_phone))}" placeholder="+1 469 555 0100"></div>
          <div><label style="${lbl}" for="trk-visa">Visa status</label>${trkSelect('trk-visa', o.visa || [], f.visa_status, '– Not known –')}</div>
          <div><label style="${lbl}" for="trk-location">Current location</label><input id="trk-location" class="trk-input" value="${escapeHtml(f.current_location)}" placeholder="e.g. Dallas, TX"></div>
          <div><label style="${lbl}" for="trk-relocate">Open to relocate</label>${trkSelect('trk-relocate', o.relocate || [], f.open_to_relocate, '– Not known –')}</div>
          <div><label style="${lbl}" for="trk-rate">Expected rate</label><input id="trk-rate" class="trk-input" value="${escapeHtml(f.expected_rate)}" placeholder="e.g. $65/hr C2C"></div>
          <div><label style="${lbl}" for="trk-availability">Availability</label>${trkSelect('trk-availability', o.availability || [], f.availability, '– Not known –')}</div>
          <div><label style="${lbl}" for="trk-followup">Next follow-up</label><input id="trk-followup" class="trk-input" type="date" value="${escapeHtml(f.follow_up_date)}"></div>
          <div><label style="${lbl}" for="trk-visa-expiry">Visa expiry</label><input id="trk-visa-expiry" class="trk-input" type="date" value="${escapeHtml(f.visa_expiry)}"></div>
          <div><label style="${lbl}" for="trk-current-rate">Current rate / salary</label><input id="trk-current-rate" class="trk-input" value="${escapeHtml(f.current_rate)}" placeholder="e.g. $55/hr W2"></div>
          <div><label style="${lbl}" for="trk-experience">Experience (years)</label><input id="trk-experience" class="trk-input" type="number" min="0" max="60" step="0.5" value="${escapeHtml(f.experience_years)}" placeholder="e.g. 8"></div>
          <div><label style="${lbl}" for="trk-work-mode">Work mode</label>${trkSelect('trk-work-mode', o.work_mode || [], f.work_mode, '– Not known –')}</div>
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-skills">Primary skills</label><input id="trk-skills" class="trk-input" maxlength="500" value="${escapeHtml(f.primary_skills)}" placeholder="e.g. Java, Spring Boot, AWS, Kafka"></div>
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-pref-locations">Preferred locations</label><input id="trk-pref-locations" class="trk-input" maxlength="500" value="${escapeHtml(f.preferred_locations)}" placeholder="e.g. Dallas, Austin, Remote"></div>
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-certs">Certifications</label><input id="trk-certs" class="trk-input" maxlength="500" value="${escapeHtml(f.certifications)}" placeholder="e.g. AWS Solutions Architect, CKA"></div>
          <div><label style="${lbl}" for="trk-github">GitHub ${f.github_url ? `<a href="${escapeHtml(f.github_url)}" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8; text-transform:none;">open ↗</a>` : ''}</label><input id="trk-github" class="trk-input" value="${escapeHtml(f.github_url)}" placeholder="github.com/username"></div>
          <div><label style="${lbl}" for="trk-portfolio">Portfolio / website ${f.portfolio_url ? `<a href="${escapeHtml(f.portfolio_url)}" target="_blank" rel="noopener noreferrer" style="color:#1d4ed8; text-transform:none;">open ↗</a>` : ''}</label><input id="trk-portfolio" class="trk-input" value="${escapeHtml(f.portfolio_url)}" placeholder="e.g. janedoe.dev"></div>
          <div><label style="${lbl}" for="trk-referred">Referred by / source</label><input id="trk-referred" class="trk-input" maxlength="120" value="${escapeHtml(f.referred_by)}" placeholder="e.g. Ravi K / Dice"></div>
          <div><label style="${lbl}" for="trk-marital" title="Kept on this card only - never copied to the bench or sent to clients">Marital status 🔒</label>${trkSelect('trk-marital', o.marital || [], f.marital_status, '– Not known –')}</div>
          <div style="grid-column:1 / -1;"><label style="${lbl}" for="trk-resume">Resume</label>
            ${c.resume ? `<div id="trk-resume-current" style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-size:13px; margin-bottom:6px;">📄 <a href="/api/sourcing/profiles/${c.id}/resume" style="color:#1d4ed8; font-weight:600;">${escapeHtml(c.resume.filename)}</a>
                <span style="color:#94a3b8; font-size:12px;">${escapeHtml([c.resume.uploaded_by, c.resume.at].filter(Boolean).join(', '))}</span>
                <button type="button" id="trk-resume-del" style="border:none; background:none; color:#b91c1c; cursor:pointer; font-size:12px;">Remove</button></div>` : ''}
            <input id="trk-resume" type="file" accept=".docx,.pdf" style="font-size:12px;">
            <div style="font-size:11px; color:#94a3b8; margin-top:3px;">Word or PDF, up to 5 MB${c.resume ? ' - choosing a file replaces the current one' : ''}. Copied to the consultant on Add to Bench.</div>
          </div>
        </div>
        ${warn}
        <div style="display:flex; gap:10px; margin-top:14px; flex-wrap:wrap;">
          <button type="button" id="trk-save" style="height:38px; padding:0 18px; border:none; border-radius:8px; background:#2563eb; color:#ffffff; font-weight:700; cursor:pointer;">Save</button>
          ${c.bench_candidate_id
              ? '<span style="height:38px; display:inline-flex; align-items:center; padding:0 14px; border-radius:8px; background:#f5f3ff; color:#6d28d9; font-weight:700;">✓ On the bench</span>'
              : '<button type="button" id="trk-bench" title="Create a bench consultant from this card (needs an email)" style="height:38px; padding:0 14px; border:1px solid #c4b5fd; border-radius:8px; background:#f5f3ff; color:#6d28d9; font-weight:700; cursor:pointer;">➕ Add to Bench</button>'}
        </div>
        <div style="margin-top:20px; font-size:12px; font-weight:800; color:#334155; text-transform:uppercase; letter-spacing:0.4px;">Activity</div>
        <div style="display:flex; gap:8px; margin-top:8px; align-items:flex-start;">
          <textarea id="trk-comment" rows="2" maxlength="2000" placeholder="e.g. Called - interested, will send resume tonight" style="flex:1; border:1px solid #cbd5e1; border-radius:8px; padding:8px 10px; font-size:13px; font-family:inherit; resize:vertical;"></textarea>
          <button type="button" id="trk-post" style="height:38px; padding:0 12px; border:none; border-radius:8px; background:#0f172a; color:#ffffff; font-weight:700; cursor:pointer;">Post</button>
        </div>
        <div id="trk-activity" style="margin-top:8px;">${trkActivityHtml(c.thread || [])}</div>
      </div>`;
    drawer.querySelectorAll('.trk-input').forEach(el => {
        el.style.cssText = 'width:100%; height:38px; box-sizing:border-box; border:1px solid #cbd5e1; border-radius:8px; padding:0 10px; font-size:13px; color:#0f172a; background:#ffffff;';
    });
}

async function trkLoadRecruiters() {
    if (trkRecruiters !== null || !(window.CURRENT_USER || {}).admin) return;
    try {
        const res = await fetch('/api/admin/recruiters');
        const data = res.ok ? await res.json() : [];
        trkRecruiters = (Array.isArray(data) ? data : (data.recruiters || [])).map(u => ({ id: u.id, name: u.name }));
    } catch (e) { trkRecruiters = []; }
}

async function trkOpen(profileId) {
    trkCurrentId = parseInt(profileId);
    await trkLoadRecruiters();
    const res = await fetch(`/api/sourcing/profiles/${trkCurrentId}/card`);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || 'Could not open the card.', 'error'); return; }
    trkRenderCard(data, []);
    document.getElementById('trk-backdrop').style.display = 'block';
    document.getElementById('trk-drawer').style.transform = 'translateX(0)';
}

function trkClose() {
    trkCurrentId = null;
    document.getElementById('trk-backdrop').style.display = 'none';
    document.getElementById('trk-drawer').style.transform = 'translateX(105%)';
}

async function trkRequest(url, body, method = 'POST') {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || 'Something went wrong.', 'error', 6000); return null; }
    return data;
}

function trkRefreshTable() {
    if (eduState.filter === 'P' || eduState.filter === 'ALL' || eduState.selected[eduState.filter]) eduSearch(eduState.page || 1);
}

async function trkSaveCard() {
    const v = (id) => (document.getElementById(id)?.value || '').trim();
    const data = await trkRequest(`/api/sourcing/profiles/${trkCurrentId}/card`, {
        contact_email: v('trk-email'), contact_phone: v('trk-phone'), visa_status: v('trk-visa'),
        current_location: v('trk-location'), open_to_relocate: v('trk-relocate'), expected_rate: v('trk-rate'),
        availability: v('trk-availability'), follow_up_date: v('trk-followup'),
        visa_expiry: v('trk-visa-expiry'), current_rate: v('trk-current-rate'), experience_years: v('trk-experience'),
        work_mode: v('trk-work-mode'), primary_skills: v('trk-skills'), preferred_locations: v('trk-pref-locations'),
        certifications: v('trk-certs'), github_url: v('trk-github'), portfolio_url: v('trk-portfolio'),
        referred_by: v('trk-referred'), marital_status: v('trk-marital'),
    });
    if (!data) return;
    trkRenderCard(data, data.warnings || []);
    showToast((data.warnings || []).length ? 'Saved - but see the warning on the card.' : 'Saved.', (data.warnings || []).length ? 'warning' : 'success');
    trkRefreshTable();
}

async function trkSetStatus(profileId, status, select) {
    const data = await trkRequest(`/api/sourcing/profiles/${profileId}/status`, { status });
    if (!data) { if (select) select.value = select.getAttribute('data-current') || 'New'; return; }
    if (select) { select.setAttribute('data-current', status); select.setAttribute('style', trkStatusStyle(status)); }
    if (trkCurrentId === parseInt(profileId)) trkRenderCard(data, []);
    showToast(`Status: ${status}`, 'success');
    trkRefreshTable();
}

async function trkPostComment() {
    const box = document.getElementById('trk-comment');
    const text = (box?.value || '').trim();
    if (!text) { showToast('Write a comment first.', 'warning'); return; }
    const data = await trkRequest(`/api/sourcing/profiles/${trkCurrentId}/comments`, { comment: text });
    if (!data) return;
    box.value = '';
    document.getElementById('trk-activity').innerHTML = trkActivityHtml(data.thread || []);
    showToast('Comment posted.', 'success');
    trkRefreshTable();
}

async function trkDeleteComment(commentId) {
    if (!confirm('Delete this comment?')) return;
    const data = await trkRequest(`/api/sourcing/comments/${commentId}`, null, 'DELETE');
    if (!data) return;
    document.getElementById('trk-activity').innerHTML = trkActivityHtml(data.thread || []);
    trkRefreshTable();
}

async function trkAddToBench() {
    if (!confirm('Add this candidate to your bench as a consultant?')) return;
    const data = await trkRequest(`/api/sourcing/profiles/${trkCurrentId}/add-to-bench`, {});
    if (!data) return;
    trkRenderCard(data, []);
    showToast(`${data.name} added to your bench.`, 'success');
    if (typeof fetchConsultants === 'function') fetchConsultants();
    trkRefreshTable();
}

async function trkReassign(userId) {
    const data = await trkRequest(`/api/sourcing/profiles/${trkCurrentId}/owner`, { user_id: userId || null });
    if (!data) return;
    trkRenderCard(data, []);
    showToast('Owner updated.', 'success');
    trkRefreshTable();
}

async function trkUploadResume(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch(`/api/sourcing/profiles/${trkCurrentId}/resume`, { method: 'POST', body: fd });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(data.error || 'Could not upload the resume.', 'error', 6000); input.value = ''; return; }
    trkRenderCard(data, []);
    showToast('Resume attached.', 'success');
}

async function trkDeleteResume() {
    if (!confirm('Remove the attached resume?')) return;
    const data = await trkRequest(`/api/sourcing/profiles/${trkCurrentId}/resume`, null, 'DELETE');
    if (!data) return;
    trkRenderCard(data, []);
    showToast('Resume removed.', 'success');
}

function initSourcingTracker() {
    const tbody = document.getElementById('edu-results');
    const drawer = document.getElementById('trk-drawer');
    if (!tbody || !drawer) return;
    tbody.addEventListener('change', (e) => {
        if (e.target.classList.contains('trk-status')) trkSetStatus(e.target.getAttribute('data-id'), e.target.value, e.target);
    });
    tbody.addEventListener('click', (e) => {
        if (e.target.closest('a, select, input')) return;          // LinkedIn link / status dropdown keep their own job
        const row = e.target.closest('tr.trk-row');
        if (row) trkOpen(row.getAttribute('data-pid'));
    });
    drawer.addEventListener('click', (e) => {
        if (e.target.closest('#trk-close')) trkClose();
        else if (e.target.closest('#trk-save')) trkSaveCard();
        else if (e.target.closest('#trk-post')) trkPostComment();
        else if (e.target.closest('#trk-bench')) trkAddToBench();
        else if (e.target.closest('.trk-del')) trkDeleteComment(e.target.closest('.trk-del').getAttribute('data-cid'));
        else if (e.target.closest('#trk-resume-del')) trkDeleteResume();
    });
    drawer.addEventListener('change', (e) => {
        if (e.target.id === 'trk-resume') trkUploadResume(e.target);
        else if (e.target.id === 'trk-card-status') trkSetStatus(trkCurrentId, e.target.value, null);
        else if (e.target.id === 'trk-owner') trkReassign(e.target.value);
    });
    document.getElementById('trk-backdrop')?.addEventListener('click', trkClose);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && trkCurrentId) trkClose(); });
    document.getElementById('edu-status-filter')?.addEventListener('change', trkRefreshTable);
    initEduFacets();
    document.getElementById('edu-counter')?.addEventListener('click', (e) => {
        const b = e.target.closest('#btn-fill-work-info');
        if (b) eduFillWorkInfo(b);
    });
    ['edu-f-mine', 'edu-f-followup', 'edu-f-contact', 'edu-f-otw', 'edu-f-contract', 'edu-f-fulltime', 'edu-f-remote', 'edu-f-hybrid', 'edu-f-onsite'].forEach(id =>
        document.getElementById(id)?.addEventListener('change', trkRefreshTable));
}

// ---- Jobs: experience filter (slider) + Experience column ----
const JOBS_EXP_ANY = 16;   // the slider's right end means "Any"

function jobsMyExperience() {
    const v = parseInt(document.getElementById('filter-experience')?.value ?? JOBS_EXP_ANY);
    return (isNaN(v) || v >= JOBS_EXP_ANY) ? null : v;
}

function syncJobsExperienceLabel() {
    const n = jobsMyExperience();
    const out = document.getElementById('filter-experience-value');
    if (out) out.textContent = n === null ? 'Any' : `${n} yr${n === 1 ? '' : 's'}`;
    const wrap = document.getElementById('filter-exp-unstated-wrap');
    if (wrap) wrap.style.display = n === null ? 'none' : 'flex';
}

function setJobsExperience(years) {
    const slider = document.getElementById('filter-experience');
    if (!slider) return;
    slider.value = (years === null || years === undefined || years === '') ? JOBS_EXP_ANY : Math.min(JOBS_EXP_ANY - 1, Math.max(0, parseInt(years)));
    syncJobsExperienceLabel();
}

function jobExperienceBadge(exp) {
    if (!exp) return '';
    const styles = {
        posting: ['#ecfdf5', '#047857', 'From the posting'],
        text: ['#eff6ff', '#1d4ed8', 'From the job description'],
        title: ['#fff7ed', '#c2410c', 'Guessed from the job title - no years stated'],
        '': ['#f1f5f9', '#64748b', 'The posting does not state experience'],
    };
    const [bg, fg, tip] = styles[exp.source] || styles[''];
    return `<span title="${tip}" style="display:inline-block; padding:2px 8px; border-radius:9999px; font-size:0.75rem; font-weight:700; background:${bg}; color:${fg}; white-space:nowrap;">${escapeHtml(exp.label || 'Not stated')}</span>`;
}

(function initJobsExperienceFilter() {
    const start = () => {
        const slider = document.getElementById('filter-experience');
        if (!slider) return;
        syncJobsExperienceLabel();
        slider.addEventListener('input', syncJobsExperienceLabel);
        slider.addEventListener('change', () => searchJobs(false));
        document.getElementById('filter-exp-unstated')?.addEventListener('change', () => searchJobs(false));
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();


// =========================================================================
// Vendors: each recruiter's private vendor contacts (admins see everyone's)
// =========================================================================
const vendorsState = { contacts: [], isAdmin: false, preview: null, filename: '', timer: null, wired: false };

function vendorsWire() {
    if (vendorsState.wired) return;
    vendorsState.wired = true;
    const $ = id => document.getElementById(id);
    $('vendors-search').addEventListener('input', () => {
        clearTimeout(vendorsState.timer);
        vendorsState.timer = setTimeout(loadVendors, 250);
    });
    $('vendors-owner').addEventListener('change', loadVendors);
    $('vendors-file').addEventListener('change', e => {
        const f = e.target.files[0];
        e.target.value = '';
        if (f) vendorsUploadPreview(f);
    });
    $('vendors-add-btn').addEventListener('click', () => vendorsOpenForm(null));
    $('vf-cancel').addEventListener('click', () => { $('vendors-form').style.display = 'none'; });
    $('vf-save').addEventListener('click', vendorsSaveForm);
    $('vp-cancel').addEventListener('click', () => { vendorsState.preview = null; $('vendors-preview').style.display = 'none'; });
    $('vp-import').addEventListener('click', vendorsImport);
}

async function loadVendors() {
    vendorsWire();
    const q = document.getElementById('vendors-search').value.trim();
    const owner = document.getElementById('vendors-owner').value;
    const params = new URLSearchParams();
    if (q) params.set('q', q);
    if (owner) params.set('owner', owner);
    try {
        const res = await fetch('/api/vendors?' + params.toString());
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Could not load vendors');
        vendorsState.contacts = data.contacts;
        vendorsState.isAdmin = data.is_admin;
        const sel = document.getElementById('vendors-owner');
        if (data.is_admin) {
            sel.style.display = '';
            document.getElementById('vendors-subtitle').textContent =
                "As an admin you see every recruiter's vendor list. Drafts only ever BCC the drafting recruiter's own contacts.";
            if (!owner && !q) {
                sel.innerHTML = '<option value="">All recruiters</option>' +
                    data.owners.map(o => `<option value="${o.id}">${escapeHtml(o.name || ('User ' + o.id))}</option>`).join('');
            }
        }
        const n = data.contacts.length;
        document.getElementById('vendors-count').textContent =
            `${n} contact${n === 1 ? '' : 's'} at ${data.companies} compan${data.companies === 1 ? 'y' : 'ies'}`;
        renderVendors();
    } catch (err) {
        document.getElementById('vendors-count').textContent = err.message;
    }
}

function renderVendors() {
    const body = document.getElementById('vendors-body');
    const showOwner = vendorsState.isAdmin;
    document.querySelectorAll('.vendors-owner-col').forEach(el => { el.style.display = showOwner ? '' : 'none'; });
    if (!vendorsState.contacts.length) {
        body.innerHTML = `<tr><td colspan="9" style="text-align:center; color:#64748b; padding:24px;">No vendor contacts yet. Download the template, fill it in Excel and upload it - or add one contact at a time.</td></tr>`;
        return;
    }
    const statuses = ['active', 'unsubscribed', 'bounced'];
    body.innerHTML = vendorsState.contacts.map(c => `
        <tr data-id="${c.id}">
            <td style="color:#0f172a; font-weight:600;">${escapeHtml(c.company_name)}</td>
            <td style="color:#0f172a;">${escapeHtml(c.contact_name || '')}${c.title ? `<div style="font-size:0.75rem; color:#64748b;">${escapeHtml(c.title)}</div>` : ''}</td>
            <td style="color:#0f172a;">${escapeHtml(c.email)}</td>
            <td style="color:#0f172a; white-space:nowrap;">${escapeHtml(c.phone || '')}</td>
            <td class="vendor-notes" title="${escapeHtml(c.notes || '')}" style="color:#475569; font-size:0.8rem; max-width:170px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(c.notes || '')}</td>
            <td><select class="form-control vendor-status" style="padding:2px 6px; font-size:0.8rem; min-width:100px;" onchange="vendorsSetStatus(${c.id}, this.value)">
                ${statuses.map(s => `<option value="${s}" ${c.status === s ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}
            </select></td>
            <td style="font-size:0.8rem; color:#0f172a;">${c.last_emailed_at ? escapeHtml(c.last_emailed_at.slice(0, 10)) : '-'}</td>
            <td class="vendors-owner-col" style="color:#0f172a;${showOwner ? '' : ' display:none;'}">${escapeHtml(c.owner_name || '')}</td>
            <td style="white-space:nowrap;">
                <button class="btn btn-secondary btn-xs" onclick="vendorsOpenForm(${c.id})">Edit</button>
                <button class="btn btn-danger btn-xs" onclick="vendorsDelete(${c.id})">Delete</button>
            </td>
        </tr>`).join('');
}

function vendorsOpenForm(id) {
    const c = id ? vendorsState.contacts.find(x => x.id === id) : null;
    const $ = k => document.getElementById(k);
    $('vf-id').value = c ? c.id : '';
    $('vendors-form-title').textContent = c ? 'Edit contact' : 'Add contact';
    $('vf-company').value = c ? c.company_name : '';
    $('vf-name').value = c ? (c.contact_name || '') : '';
    $('vf-email').value = c ? c.email : '';
    $('vf-email').disabled = !!c;
    $('vf-phone').value = c ? (c.phone || '') : '';
    $('vf-title').value = c ? (c.title || '') : '';
    $('vf-notes').value = c ? (c.notes || '') : '';
    $('vendors-form').style.display = '';
    $('vf-company').focus();
}

async function vendorsSaveForm() {
    const v = k => document.getElementById(k).value.trim();
    const id = v('vf-id');
    const body = id
        ? { company_name: v('vf-company'), contact_name: v('vf-name'), phone: v('vf-phone'), title: v('vf-title'), notes: v('vf-notes') }
        : { company: v('vf-company'), name: v('vf-name'), email: v('vf-email'), phone: v('vf-phone'), title: v('vf-title'), notes: v('vf-notes') };
    const res = await fetch(id ? `/api/vendors/${id}` : '/api/vendors', {
        method: id ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || 'Could not save', 'error'); return; }
    document.getElementById('vendors-form').style.display = 'none';
    showToast(id ? 'Contact updated' : 'Contact added', 'success');
    loadVendors();
}

async function vendorsSetStatus(id, status) {
    const res = await fetch(`/api/vendors/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || 'Could not update', 'error'); loadVendors(); return; }
    const c = vendorsState.contacts.find(x => x.id === id);
    if (c) c.status = status;
    showToast(status === 'active' ? 'Contact will be used in drafts again' : `Marked ${status} - never put in a draft`, 'success');
}

async function vendorsDelete(id) {
    const c = vendorsState.contacts.find(x => x.id === id);
    if (!confirm(`Delete ${c ? c.email : 'this contact'}?`)) return;
    const res = await fetch(`/api/vendors/${id}`, { method: 'DELETE' });
    if (!res.ok) { showToast('Could not delete', 'error'); return; }
    showToast('Contact deleted', 'success');
    loadVendors();
}

async function vendorsUploadPreview(file) {
    const fd = new FormData();
    fd.append('file', file);
    const res = await fetch('/api/vendors/upload-preview', { method: 'POST', body: fd });
    const data = await res.json();
    if (!res.ok) { showToast(data.error || 'Could not read that file', 'error'); return; }
    vendorsState.preview = data.rows;
    vendorsState.filename = data.filename;
    const k = data.counts;
    document.getElementById('vp-filename').textContent = data.filename;
    document.getElementById('vp-counts').innerHTML =
        `<b style="color:#059669;">${k.new} new</b> &middot; <span style="color:#64748b;">${k.duplicate} already in your list / repeated</span> &middot; <span style="color:#b91c1c;">${k.problem} with a problem (skipped)</span>`;
    const label = { new: ['New', '#059669'], duplicate: ['Duplicate', '#64748b'], problem: ['Problem', '#b91c1c'] };
    document.querySelector('#vp-table tbody').innerHTML = data.rows.slice(0, 500).map(r => `
        <tr><td>${r.line}</td><td style="color:#0f172a;">${escapeHtml(r.company || '')}</td><td style="color:#0f172a;">${escapeHtml(r.name || '')}</td>
        <td style="color:#0f172a;">${escapeHtml(r.email || '')}</td><td style="color:#0f172a;">${escapeHtml(r.phone || '')}</td><td class="vendor-notes" title="${escapeHtml(r.notes || '')}" style="color:#475569; font-size:0.8rem; max-width:170px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(r.notes || '')}</td>
        <td style="color:${label[r.status][1]}; font-weight:600;">${label[r.status][0]}${r.reason ? ` <span style="font-weight:400;">- ${escapeHtml(r.reason)}</span>` : ''}</td></tr>`).join('');
    const btn = document.getElementById('vp-import');
    btn.disabled = k.new === 0;
    btn.textContent = k.new ? `Import ${k.new} new contact${k.new === 1 ? '' : 's'}` : 'Nothing new to import';
    document.getElementById('vendors-preview').style.display = '';
}

async function vendorsImport() {
    if (!vendorsState.preview) return;
    const rows = vendorsState.preview.filter(r => r.status === 'new');
    const btn = document.getElementById('vp-import');
    btn.disabled = true;
    const res = await fetch('/api/vendors/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows, filename: vendorsState.filename })
    });
    const data = await res.json();
    btn.disabled = false;
    if (!res.ok) { showToast(data.error || 'Import failed', 'error'); return; }
    vendorsState.preview = null;
    document.getElementById('vendors-preview').style.display = 'none';
    showToast(`Imported ${data.added} vendor contact${data.added === 1 ? '' : 's'}`, 'success');
    loadVendors();
}


// =========================================================================
// Paste Requirement & Draft: BCC the recruiter's own vendor contacts at that company
// =========================================================================
const pdVendor = { timer: null, seq: 0, contacts: [], unknown: [], company: '', max: 10, unticked: new Set() };

function pdVendorEmails() {
    const text = document.getElementById('pd-raw-text')?.value || '';
    const to = (document.getElementById('pd-email')?.value || '').trim();
    const found = text.match(/[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) || [];
    return [...new Set([to, ...found].map(e => e.replace(/^[.']+|[.']+$/g, '').toLowerCase()).filter(e => e.includes('@')))];
}

function pdVendorReset() {
    clearTimeout(pdVendor.timer);
    pdVendor.seq++;
    pdVendor.contacts = [];
    pdVendor.unknown = [];
    pdVendor.unticked = new Set();
    const box = document.getElementById('pd-vendor-box');
    if (box) { box.innerHTML = ''; box.style.display = 'none'; }
}

function pdVendorSchedule() {
    clearTimeout(pdVendor.timer);
    pdVendor.timer = setTimeout(pdVendorCheck, 400);
}

async function pdVendorCheck() {
    const emails = pdVendorEmails();
    const company = (document.getElementById('pd-company')?.value || '').trim();
    const seq = ++pdVendor.seq;
    if (!emails.length && !company) { pdVendorReset(); return; }
    try {
        const res = await fetch('/api/vendors/match', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ emails, company })
        });
        const data = await res.json();
        if (seq !== pdVendor.seq || !res.ok) return;   // a newer check is running
        const to = (document.getElementById('pd-email')?.value || '').trim().toLowerCase();
        pdVendor.contacts = (data.contacts || []).filter(c => c.email.toLowerCase() !== to);
        pdVendor.unknown = data.unknown_emails || [];
        pdVendor.company = data.company || '';
        pdVendor.max = data.max_bcc || 10;
        const companyInput = document.getElementById('pd-company');
        if (companyInput && !companyInput.value.trim() && pdVendor.company && pdVendor.contacts.length) companyInput.value = pdVendor.company;
        pdVendorRender();
    } catch (err) { /* the draft still works without the vendor box */ }
}

function pdVendorRender() {
    const box = document.getElementById('pd-vendor-box');
    if (!box) return;
    const parts = [];
    const n = pdVendor.contacts.length;
    // ticked by default, except ones the recruiter unticked (kept across re-checks), up to the cap
    const tickedIds = new Set(pdVendor.contacts.filter(c => !pdVendor.unticked.has(c.id)).slice(0, pdVendor.max).map(c => c.id));
    if (n) {
        const capped = n > pdVendor.max;
        parts.push(`<div style="font-weight:700; color:#065f46; margin-bottom:6px;">🤝 Known vendor: ${escapeHtml(pdVendor.company)} &middot; <span id="pd-vendor-count"></span></div>
            <div style="display:flex; flex-direction:column; gap:4px; max-height:170px; overflow:auto;">
            ${pdVendor.contacts.map(c => `<label style="display:flex; gap:8px; align-items:center; font-weight:400; text-transform:none; color:#0f172a; margin:0;">
                <input type="checkbox" class="pd-bcc" value="${c.id}" ${tickedIds.has(c.id) ? 'checked' : ''}>
                <span>${escapeHtml(c.contact_name || '')}${c.contact_name ? ' &middot; ' : ''}${escapeHtml(c.email)}</span></label>`).join('')}
            </div>
            ${capped ? `<div style="font-size:0.78rem; color:#64748b; margin-top:4px;">At most ${pdVendor.max} contacts per draft - tick the ones you want.</div>` : ''}`);
    }
    const company = (document.getElementById('pd-company')?.value || '').trim();
    pdVendor.unknown.forEach(e => {
        parts.push(`<div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-top:${parts.length ? 8 : 0}px; color:#0f172a;">
            <span style="font-size:0.85rem;">${escapeHtml(e)} isn't in your vendors.</span>
            <button type="button" class="btn btn-secondary btn-xs pd-save-vendor" data-email="${escapeHtml(e)}">➕ Save to my vendors</button></div>`);
    });
    if (!parts.length) { box.innerHTML = ''; box.style.display = 'none'; return; }
    box.innerHTML = parts.join('');
    box.style.display = '';
    box.querySelectorAll('.pd-bcc').forEach(cb => cb.addEventListener('change', () => {
        const id = parseInt(cb.value);
        if (cb.checked) pdVendor.unticked.delete(id); else pdVendor.unticked.add(id);
        pdVendorCount();
    }));
    box.querySelectorAll('.pd-save-vendor').forEach(b => b.addEventListener('click', () => pdVendorSave(b.dataset.email, company)));
    pdVendorCount();
}

function pdVendorCount() {
    const boxes = [...document.querySelectorAll('#pd-vendor-box .pd-bcc')];
    const ticked = boxes.filter(b => b.checked).length;
    boxes.forEach(b => { b.disabled = !b.checked && ticked >= pdVendor.max; });
    const el = document.getElementById('pd-vendor-count');
    if (el) el.textContent = ticked ? `${ticked} contact${ticked === 1 ? '' : 's'} will be BCC'd` : 'no contacts will be BCC\'d';
}

function pdVendorSelectedIds() {
    return [...document.querySelectorAll('#pd-vendor-box .pd-bcc:checked')].map(b => parseInt(b.value)).slice(0, pdVendor.max);
}

async function pdVendorSave(email, company) {
    const res = await fetch('/api/vendors', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, company: company || pdVendor.company, notes: 'Saved from a pasted requirement' })
    });
    const data = await res.json();
    if (!res.ok) {
        showToast((data.error || 'Could not save') + (/company/i.test(data.error || '') ? ' - type the company first.' : ''), 'warning', 6000);
        return;
    }
    showToast(`${email} saved to your vendors`, 'success');
    pdVendorCheck();
}


// =========================================================================
// Browse Jobs: automatic drafts for jobs from the recruiter's known vendors
// (max 10 per consultant per day, never the same job twice - enforced by the server)
// =========================================================================
async function autoVendorDrafts(candidateId) {
    const jobIds = (state.jobs || []).filter(j => j.vendor && j.vendor.count).map(j => j.id);
    if (!candidateId || !jobIds.length) return;
    try {
        const res = await fetch('/api/outreach/auto-vendor-drafts', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ candidate_id: candidateId, job_ids: jobIds })
        });
        const data = await res.json();
        if (!res.ok) return;
        const name = data.candidate_name || 'this consultant';
        const n = data.created.length;
        if (n) {
            const bcc = data.created.reduce((a, c) => a + c.bcc, 0);
            showToast(`🤝 ${n} known-vendor draft${n === 1 ? '' : 's'} created in ${name}'s Gmail (${bcc} vendor contact${bcc === 1 ? '' : 's'} in BCC). ` +
                      `${data.remaining_today} auto-draft${data.remaining_today === 1 ? '' : 's'} left today. Review them in Gmail Drafts - nothing is sent.`, 'success', 10000);
            data.created.forEach(c => {
                const btn = document.querySelector(`#jobs-table-body tr[data-job-id="${c.job_id}"] .btn-draft-job`);
                if (btn) btn.insertAdjacentHTML('beforebegin', '<span class="auto-drafted-tag" style="font-size:0.72rem; color:#065f46; font-weight:700; margin-right:6px;">✓ Auto-drafted</span>');
            });
        }
        if (data.error) {
            showToast(`Known-vendor drafts for ${name}: ${data.error}`, 'warning', 8000);
        } else if (!n && data.skipped.daily_limit) {
            showToast(`Daily limit reached: ${name} already has 10 automatic known-vendor drafts today.`, 'info', 6000);
        }
    } catch (err) { /* browsing still works without the automatic drafts */ }
}

// HarvestAPI direct trial: what the provider actually returned, and the ESTIMATED cost (HarvestAPI's
// docs publish no per-call prices - the exact figure is in the HarvestAPI dashboard).
function harvestSummary(runs, cost) {
    const found = runs.reduce((a, h) => a + (h.search_found || 0), 0);
    const fetched = runs.reduce((a, h) => a + (h.profiles_ok || 0), 0);
    const totals = runs.map(h => h.search_total).filter(t => typeof t === 'number');
    const total = totals.length ? Math.max(...totals) : null;
    return ` HarvestAPI: the search returned ${found} profile${found === 1 ? '' : 's'}` +
        (total !== null ? ` (LinkedIn reports ${total.toLocaleString()} in total)` : '') +
        `, ${fetched} full profile${fetched === 1 ? '' : 's'} fetched. Estimated cost: about $${Number(cost || 0).toFixed(2)} - check the exact usage in your HarvestAPI dashboard.`;
}

// "Took 38.2s - AI analysis 14.1s (reused) - AI rewrite 22.9s - Word file 0.3s" under the result.
function optimizerTimingsHtml(t) {
    if (!t || typeof t.total_s !== 'number') return '';
    const parts = [];
    if (typeof t.analysis_s === 'number') parts.push(`AI analysis ${t.analysis_cached ? 'reused (0s)' : t.analysis_s.toFixed(1) + 's'}`);
    if (typeof t.rewrite_s === 'number') parts.push(`AI rewrite ${t.rewrite_s.toFixed(1)}s${t.rewrite_mode === 'full' ? ' (full-resume mode)' : ''}`);
    if (typeof t.apply_s === 'number') parts.push(`Word file ${t.apply_s.toFixed(1)}s`);
    return `<div class="optimizer-timings" style="font-size:0.75rem; color:#64748b; margin-top:4px;">Took ${t.total_s.toFixed(1)}s${parts.length ? ' &middot; ' + parts.join(' &middot; ') : ''}</div>`;
}

// =========================================================================
// Cloud alignment (cloud_align.py): the JD's primary cloud vs the resume / consultant.
// Equivalent services are talking points to CONFIRM with the consultant - never written into the resume.
// =========================================================================
function renderCloudCheck(el, cc) {
    if (!el) return;
    if (!cc || !cc.target) { el.style.display = 'none'; el.innerHTML = ''; return; }
    const ok = !!cc.ok;
    el.style.display = 'block';
    el.style.background = ok ? '#ecfdf5' : '#fffbeb';
    el.style.border = ok ? '1px solid #a7f3d0' : '1px solid #fde68a';
    el.style.color = ok ? '#065f46' : '#92400e';
    const ticks = Object.entries(cc.sections || {}).map(([k, v]) => `${v ? '✓' : '✗'} ${escapeHtml(k.replace('_', ' '))}`).join(' &middot; ');
    const eq = (cc.equivalents || []).length
        ? `<div style="margin-top:6px; color:#78350f;">Equivalent services to ask the consultant about (only if they really used them): ` +
          cc.equivalents.slice(0, 8).map(e => `${escapeHtml(e.from)} &rarr; ${escapeHtml(e.to)}`).join(', ') + '</div>'
        : '';
    el.innerHTML = `<b>${ok ? '☁ Cloud check passed' : '⚠ Cloud mismatch'}:</b> ${escapeHtml(cc.message)}<div style="margin-top:4px; font-size:0.8rem;">${ticks}</div>${eq}`;
}

let pdCloudTimer = null;
function pdCloudSchedule() {
    clearTimeout(pdCloudTimer);
    pdCloudTimer = setTimeout(pdCloudCheck, 450);
}

async function pdCloudCheck() {
    const note = document.getElementById('pd-cloud-note');
    const jd = document.getElementById('pd-raw-text')?.value || '';
    if (!note) return;
    if (jd.trim().length < 20) { note.style.display = 'none'; return; }
    const cid = parseInt(document.getElementById('pd-consultant-select')?.value || '');
    try {
        const res = await fetch('/api/jd/cloud', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jd_text: jd, candidate_id: cid || null }) });
        const d = await res.json();
        if (!res.ok || !d.primary) { note.style.display = 'none'; return; }
        const others = (d.mentioned || []).filter(c => c !== d.primary);
        const cand = (state.consultants || []).find(c => c.id === cid);
        note.style.display = 'block';
        if (d.mismatch) {
            note.style.background = '#fffbeb'; note.style.border = '1px solid #fde68a'; note.style.color = '#92400e';
            note.innerHTML = `⚠ <b>JD cloud: ${escapeHtml(d.primary)}</b>${others.length ? ` (also mentions ${escapeHtml(others.join(', '))})` : ''} - ` +
                `${escapeHtml(cand ? cand.name : 'this consultant')}'s profile shows ${escapeHtml(d.consultant_clouds.join(', '))}. ` +
                `Optimize the resume for this JD and confirm their ${escapeHtml(d.primary)} experience before sending.`;
        } else {
            note.style.background = '#eff6ff'; note.style.border = '1px solid #bfdbfe'; note.style.color = '#1e3a8a';
            note.innerHTML = `☁ <b>JD cloud: ${escapeHtml(d.primary)}</b>${others.length ? ` (also mentions ${escapeHtml(others.join(', '))})` : ''}` +
                (d.consultant_clouds && d.consultant_clouds.length ? ` - matches the consultant's profile.` : '');
        }
    } catch (err) { note.style.display = 'none'; }
}

// =========================================================================
// Saved, JD-tailored resume versions (resume_versions.py): nothing is saved until the recruiter
// clicks Save; the file is named Name_PrimarySkill.docx (_v2, _v3 ... never overwritten).
// =========================================================================
async function saveResumeVersion(v) {
    const res = await fetch('/api/optimized-resumes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: v.candidateId, job_id: v.jobId || null, jd_text: v.jdText, ai_text: v.aiText,
            edited_text: v.editedText, docx_base64: v.docxB64 || '', primary_skill: v.primarySkill || '' })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || 'Could not save the resume.');
    return data;
}

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('btn-save-optimized-version');
    if (btn) btn.addEventListener('click', async () => {
        const lo = state.lastOptimized;
        const status = document.getElementById('save-version-status');
        if (!lo || !lo.candidateId) { showToast('Select the consultant this resume belongs to, then optimize and save.', 'warning'); return; }
        btn.disabled = true;
        try {
            const d = await saveResumeVersion({ ...lo, editedText: document.getElementById('result-preview-text').value });
            status.innerHTML = `<span style="color:#059669;">✓ Saved as <b>${escapeHtml(d.filename)}</b></span>` +
                ` &middot; <a href="/api/optimized-resumes/${d.id}/download">download</a>` + (d.note ? ` <span style="color:#b45309;">${escapeHtml(d.note)}</span>` : '');
            showToast(`Saved ${d.filename}`, 'success');
        } catch (err) { showToast(err.message, 'error', 6000); }
        finally { btn.disabled = false; }
    });
    initDraftOptimize();
});

// ---- The optimize panel, opened from Paste Requirement & Draft (mode 'paste') or a Jobs row (mode 'job'):
// Optimize -> review / edit side by side -> Save (+ use in the draft / create the job's draft) | Download | Re-optimize | Discard.
// Nothing is saved until the recruiter clicks Save / Download / Create Draft.
const pdResume = { versionId: null, filename: '', result: null, candId: null };
// runSeq: every open / optimize run gets a new number. A slow answer from an earlier run (another job,
// or a window that was closed) carries an old number and is ignored - it must never land in this window.
const optPanel = { mode: 'paste', candId: null, jobId: null, emailInput: null, saved: null, savedText: '', runSeq: 0 };
// Unsaved optimize results per consultant + job, kept while the page is open: closing the window (x) and
// reopening the same job brings the result back instead of running the AI again. Discard forgets it.
const optCache = {};
const _hash = (t) => { let h = 0; for (let i = 0; i < t.length; i++) h = (h * 31 + t.charCodeAt(i)) | 0; return h; };
const optKey = () => optPanel.mode === 'job' ? `${optPanel.candId}:${optPanel.jobId}`
    : `${optPanel.candId}:p:${_hash((document.getElementById('do-jd')?.value || '').trim())}`;

function pdResumeReset() {
    pdResume.versionId = null; pdResume.filename = ''; pdResume.result = null; pdResume.candId = null; pdResume.versionText = '';
    const el = document.getElementById('pd-resume-version');
    if (el) el.innerHTML = "Attaching: the consultant's original resume.";
}

function initDraftOptimize() {
    const $ = id => document.getElementById(id);
    if (!$('btn-pd-optimize')) return;
    $('btn-pd-optimize').addEventListener('click', () => {
        const candId = parseInt($('pd-consultant-select')?.value || '');
        const jd = ($('pd-raw-text')?.value || '').trim();
        if (!candId) { showToast('Select the consultant first.', 'warning'); return; }
        if (jd.length < 30) { showToast('Paste the requirement (JD) first.', 'warning'); return; }
        openOptimizePanel({ mode: 'paste', candId, jdText: jd });
    });
    $('btn-do-reoptimize').addEventListener('click', () => draftOptimizeRun(false));
    const close = () => {
        $('modal-draft-optimize').style.display = 'none';
        if (optPanel.mode !== 'job' && doBox.visible()) pdBox.set(doBox.values(), 'Carried over from the Optimize window - check it, then Create Gmail Draft.');
    };
    $('btn-close-draft-optimize').addEventListener('click', close);
    $('btn-do-discard').addEventListener('click', () => { pdResume.result = null; delete optCache[optKey()]; close(); showToast('Discarded - nothing was saved.', 'info'); });
    $('do-optimized').addEventListener('input', () => { const c = optCache[optKey()]; if (c) c.edited = $('do-optimized').value; });
    $('btn-do-save').addEventListener('click', () => (optPanel.mode === 'job' ? jobOptimizeSaveAndDraft() : draftOptimizeSave()));
    $('btn-do-download').addEventListener('click', optimizeDownload);
    $('pd-consultant-select')?.addEventListener('change', () => { if (pdResume.versionId) pdResumeReset(); pdConsultantChanged(); });
    $('pd-raw-text')?.addEventListener('input', () => { clearTimeout(pdState.readTimer); pdState.readTimer = setTimeout(pdRead, 1200); });
}

// opts: {mode, candId, jdText?, jobId?, emailInput?}. Returns true when an earlier result for this job
// was brought back (nothing to run).
async function openOptimizePanel(opts) {
    const $ = id => document.getElementById(id);
    Object.assign(optPanel, { mode: opts.mode, candId: opts.candId, jobId: opts.jobId || null, emailInput: opts.emailInput || null, saved: null, savedText: '' });
    optPanel.runSeq++;               // anything still running for the previous window is now stale
    pdResume.result = null;
    $('do-optimized').value = '';
    $('do-original').value = '';
    $('do-cloud').style.display = 'none';
    $('do-filename').innerHTML = '';
    $('do-status').innerHTML = '';
    doBox.reset();
    pdResume.result = null;
    $('do-jd').value = opts.jdText || '';
    $('do-jd-wrap').style.display = opts.mode === 'job' ? '' : 'none';
    $('do-title').textContent = opts.title ? `⚡ Optimize Resume - ${opts.title}` : '⚡ Optimize Resume for this Requirement';
    $('btn-do-save').innerHTML = opts.mode === 'job' ? '✉️ Save &amp; Create Draft' : '💾 Save &amp; use in this draft';
    $('btn-do-download').style.display = 'none';
    $('modal-draft-optimize').style.display = 'flex';
    if (await optRestore(opts)) return true;
    if (opts.mode === 'job' && !opts.jdText) return false;     // the job's JD is still loading (jobOptimizeOpen)
    draftOptimizeRun(true);
    return false;
}

// Same consultant + job again: the unsaved result from earlier (this page), else the version saved for this job.
async function optRestore(opts) {
    const $ = id => document.getElementById(id);
    const seq = optPanel.runSeq;
    const cached = optCache[optKey()];
    let original = '';
    const loadOriginal = async () => {
        try { const c = await (await fetch(`/api/consultants/${optPanel.candId}`)).json(); original = (c.resume_text || '').trim(); } catch (e) { /* shown empty */ }
        if (seq !== optPanel.runSeq) return;
        $('do-original').value = original || 'No resume text on file for this consultant.';
    };
    if (cached) {
        await loadOriginal();
        pdResume.result = cached.result;
        pdResume.candId = optPanel.candId;
        if (cached.jd) $('do-jd').value = cached.jd;
        $('do-optimized').value = cached.edited;
        renderCloudCheck($('do-cloud'), cached.result.cloud_check);
        $('do-status').innerHTML = `<b>Your earlier result for this job</b> (not saved yet) - match ${cached.result.initial_match_percentage ?? '?'}% &rarr; ${cached.result.target_match_percentage ?? '?'}%. Nothing was run again. Click <b>Re-optimize</b> to run the AI again.`;
    } else {
        if (opts.mode !== 'job') return false;      // pasted requirement: only an unsaved earlier result can come back
        let v = null;
        try { v = (await (await fetch(`/api/optimized-resumes/for-job?candidate_id=${optPanel.candId}&job_id=${optPanel.jobId}`)).json()).version; } catch (e) { v = null; }
        if (seq !== optPanel.runSeq) return true;      // the window moved on - nothing more to do here
        if (!v || !v.text) return false;
        await loadOriginal();
        if (seq !== optPanel.runSeq) return true;
        pdResume.result = { optimized: true, updated_resume_text: v.text, saved_version: true };
        pdResume.candId = optPanel.candId;
        $('do-optimized').value = v.text;
        $('do-cloud').style.display = 'none';
        optPanel.saved = { id: v.id, filename: v.filename };
        optPanel.savedText = v.text;
        $('do-status').innerHTML = `<b>Showing the resume you saved for this job</b> - ${escapeHtml(v.filename)} (${escapeHtml(v.created_at)}). Nothing was run again. Click <b>Re-optimize</b> to run the AI again.`;
        $('do-filename').innerHTML = `✓ Saved as <b>${escapeHtml(v.filename)}</b> - it will be attached to this job's draft.`;
    }
    $('btn-do-save').disabled = false;
    $('btn-do-download').style.display = '';
    $('btn-do-reoptimize').disabled = false;
    if (($('do-jd').value || '').trim().length < 30) $('do-jd').value = opts.jdText || opts.summary || '';
    doEmailGenerate();
    return true;
}

async function draftOptimizeRun(firstTime) {
    const $ = id => document.getElementById(id);
    const candId = optPanel.candId;
    const jd = ($('do-jd').value || '').trim();
    if (!candId) { showToast('Select the consultant first.', 'warning'); return; }
    if (jd.length < 30) { $('do-status').innerHTML = '<span style="color:#b45309;">Paste the job description into the box above, then click Re-optimize.</span>'; $('btn-do-reoptimize').disabled = false; return; }
    $('do-cloud').style.display = 'none';
    $('btn-do-save').disabled = true;
    $('btn-do-download').style.display = 'none';
    $('btn-do-reoptimize').disabled = true;
    optPanel.saved = null;
    const seq = ++optPanel.runSeq;
    const key = optKey();                       // this run's job, fixed now - not whatever is open when it answers
    const stale = () => seq !== optPanel.runSeq;
    pdResume.result = null;
    if (firstTime || pdResume.candId !== candId || !$('do-original').value.trim()) {
        $('do-original').value = 'Loading...';
        $('do-optimized').value = '';
        try {
            const r = await fetch(`/api/consultants/${candId}`);
            const c = await r.json();
            if (stale()) return;
            $('do-original').value = (c.resume_text || '').trim() || 'No resume text on file for this consultant.';
        } catch (err) { if (!stale()) $('do-original').value = 'Could not load the original resume.'; }
    }
    if (stale()) return;
    pdResume.candId = candId;
    const started = Date.now();
    const tick = () => { if (!stale()) $('do-status').innerHTML = `<b>Optimizing - ${Math.round((Date.now() - started) / 1000)}s.</b> The AI checks the match, then writes only the changes. Nothing is saved until you click Save.`; };
    tick();
    const timer = setInterval(tick, 1000);
    try {
        const res = await fetch('/api/resume-bot/optimize', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jd_text: jd, candidate_id: candId, use_stored_file: true }) });
        const data = await res.json().catch(() => ({}));
        if (stale()) {                          // a late answer for another job / a closed window: never shown here,
            if (res.ok && data.optimized) optCache[key] = { result: data, edited: data.updated_resume_text || '', jd };   // kept for ITS job
            return;
        }
        if (!res.ok || data.error) throw new Error(data.error || 'Optimization failed.');
        pdResume.result = data;
        $('do-optimized').value = data.updated_resume_text || '';
        if (data.optimized) optCache[key] = { result: data, edited: data.updated_resume_text || '', jd };
        renderCloudCheck($('do-cloud'), data.cloud_check);
        $('do-status').innerHTML = (data.optimized
            ? `<b>Optimized</b> - match ${data.initial_match_percentage ?? '?'}% &rarr; ${data.target_match_percentage ?? '?'}%. Review and edit on the right, then ${optPanel.mode === 'job' ? 'Download or Create Draft' : 'Save'}.`
            : `<b>Not changed:</b> ${escapeHtml(data.not_optimized_reason || 'the resume was left as it is.')}` +
              (data.ai_unavailable_reason ? `<div style="color:#b45309; margin-top:4px;"><b>Why the AI didn't run:</b> ${escapeHtml(data.ai_unavailable_reason)}</div>` : '')) +
            optimizerTimingsHtml(data.timings);
        $('do-filename').innerHTML = data.suggested_filename
            ? `Will be saved as <b>${escapeHtml(data.suggested_filename)}</b>${optPanel.mode === 'job' ? ' and attached to this job\'s draft' : ' and attached to this draft'}.` : '';
        $('btn-do-save').disabled = !data.optimized;
        $('btn-do-download').style.display = data.optimized ? '' : 'none';
        // show the email before anything is drafted (from the Paste window always - written from the original
        // resume when the optimizer left it unchanged)
        if (data.optimized || (optPanel.mode !== 'job' && data.ai_powered !== false)) doEmailGenerate();
    } catch (err) {
        if (!stale()) $('do-status').innerHTML = `<span style="color:#b91c1c;">${escapeHtml(err.message)}</span>`;
    } finally {
        clearInterval(timer);
        if (!stale()) $('btn-do-reoptimize').disabled = false;
    }
}

// Save once per edited text: Download and Create Draft reuse the saved version unless the text changed.
async function optimizeEnsureSaved() {
    const $ = id => document.getElementById(id);
    const data = pdResume.result;
    if (!data || !data.optimized) throw new Error('Optimize the resume first.');
    const edited = $('do-optimized').value;
    if (optPanel.saved && optPanel.savedText === edited) return optPanel.saved;
    const d = await saveResumeVersion({ candidateId: optPanel.candId, jobId: optPanel.jobId, jdText: $('do-jd').value, aiText: data.updated_resume_text || '',
        editedText: edited, docxB64: data.format_preserved ? (data.docx_base64 || '') : '', primarySkill: data.primary_skill || '' });
    optPanel.saved = d;
    optPanel.savedText = edited;
    delete optCache[optKey()];      // saved: reopening this job now shows the saved version
    $('do-filename').innerHTML = `✓ Saved as <b>${escapeHtml(d.filename)}</b>` + (d.note ? ` <span style="color:#b45309;">${escapeHtml(d.note)}</span>` : '');
    return d;
}

async function optimizeDownload() {
    try {
        const d = await optimizeEnsureSaved();
        window.location.href = `/api/optimized-resumes/${d.id}/download`;
    } catch (err) { showToast(err.message, 'error', 6000); }
}

async function draftOptimizeSave() {
    const $ = id => document.getElementById(id);
    $('btn-do-save').disabled = true;
    try {
        const d = await optimizeEnsureSaved();
        pdResume.versionId = d.id;
        pdResume.filename = d.filename;
        pdResume.versionText = $('do-optimized').value;
        $('pd-resume-version').innerHTML = `Attaching: <b style="color:#059669;">${escapeHtml(d.filename)}</b> (tailored, saved) ` +
            `&middot; <a href="/api/optimized-resumes/${d.id}/download">view</a> &middot; <a href="#" id="pd-use-original">use original instead</a>` +
            (d.note ? `<div style="color:#b45309;">${escapeHtml(d.note)}</div>` : '');
        $('pd-use-original').addEventListener('click', (e) => { e.preventDefault(); pdResumeReset(); });
        $('modal-draft-optimize').style.display = 'none';
        if (doBox.visible()) {
            pdBox.set(doBox.values(), `Email from the Optimize window, written from ${d.filename} - check it, then Create Gmail Draft.`);
            showToast(`Saved ${d.filename} - it and this email will be used for the draft.`, 'success', 6000);
        } else {
            showToast(`Saved ${d.filename} - it will be attached to this draft. Writing the email from it...`, 'success', 6000);
            pdBox.generate();
        }
    } catch (err) {
        showToast(err.message, 'error', 6000);
        $('btn-do-save').disabled = false;
    }
}

// ---- Jobs tab: Optimize Resume opens the panel in place (no tab switch)
async function jobOptimizeOpen(jobId, candId, row) {
    const job = (state.jobs || []).find(j => j.id === jobId);
    if (!job) { showToast('Job not found - refresh the Jobs list.', 'error'); return; }
    if (!candId) { showToast('Please select a Target Candidate first', 'warning'); return; }
    const header = [job.title, job.company].filter(Boolean).join(' - ');
    const desc = (job.description || '').trim();
    let full = (job.full_description || '').trim() || ((job.source === 'Manual Paste' || desc.length >= FULL_JD_MIN_CHARS) ? desc : '');
    const emailInput = row ? row.querySelector('.email-inline-input') : null;
    const restored = await openOptimizePanel({ mode: 'job', candId, jobId, emailInput, title: header,
        jdText: full, summary: [header, desc].filter(Boolean).join('\n\n') });
    if (restored || full) return;
    const $ = id => document.getElementById(id);
    const summary = [header + (job.location ? ` (${job.location})` : ''), desc].filter(Boolean).join('\n\n');
    $('do-jd').value = summary;
    let why = '';
    if (JD_AUTO_FETCH_RE.test(job.url || '')) {
        $('do-status').innerHTML = '<b>Reading the full job description from the posting...</b>';
        try {
            const seq = optPanel.runSeq;
            const r = await fetch(`/api/jobs/${job.id}/full-description`, { method: 'POST' });
            const d = await r.json().catch(() => ({}));
            if (seq !== optPanel.runSeq) return;       // another job was opened meanwhile
            if (r.ok && d.description) {
                job.full_description = d.description;
                if ($('do-jd').value === summary) $('do-jd').value = d.description;
                return draftOptimizeRun(true);
            }
            why = d.error || '';
        } catch (err) { why = 'The full description could not be read automatically.'; }
    }
    $('do-jd-wrap').open = true;
    $('do-original').value = '';
    $('do-status').innerHTML = `<span style="color:#b45309;">${why ? escapeHtml(why) + ' ' : ''}Only a short summary is saved for this job. ${job.url ? `<a href="${escapeHtml(job.url)}" target="_blank" rel="noopener noreferrer">Open the posting ↗</a>, copy` : 'Copy'} the full job description into the box above, then click Re-optimize.</span>`;
    $('btn-do-reoptimize').disabled = false;
}

// ---- Shared email component: "Email that will be sent" (checklist, To, Subject, Body, Ask AI, Undo,
// Rewrite). Used by the Optimize window ('do') and the Paste Requirement window ('pd'), so both behave the
// same. ctx() returns what the server needs: candidate_id, job_id or the pasted job fields, jd_text,
// resume_text (optimized text on screen) / optimized_resume_id, to_email.
function emailBox(prefix, ctx) {
    const $ = (id) => document.getElementById(id);
    const box = { undo: [], rendered: false, seq: 0 };
    const render = () => {
        if (box.rendered) return;
        box.rendered = true;
        $(`${prefix}-email-box`).innerHTML = `
            <div style="border:1px solid #e2e8f0; border-radius:10px; padding:12px 14px; background:#ffffff;">
                <div style="display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">
                    <span style="font-size:0.85rem; font-weight:800; color:#0f172a;">✉️ Email that will be sent</span>
                    <div style="display:flex; gap:6px; align-items:center;">
                        <span id="${prefix}-email-note" style="font-size:0.75rem; color:#059669; font-weight:600;"></span>
                        <button type="button" class="btn btn-secondary btn-sm" id="btn-${prefix}-email-undo" disabled>↶ Undo</button>
                        <button type="button" class="btn btn-secondary btn-sm" id="btn-${prefix}-email-rewrite" title="Write the email again from the resume as it is now">↻ Rewrite email from the resume</button>
                    </div>
                </div>
                <div id="${prefix}-email-status" style="font-size:0.82rem; color:#1e3a8a; margin-bottom:6px;"></div>
                <div id="${prefix}-email-checklist" style="display:none; font-size:0.8rem; line-height:1.6; padding:8px 10px; border-radius:8px; background:#f8fafc; border:1px solid #e2e8f0; margin-bottom:8px;"></div>
                <div style="display:grid; grid-template-columns: 1fr 1.4fr; gap:8px; margin-bottom:8px;">
                    <div><label style="font-size:0.75rem; font-weight:700; color:#475569;">To</label><input id="${prefix}-email-to" class="form-control" style="font-size:0.85rem;" placeholder="recruiter@company.com"></div>
                    <div><label style="font-size:0.75rem; font-weight:700; color:#475569;">Subject</label><input id="${prefix}-email-subject" class="form-control" style="font-size:0.85rem; font-weight:600;"></div>
                </div>
                <textarea id="${prefix}-email-body" rows="12" class="form-control" style="font-size:0.84rem; line-height:1.55;"></textarea>
                <div style="display:flex; gap:8px; margin-top:8px;">
                    <input id="${prefix}-email-instr" class="form-control" placeholder="Tell the AI what to change, e.g. 'shorter' or 'notice period is 30 days'" style="flex:1; font-size:0.85rem;">
                    <button type="button" class="btn btn-primary btn-sm" id="btn-${prefix}-email-ai">Ask AI</button>
                </div>
            </div>`;
        $(`btn-${prefix}-email-rewrite`).addEventListener('click', () => box.generate());
        $(`btn-${prefix}-email-ai`).addEventListener('click', () => box.ask());
        $(`${prefix}-email-instr`).addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); box.ask(); } });
        $(`btn-${prefix}-email-undo`).addEventListener('click', () => {
            const prev = box.undo.pop();
            if (prev) { $(`${prefix}-email-subject`).value = prev.subject; $(`${prefix}-email-body`).value = prev.body; }
            $(`btn-${prefix}-email-undo`).disabled = !box.undo.length;
            $(`${prefix}-email-note`).textContent = prev ? '↶ Undone' : '';
        });
    };
    box.reset = () => { box.seq++; box.undo = []; const el = $(`${prefix}-email-box`); if (el) el.style.display = 'none'; if (box.rendered) { $(`${prefix}-email-body`).value = ''; } };
    box.visible = () => { const el = $(`${prefix}-email-box`); return !!el && el.style.display !== 'none' && !!(box.rendered && $(`${prefix}-email-body`).value.trim()); };
    box.values = () => ({ to: $(`${prefix}-email-to`).value.trim(), subject: $(`${prefix}-email-subject`).value.trim(), body: $(`${prefix}-email-body`).value.trim(),
        checklist: box.rendered ? $(`${prefix}-email-checklist`).innerHTML : '' });
    box.set = (v, note) => {      // an email carried over from another box (Optimize window -> Paste window)
        render();
        $(`${prefix}-email-box`).style.display = '';
        if ($(`${prefix}-email-body`).value && $(`${prefix}-email-body`).value !== v.body) { box.undo.push({ subject: $(`${prefix}-email-subject`).value, body: $(`${prefix}-email-body`).value }); $(`btn-${prefix}-email-undo`).disabled = false; }
        $(`${prefix}-email-to`).value = v.to || '';
        $(`${prefix}-email-subject`).value = v.subject || '';
        $(`${prefix}-email-body`).value = v.body || '';
        $(`${prefix}-email-checklist`).innerHTML = v.checklist || '';
        $(`${prefix}-email-checklist`).style.display = v.checklist ? '' : 'none';
        $(`${prefix}-email-status`).innerHTML = escapeHtml(note || '');
    };
    box.generate = async () => {
        render();
        $(`${prefix}-email-box`).style.display = '';
        $(`${prefix}-email-status`).innerHTML = '<b>Writing the email</b> from the job description and the resume that will be attached...';
        $(`btn-${prefix}-email-rewrite`).disabled = true;
        const seq = ++box.seq;
        try {
            const res = await fetch('/api/outreach/application-preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(ctx()) });
            const d = await res.json().catch(() => ({}));
            if (seq !== box.seq) return;           // reset / reopened / rewritten again meanwhile
            if (!res.ok) { $(`${prefix}-email-status`).innerHTML = `<span style="color:#b91c1c;">${escapeHtml(d.error || 'Could not write the email.')}</span>`; return; }
            if ($(`${prefix}-email-body`).value) { box.undo.push({ subject: $(`${prefix}-email-subject`).value, body: $(`${prefix}-email-body`).value }); $(`btn-${prefix}-email-undo`).disabled = false; }
            $(`${prefix}-email-to`).value = d.to || '';
            $(`${prefix}-email-subject`).value = d.subject || '';
            $(`${prefix}-email-body`).value = d.body || '';
            const items = d.checklist || [];
            $(`${prefix}-email-checklist`).innerHTML = items.length ? '<b style="color:#334155;">What this job asks</b><br>' + items.map(i =>
                `<span style="color:${i.ok ? '#059669' : '#b45309'};">${i.ok ? '✓' : '⚠'} ${escapeHtml(i.text)}</span>`).join('<br>') : '';
            $(`${prefix}-email-checklist`).style.display = items.length ? '' : 'none';
            $(`${prefix}-email-status`).innerHTML = 'Check and edit the email - the draft gets exactly this email and the resume shown as attached.'
                + (d.fallback ? ' <span style="color:#b45309;">(Standard email used - the AI could not write one that only uses facts from the resume.)</span>' : '');
            $(`${prefix}-email-note`).textContent = '';
        } catch (err) {
            $(`${prefix}-email-status`).innerHTML = `<span style="color:#b91c1c;">${escapeHtml(err.message)}</span>`;
        } finally { $(`btn-${prefix}-email-rewrite`).disabled = false; }
    };
    box.ask = async () => {
        const instruction = $(`${prefix}-email-instr`).value.trim();
        if (!instruction) return;
        const btn = $(`btn-${prefix}-email-ai`);
        btn.disabled = true; btn.textContent = 'Thinking...';
        try {
            const cur = { subject: $(`${prefix}-email-subject`).value, body: $(`${prefix}-email-body`).value };
            const c = ctx();
            const res = await fetch('/api/ai/personalize-draft', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign({}, c, { instruction, current_subject: cur.subject, current_body: cur.body })) });
            const d = await res.json().catch(() => ({}));
            if (!res.ok || !d.success) { showToast(d.error || 'The AI could not answer.', 'error'); return; }
            if (d.applied) {
                box.undo.push(cur);
                $(`btn-${prefix}-email-undo`).disabled = false;
                $(`${prefix}-email-subject`).value = d.subject;
                $(`${prefix}-email-body`).value = d.body;
                $(`${prefix}-email-instr`).value = '';
                $(`${prefix}-email-note`).textContent = `✓ ${d.lines_changed} line${d.lines_changed === 1 ? '' : 's'} changed`;
            }
            const extra = (d.needs_info || []).length ? ' ❓ ' + d.needs_info.join(' ❓ ') : '';
            $(`${prefix}-email-status`).innerHTML = `<span style="color:${d.applied ? '#334155' : '#b45309'};">${escapeHtml((d.reply || '') + extra)}</span>`;
        } finally { btn.disabled = false; btn.textContent = 'Ask AI'; }
    };
    return box;
}

// The Optimize window's email (Jobs tab): written from the optimized resume on screen.
const doBox = emailBox('do', () => {
    const paste = optPanel.mode !== 'job';
    const v = (id) => (document.getElementById(id)?.value || '').trim();
    return { candidate_id: optPanel.candId, job_id: paste ? null : optPanel.jobId, jd_text: document.getElementById('do-jd').value,
        job_title: paste ? v('pd-title') : '', company: paste ? v('pd-company') : '',
        resume_text: document.getElementById('do-optimized').value, tailored: !!(pdResume.result && pdResume.result.optimized),
        to_email: paste ? v('pd-email') : (optPanel.emailInput ? optPanel.emailInput.value : '').trim() };
});
const doEmailGenerate = () => doBox.generate();

// ---- Paste Requirement window: AI reading, match %, "already applied", and the same email component
const pdState = { readTimer: null, read: null, lastRaw: '' };
const pdVal = (id) => (document.getElementById(id)?.value || '').trim();
const pdBox = emailBox('pd', () => ({ candidate_id: parseInt(pdVal('pd-consultant-select')) || null, job_title: pdVal('pd-title'), company: pdVal('pd-company'),
    jd_text: pdVal('pd-raw-text'), to_email: pdVal('pd-email'), optimized_resume_id: pdResume.versionId || null, resume_text: pdResume.versionText || '',
    tailored: !!pdResume.versionId }));

function pdInfo() {
    const el = document.getElementById('pd-ai-info');
    const r = pdState.read;
    if (!el) return;
    if (!r) { el.style.display = 'none'; el.innerHTML = ''; return; }
    const bits = [r.title, r.company, r.location, r.employment_type, r.rate].filter(Boolean).map(escapeHtml).join(' · ');
    const skills = (r.skills || []).length ? `<div style="color:#475569;">Skills: ${r.skills.map(escapeHtml).join(', ')}</div>` : '';
    const m = pdState.match;
    const match = !m ? '' : m.loading ? '<div style="color:#1e3a8a;">Checking the match for this consultant...</div>'
        : m.error ? `<div style="color:#b45309;">Match check unavailable: ${escapeHtml(m.error)}</div>`
        : `<div id="pd-match" style="font-weight:700; color:${m.match >= 60 ? '#059669' : '#b45309'};">Match for this consultant: ${m.match ?? '?'}%`
          + (m.match != null && m.match < 60 ? ' - weak match, consider another consultant' : '') + `</div>`;
    const dups = (pdState.dups || []).length ? `<div id="pd-dups" style="color:#b45309;">⚠ Already in the tracker: ${pdState.dups.map(d =>
        `${escapeHtml(d.title || '')}${d.company ? ' - ' + escapeHtml(d.company) : ''} (${escapeHtml(d.status)}, ${escapeHtml(d.when)}, ${escapeHtml(d.why)})`).join('; ')}</div>` : '';
    el.innerHTML = `<div><b>${r.ai ? '✨ AI read:' : 'Read:'}</b> ${bits || '(no details found)'}</div>${skills}${match}${dups}`;
    el.style.display = '';
}

async function pdRead() {
    const raw = pdVal('pd-raw-text');
    if (raw.length < 80 || raw === pdState.lastRaw) return;
    pdState.lastRaw = raw;
    try {
        const res = await fetch('/api/outreach/read-requirement', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ raw_text: raw, candidate_id: parseInt(pdVal('pd-consultant-select')) || null }) });
        const r = await res.json().catch(() => ({}));
        if (!res.ok || raw !== pdVal('pd-raw-text')) return;
        pdState.read = r;
        pdState.dups = r.duplicates || [];
        const fill = (id, v) => { const el = document.getElementById(id); if (el && v && !el.value.trim()) el.value = v; };   // never overwrite what was typed
        fill('pd-title', r.title); fill('pd-company', r.company); fill('pd-email', r.recruiter_email); fill('pd-rate', r.rate);
        pdInfo();
        pdMatch();
        pdBox.generate();
    } catch (err) { /* reading is a convenience - the form still works by hand */ }
}

async function pdMatch() {
    const cid = parseInt(pdVal('pd-consultant-select'));
    const jd = pdVal('pd-raw-text');
    if (!cid || jd.length < 80 || !pdState.read) return;
    pdState.match = { loading: true };
    pdInfo();
    try {
        const res = await fetch('/api/outreach/match-check', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidate_id: cid, jd_text: jd }) });
        const d = await res.json().catch(() => ({}));
        if (cid !== parseInt(pdVal('pd-consultant-select'))) return;
        pdState.match = res.ok ? d : { error: d.error || 'unknown' };
    } catch (err) { pdState.match = { error: err.message }; }
    pdInfo();
}

async function pdConsultantChanged() {
    if (!pdState.read) return;
    const cid = parseInt(pdVal('pd-consultant-select'));
    try {
        const res = await fetch('/api/outreach/read-requirement', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ duplicates_only: true, candidate_id: cid, title: pdVal('pd-title'), company: pdVal('pd-company'), recruiter_email: pdVal('pd-email') }) });
        pdState.dups = (await res.json().catch(() => ({}))).duplicates || [];
    } catch (err) { pdState.dups = []; }
    pdInfo();
    pdMatch();
    pdBox.generate();
}

function pdResetAi() {
    clearTimeout(pdState.readTimer);
    Object.assign(pdState, { read: null, lastRaw: '', match: null, dups: [] });
    pdInfo();
    pdBox.reset();
}

async function jobOptimizeSaveAndDraft() {
    const $ = id => document.getElementById(id);
    const to = $('do-email-to').value.trim();
    const subject = $('do-email-subject').value.trim();
    const body = $('do-email-body').value.trim();
    if (!doBox.visible() || !body) { showToast('Wait for the email to be written (below the resumes), then save.', 'warning'); return; }
    if (!to || !to.includes('@')) { showToast('Add the recipient email (To) in the email section first.', 'warning'); $('do-email-to').focus(); return; }
    if (/\[please (fill|add):/i.test(body) && !confirm('The email still has [please fill / add] parts. Create the draft anyway and fill them in Gmail?')) return;
    $('btn-do-save').disabled = true;
    try {
        const d = await optimizeEnsureSaved();
        $('modal-draft-optimize').style.display = 'none';
        await createJobDraft(optPanel.jobId, optPanel.candId, to, null, d.id, { subject, body });
    } catch (err) {
        showToast(err.message, 'error', 6000);
    } finally { $('btn-do-save').disabled = false; }
}

// ---- Jobs tab: "Applied" column (per job)
async function setJobApplyStatus(jobId, status, selectEl, candidateId = null) {
    const res = await fetch(`/api/jobs/${jobId}/apply-status`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, candidate_id: candidateId }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(d.error || 'Could not save the status.', 'error'); return; }
    const job = (state.jobs || []).find(j => j.id === jobId);
    if (job) job.apply_status = status;
    if (selectEl) selectEl.style.background = status ? '#ecfdf5' : '#fff';
    showToast(`Marked: ${d.label}`, 'success', 2500);
}


// =========================================================================
// Application tracker (application_tracker.py): every application of every consultant
// =========================================================================
const trk = { candId: '', statuses: [], methods: [], rows: [], wired: false };

function openTracker(candId) {
    const $ = id => document.getElementById(id);
    trkWire();
    const sel = $('trk-consultant');
    sel.innerHTML = '<option value="">All my consultants</option>' + (state.consultants || [])
        .map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    sel.value = candId ? String(candId) : '';
    $('trk-add').style.display = 'none';
    $('trk-msg').innerHTML = '';
    $('modal-tracker').style.display = 'flex';
    trkLoad();
}

function trkWire() {
    if (trk.wired) return;
    trk.wired = true;
    const $ = id => document.getElementById(id);
    $('btn-close-tracker').addEventListener('click', () => { $('modal-tracker').style.display = 'none'; });
    ['trk-consultant', 'trk-status', 'trk-due'].forEach(id => $(id).addEventListener('change', trkLoad));
    $('trk-add-btn').addEventListener('click', () => {
        if (!$('trk-consultant').value) { trkMsg('Choose a consultant first (top left), then add the application.', 'warn'); return; }
        $('trk-add').style.display = '';
        $('trk-a-title').focus();
    });
    $('trk-a-cancel').addEventListener('click', () => { $('trk-add').style.display = 'none'; });
    $('trk-a-save').addEventListener('click', trkAddSave);
    $('trk-replies-btn').addEventListener('click', trkCheckReplies);
    $('trk-open-replies').addEventListener('click', () => openReplies(trk.candId || ''));
    $('trk-open-reqs').addEventListener('click', () => openRequirements(trk.candId || ''));
}

function trkMsg(text, kind) {
    const colors = { ok: '#059669', warn: '#b45309', err: '#b91c1c' };
    document.getElementById('trk-msg').innerHTML = text ? `<span style="color:${colors[kind] || '#475569'};">${escapeHtml(text)}</span>` : '';
}

async function trkLoad() {
    const $ = id => document.getElementById(id);
    trk.candId = $('trk-consultant').value;
    const params = new URLSearchParams();
    if (trk.candId) params.set('candidate_id', trk.candId);
    if ($('trk-status').value) params.set('status', $('trk-status').value);
    if ($('trk-due').checked) params.set('due', '1');
    $('trk-export').href = '/api/applications/export-xlsx' + (trk.candId ? `?candidate_id=${trk.candId}` : '');
    $('trk-replies-btn').disabled = !trk.candId;
    const res = await fetch('/api/applications?' + params.toString());
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { trkMsg(d.error || 'Could not load applications.', 'err'); return; }
    trk.rows = d.applications;
    if (!trk.statuses.length) {
        trk.statuses = d.statuses;
        trk.methods = d.methods;
        $('trk-status').innerHTML = '<option value="">All statuses</option><option value="open">Open (in progress)</option>' +
            d.statuses.map(s => `<option value="${s.value}">${escapeHtml(s.label)}</option>`).join('');
        $('trk-a-status').innerHTML = d.statuses.map(s => `<option value="${s.value}" ${s.value === 'Applied' ? 'selected' : ''}>${escapeHtml(s.label)}</option>`).join('');
        $('trk-a-method').innerHTML = d.methods.map(m => `<option value="${m.value}">${escapeHtml(m.value ? m.label : 'How applied?')}</option>`).join('');
    }
    const s = d.summary;
    const chip = (label, n, color) => `<span style="background:${color}; border-radius:999px; padding:4px 12px; font-size:0.8rem; font-weight:700; color:#0f172a;">${label}: ${n}</span>`;
    $('trk-summary').innerHTML = chip('Total', s.total, '#f1f5f9') + chip('Applied this week', s.applied_this_week, '#eff6ff') +
        chip('Responses', s.responded, '#ecfdf5') + chip('Interviews', s.interviews, '#f5f3ff') + chip('Placed', s.placed, '#dcfce7') +
        chip('Follow-ups due', s.follow_ups_due, s.follow_ups_due ? '#fef3c7' : '#f1f5f9');
    trkRender();
}

function trkRender() {
    const body = document.getElementById('trk-body');
    const showCand = !trk.candId;
    document.querySelectorAll('.trk-col-cand').forEach(el => { el.style.display = showCand ? '' : 'none'; });
    if (!trk.rows.length) {
        body.innerHTML = '<tr><td colspan="11" style="text-align:center; color:#64748b; padding:20px;">No applications yet. Drafts, the Jobs "Applied" column and "+ Add application" all appear here.</td></tr>';
        return;
    }
    const opt = (list, cur) => list.map(o => `<option value="${o.value}" ${o.value === (cur || '') ? 'selected' : ''}>${escapeHtml(o.value ? o.label : '-')}</option>`).join('');
    body.innerHTML = trk.rows.map(r => `
        <tr data-app-id="${r.id}" style="${r.follow_up_due ? 'background:#fffbeb;' : ''}">
            <td style="min-width:136px; color:#0f172a;"><b>${escapeHtml(r.job_title || '')}</b><div style="color:#64748b;">${escapeHtml(r.job_company || '')}</div>
                ${r.job_url ? `<a href="${escapeHtml(r.job_url)}" target="_blank" rel="noopener noreferrer">posting ↗</a>` : ''}</td>
            <td class="trk-col-cand" style="${showCand ? '' : 'display:none;'} color:#0f172a;">${escapeHtml(r.candidate_name || '')}</td>
            <td><select class="form-control trk-f" data-f="status" style="font-size:0.78rem; min-width:128px;">${opt(trk.statuses, r.stage)}</select></td>
            <td><select class="form-control trk-f" data-f="apply_method" style="font-size:0.78rem; min-width:118px;">${opt(trk.methods, r.apply_method)}</select></td>
            <td style="white-space:nowrap; color:#0f172a;">${escapeHtml((r.applied_date || r.drafted_at || '').slice(0, 10))}${r.last_reply_at ? `<div style="color:#059669;">reply ${escapeHtml(r.last_reply_at.slice(0, 10))}</div>` : ''}</td>
            <td><input class="form-control trk-f" data-f="recruiter_email" value="${escapeHtml(r.recruiter_email || '')}" placeholder="email" style="font-size:0.78rem; min-width:140px;"></td>
            <td style="color:#0f172a;">${r.resume_filename ? `<a href="/api/optimized-resumes/${r.resume_version_id}/download">${escapeHtml(r.resume_filename)}</a>` : '<span style="color:#94a3b8;">original</span>'}</td>
            <td><input type="date" class="form-control trk-f" data-f="follow_up_date" value="${escapeHtml(r.follow_up_date || '')}" style="font-size:0.8rem;"></td>
            <td><input type="date" class="form-control trk-f" data-f="interview_at" value="${escapeHtml((r.interview_at || '').slice(0, 10))}" style="font-size:0.8rem;">
                <input class="form-control trk-f" data-f="interview_round" value="${escapeHtml(r.interview_round || '')}" placeholder="round" style="font-size:0.75rem; margin-top:3px;"></td>
            <td><textarea class="form-control trk-f" data-f="notes" rows="2" style="font-size:0.78rem; min-width:140px;">${escapeHtml(r.notes || '')}</textarea></td>
            <td style="white-space:nowrap;"><button type="button" class="btn btn-secondary btn-xs trk-hist">History</button>
                ${r.reply_count ? `<button type="button" class="btn btn-secondary btn-xs trk-rp" data-cand="${r.candidate_id}" title="Recruiter replies" style="margin-top:3px; ${r.replies_open ? 'border-color:#f59e0b; background:#fffbeb;' : ''}">💬 ${r.reply_count}${r.replies_open ? ' new' : ''}</button>` : ''}</td>
        </tr>`).join('');
    body.querySelectorAll('.trk-f').forEach(el => el.addEventListener('change', () => trkSave(el)));
    body.querySelectorAll('.trk-hist').forEach(b => b.addEventListener('click', () => trkHistory(b.closest('tr'))));
    body.querySelectorAll('.trk-rp').forEach(b => b.addEventListener('click', () =>
        openReplies(b.getAttribute('data-cand'), parseInt(b.closest('tr').getAttribute('data-app-id')))));
}

async function trkSave(el) {
    const tr = el.closest('tr');
    const id = parseInt(tr.getAttribute('data-app-id'));
    const res = await fetch(`/api/applications/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [el.getAttribute('data-f')]: el.value }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(d.error || 'Could not save.', 'error'); return; }
    const i = trk.rows.findIndex(r => r.id === id);
    if (i >= 0) trk.rows[i] = d.application;
    tr.style.background = d.application.follow_up_due ? '#fffbeb' : '';
    showToast('Saved', 'success', 1500);
}

async function trkHistory(tr) {
    const next = tr.nextElementSibling;
    if (next && next.classList.contains('trk-hist-row')) { next.remove(); return; }
    const res = await fetch(`/api/applications/${tr.getAttribute('data-app-id')}/events`);
    const d = await res.json().catch(() => ({ events: [] }));
    const row = document.createElement('tr');
    row.className = 'trk-hist-row';
    row.innerHTML = `<td colspan="11" style="background:#f8fafc; font-size:0.8rem; color:#334155;">` +
        ((d.events || []).map(e => `<div>${escapeHtml(e.created_at)} &middot; <b>${escapeHtml(e.who)}</b> &middot; ${escapeHtml(e.detail || e.kind)}</div>`).join('') || 'No history yet.') + '</td>';
    tr.after(row);
}

async function trkAddSave() {
    const v = id => document.getElementById(id).value.trim();
    const res = await fetch('/api/applications', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ candidate_id: parseInt(v('trk-consultant')), job_title: v('trk-a-title'), company: v('trk-a-company'),
            job_url: v('trk-a-url'), recruiter_email: v('trk-a-email'), status: v('trk-a-status'), apply_method: v('trk-a-method') }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { trkMsg(d.error || 'Could not add.', 'err'); return; }
    ['trk-a-title', 'trk-a-company', 'trk-a-url', 'trk-a-email'].forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('trk-add').style.display = 'none';
    trkMsg('Application added.', 'ok');
    trkLoad();
}

async function trkCheckReplies() {
    const btn = document.getElementById('trk-replies-btn');
    btn.disabled = true;
    trkMsg('Checking the consultant\'s Gmail (read-only)...', '');
    try {
        const res = await fetch('/api/applications/check-replies', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ candidate_id: parseInt(trk.candId) }) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { trkMsg(d.error || 'Could not check replies.', 'err'); return; }
        trkMsg(d.new_replies
            ? `${d.new_replies} new repl${d.new_replies === 1 ? 'y' : 'ies'} read and sorted (${d.replied.map(r => r.job_title).join(', ')}) - open 📨 Replies to answer.`
            : `Checked ${d.checked} open application${d.checked === 1 ? '' : 's'} - no new replies.`, d.new_replies ? 'ok' : '');
        trkLoad();
    } finally { btn.disabled = false; }
}

// =========================================================================
// Recruiter replies (reply_inbox.py): read, sorted by AI, answered with AI, saved in the Gmail thread.
// =========================================================================
const rp = { wired: false, rows: [], current: null, focusApp: null };
const RP_CAT_COLORS = { interview: '#dcfce7', question: '#e0f2fe', needs_resume: '#ede9fe', rejection: '#fee2e2', auto_reply: '#f1f5f9', other: '#f1f5f9' };

function openReplies(candId, focusAppId) {
    const $ = id => document.getElementById(id);
    rpWire();
    $('rp-consultant').innerHTML = '<option value="">All my consultants</option>' + (state.consultants || [])
        .map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    $('rp-consultant').value = candId ? String(candId) : '';
    rp.focusApp = focusAppId || null;
    $('rp-open').checked = !focusAppId;
    $('rp-msg').innerHTML = '';
    $('rp-detail').innerHTML = '<div style="color:#64748b; font-size:0.9rem;">Pick a reply on the left.</div>';
    $('modal-replies').style.display = 'flex';
    rpLoad();
}

function rpWire() {
    if (rp.wired) return;
    rp.wired = true;
    const $ = id => document.getElementById(id);
    $('btn-close-replies').addEventListener('click', () => { $('modal-replies').style.display = 'none'; if (document.getElementById('modal-tracker').style.display === 'flex') trkLoad(); });
    ['rp-consultant', 'rp-open'].forEach(id => $(id).addEventListener('change', rpLoad));
    $('rp-check').addEventListener('click', rpCheck);
    $('rp-mode').addEventListener('change', async () => {
        const res = await fetch('/api/me/auto-reply-mode', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode: $('rp-mode').value }) });
        const d = await res.json().catch(() => ({}));
        showToast(res.ok ? 'Auto-reply setting saved' : (d.error || 'Could not save'), res.ok ? 'success' : 'error');
    });
    $('rp-list').addEventListener('click', (e) => { const it = e.target.closest('.rp-item'); if (it) rpOpen(parseInt(it.getAttribute('data-id'))); });
}

function rpMsg(text, kind) {
    const colors = { ok: '#059669', warn: '#b45309', err: '#b91c1c' };
    document.getElementById('rp-msg').innerHTML = text ? `<span style="color:${colors[kind] || '#475569'};">${escapeHtml(text)}</span>` : '';
}

async function rpLoad() {
    const $ = id => document.getElementById(id);
    const p = new URLSearchParams();
    if ($('rp-consultant').value) p.set('candidate_id', $('rp-consultant').value);
    if ($('rp-open').checked) p.set('open', '1');
    const res = await fetch('/api/replies?' + p.toString());
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { rpMsg(d.error || 'Could not load replies.', 'err'); return; }
    rp.rows = d.replies || [];
    if (!$('rp-mode').options.length) {   // filled once: a list refresh never overwrites the choice being saved
        $('rp-mode').innerHTML = Object.entries(d.modes || {}).map(([k, v]) => `<option value="${k}" ${k === d.mode ? 'selected' : ''}>${escapeHtml(v)}</option>`).join('');
    }
    const rows = rp.focusApp ? [...rp.rows.filter(r => r.application_id === rp.focusApp), ...rp.rows.filter(r => r.application_id !== rp.focusApp)] : rp.rows;
    $('rp-list').innerHTML = rows.length ? rows.map(r => `
        <div class="rp-item" data-id="${r.id}" style="padding:10px 12px; border-bottom:1px solid #f1f5f9; cursor:pointer; ${r.handled ? 'opacity:0.65;' : ''}">
            <div style="display:flex; justify-content:space-between; gap:6px;"><b style="font-size:0.85rem; color:#0f172a;">${escapeHtml(r.from_name || r.from_email)}</b>
                <span style="font-size:0.72rem; color:#64748b; white-space:nowrap;">${escapeHtml((r.received_at || '').slice(0, 16))}</span></div>
            <div style="font-size:0.8rem; color:#334155; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${escapeHtml(r.subject || '')}</div>
            <div style="font-size:0.75rem; color:#64748b;">${escapeHtml(r.candidate_name || '')} · ${escapeHtml(r.job_title || '')}</div>
            <span style="display:inline-block; margin-top:4px; font-size:0.7rem; font-weight:700; padding:1px 8px; border-radius:999px; background:${RP_CAT_COLORS[r.category] || '#f1f5f9'}; color:#0f172a;">${escapeHtml(r.category_label)}</span>
            ${r.sent_at ? '<span style="font-size:0.7rem; color:#059669; font-weight:700; margin-left:4px;">✓ answered automatically</span>' : r.gmail_draft_at ? '<span style="font-size:0.7rem; color:#059669; font-weight:700; margin-left:4px;">✓ draft in Gmail</span>' : r.has_draft ? '<span style="font-size:0.7rem; color:#2563eb; font-weight:700; margin-left:4px;">✎ reply ready</span>' : ''}
        </div>`).join('') : '<div style="padding:16px; color:#64748b; font-size:0.85rem;">No replies here yet. Click "Check Gmail now" - or turn off "Only not handled".</div>';
    if (rp.focusApp) {   // from a tracker badge: open that application's real reply (not an out-of-office)
        const mine = rows.filter(r => r.application_id === rp.focusApp);
        const pick = mine.find(r => r.category !== 'auto_reply' && !r.handled) || mine.find(r => r.category !== 'auto_reply') || mine[0];
        rp.focusApp = null;
        if (pick) rpOpen(pick.id);
    }
}

async function rpOpen(id) {
    const res = await fetch(`/api/replies/${id}`);
    const r = await res.json().catch(() => ({}));
    if (!res.ok) { rpMsg(r.error || 'Could not open the reply.', 'err'); return; }
    rp.current = r;
    rp.undo = [];
    document.querySelectorAll('.rp-item').forEach(el => { el.style.background = parseInt(el.getAttribute('data-id')) === id ? '#eff6ff' : ''; });
    const L = 'font-size:0.72rem; font-weight:700; color:#64748b; text-transform:uppercase; letter-spacing:0.04em; margin:12px 0 4px;';
    const chip = (t) => `<button type="button" class="btn-prompt-chip rp-chip">${escapeHtml(t)}</button>`;
    document.getElementById('rp-detail').innerHTML = `
        <div style="display:flex; justify-content:space-between; gap:10px; align-items:flex-start;">
            <div><div style="font-weight:800; color:#0f172a;">${escapeHtml(r.subject || '')}</div>
                <div style="font-size:0.8rem; color:#475569;">From ${escapeHtml(r.from_name ? `${r.from_name} <${r.from_email}>` : r.from_email)} · ${escapeHtml(r.received_at || '')}</div>
                <div style="font-size:0.8rem; color:#475569;">${escapeHtml(r.candidate_name || '')} → ${escapeHtml(r.job_title || '')} (${escapeHtml(r.job_company || '')})</div></div>
            <span style="font-size:0.75rem; font-weight:700; padding:2px 10px; border-radius:999px; background:${RP_CAT_COLORS[r.category] || '#f1f5f9'}; white-space:nowrap;" title="${escapeHtml(r.category_reason || '')}">${escapeHtml(r.category_label)}</span>
        </div>
        ${r.category_reason ? `<div style="font-size:0.78rem; color:#64748b; margin-top:4px;">AI: ${escapeHtml(r.category_reason)}</div>` : ''}
        <div style="${L}">Recruiter wrote</div>
        <div id="rp-body" style="white-space:pre-wrap; font-size:0.85rem; color:#0f172a; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px; max-height:200px; overflow-y:auto;">${escapeHtml(r.body || '(no text)')}</div>
        <div style="${L}">Your reply ${r.gmail_draft_at ? '<span style="color:#059669; text-transform:none;">- saved as a Gmail draft ' + escapeHtml(String(r.gmail_draft_at).slice(0, 16)) + '</span>' : ''}${r.sent_at ? '<span style="color:#059669; text-transform:none;"> - sent automatically ' + escapeHtml(String(r.sent_at).slice(0, 16)) + '</span>' : ''}</div>
        ${r.draft_note ? `<div id="rp-note" style="font-size:0.78rem; color:#92400e; background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:6px 8px; margin-bottom:6px;">${escapeHtml(r.draft_note)}</div>` : '<div id="rp-note"></div>'}
        <textarea id="rp-draft" rows="8" class="form-control" style="width:100%; font-size:0.85rem; line-height:1.5;" placeholder="Write the reply, or ask the AI below.">${escapeHtml(r.draft_body || '')}</textarea>
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">${chip('Write a reply')}${chip('Shorter')}${chip('Share availability for a call')}${chip('Say the resume is attached')}${chip('Politely ask for the rate and client name')}</div>
        <div style="display:flex; gap:8px; margin-top:8px;">
            <input id="rp-instr" class="form-control" placeholder="Tell the AI what to say, e.g. 'He can talk Tuesday 2-5 PM IST'" style="flex:1;">
            <button type="button" class="btn btn-primary btn-sm" id="rp-ai">Ask AI</button>
            <button type="button" class="btn btn-secondary btn-sm" id="rp-undo" disabled>↶ Undo</button>
        </div>
        <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-top:12px; padding-top:10px; border-top:1px solid #f1f5f9;">
            <label style="display:flex; align-items:center; gap:6px; font-size:0.82rem; margin:0; text-transform:none; font-weight:500;"><input type="checkbox" id="rp-attach" ${r.category === 'needs_resume' ? 'checked' : ''}> Attach resume</label>
            <span style="flex:1"></span>
            <button type="button" class="btn btn-secondary btn-sm" id="rp-handled">${r.handled ? 'Mark not handled' : 'Mark handled'}</button>
            <button type="button" class="btn btn-primary btn-sm" id="rp-save">🚀 Save reply to Gmail (same thread)</button>
        </div>
        <div style="font-size:0.75rem; color:#94a3b8; margin-top:6px;">The reply is saved in the consultant's Gmail Drafts as "Re: ..." in this recruiter's thread - open Gmail and press Send.</div>`;
    const $ = id => document.getElementById(id);
    document.querySelectorAll('.rp-chip').forEach(b => b.addEventListener('click', () => rpAi(b.textContent)));
    $('rp-ai').addEventListener('click', () => rpAi($('rp-instr').value.trim()));
    $('rp-instr').addEventListener('keydown', (e) => { if (e.key === 'Enter') rpAi($('rp-instr').value.trim()); });
    $('rp-undo').addEventListener('click', () => { const prev = rp.undo.pop(); if (prev !== undefined) $('rp-draft').value = prev; $('rp-undo').disabled = !rp.undo.length; });
    $('rp-save').addEventListener('click', rpSave);
    $('rp-handled').addEventListener('click', async () => {
        await fetch(`/api/replies/${r.id}/handled`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ handled: !r.handled }) });
        rpLoad(); rpOpen(r.id);
    });
}

async function rpAi(instruction) {
    if (!rp.current || !instruction) return;
    const $ = id => document.getElementById(id);
    const btn = $('rp-ai');
    btn.disabled = true; btn.textContent = 'Thinking...';
    try {
        const current = $('rp-draft').value;
        const res = await fetch(`/api/replies/${rp.current.id}/ai`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instruction, current_body: current }) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { rpMsg(d.error || 'The AI could not answer.', 'err'); return; }
        const notes = [d.note, ...(d.needs_info || []).map(q => '❓ ' + q)].filter(Boolean);
        $('rp-note').innerHTML = notes.length ? `<div style="font-size:0.78rem; color:${d.applied ? '#334155' : '#92400e'}; background:${d.applied ? '#f1f5f9' : '#fffbeb'}; border:1px solid ${d.applied ? '#e2e8f0' : '#fde68a'}; border-radius:8px; padding:6px 8px; margin-bottom:6px;">${notes.map(escapeHtml).join('<br>')}</div>` : '';
        if (d.applied) {
            rp.undo.push(current);
            $('rp-undo').disabled = false;
            $('rp-draft').value = d.body;
            $('rp-instr').value = '';
        }
    } finally { btn.disabled = false; btn.textContent = 'Ask AI'; }
}

async function rpSave() {
    const $ = id => document.getElementById(id);
    const btn = $('rp-save');
    btn.disabled = true;
    try {
        const res = await fetch(`/api/replies/${rp.current.id}/save-draft`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ body: $('rp-draft').value, attach_resume: $('rp-attach').checked }) });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(d.error || 'Could not save the draft.', 'error', 7000); return; }
        showToast(`Reply saved in Gmail Drafts (same thread)${d.attached ? ' with ' + d.attached : ''} - open Gmail to send it.`, 'success', 7000);
        rpLoad();
        rpOpen(rp.current.id);
    } finally { btn.disabled = false; }
}

async function rpCheck() {
    const $ = id => document.getElementById(id);
    const btn = $('rp-check');
    btn.disabled = true;
    rpMsg('Reading recruiter replies from Gmail (nothing is marked read or changed)...', '');
    try {
        const cid = $('rp-consultant').value;
        const res = cid
            ? await fetch('/api/applications/check-replies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidate_id: parseInt(cid) }) })
            : await fetch('/api/replies/check-all', { method: 'POST' });
        const d = await res.json().catch(() => ({}));
        if (!res.ok) { rpMsg(d.error || 'Could not check Gmail.', 'err'); return; }
        rpMsg(`${d.new_replies || 0} new repl${d.new_replies === 1 ? 'y' : 'ies'}.` + ((d.errors || []).length ? ' Problems: ' + d.errors.join('; ') : ''), d.new_replies ? 'ok' : '');
        rpLoad();
    } finally { btn.disabled = false; }
}

// =========================================================================
// Requirements from Gmail (inbox_requirements.py): requirement email -> tailored resume -> draft reply.
// =========================================================================
const rq = { wired: false, poll: null };
const RQ_COLORS = { drafted: '#dcfce7', drafted_input: '#fef9c3', low_match: '#fef3c7', not_requirement: '#f1f5f9', filtered: '#f1f5f9', error: '#fee2e2', checking: '#e0f2fe' };

function openRequirements(candId) {
    const $ = id => document.getElementById(id);
    if (!rq.wired) {
        rq.wired = true;
        $('btn-close-reqs').addEventListener('click', () => { $('modal-reqs').style.display = 'none'; clearTimeout(rq.poll); });
        ['rq-consultant', 'rq-status'].forEach(id => $(id).addEventListener('change', rqLoad));
        $('rq-scan').addEventListener('click', rqScan);
    }
    $('rq-consultant').innerHTML = '<option value="">All my consultants</option>' + (state.consultants || [])
        .map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('');
    $('rq-consultant').value = candId ? String(candId) : '';
    $('rq-msg').textContent = '';
    $('modal-reqs').style.display = 'flex';
    rqLoad();
}

async function rqLoad() {
    const $ = id => document.getElementById(id);
    const cid = $('rq-consultant').value;
    const res = await fetch('/api/requirements' + (cid ? `?candidate_id=${cid}` : ''));
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { $('rq-msg').textContent = d.error || 'Could not load.'; return; }
    $('rq-min').textContent = d.min_match;
    const want = $('rq-status').value;
    const rows = (d.requirements || []).filter(r => !want || r.status === want || (want === 'drafted' && r.status === 'drafted_input') || (want === 'error' && r.status === 'checking'));
    $('rq-body').innerHTML = rows.length ? rows.map(r => `
        <tr data-id="${r.id}">
            <td style="white-space:nowrap;">${escapeHtml((r.received_at || '').slice(0, 16))}</td>
            <td>${escapeHtml(r.candidate_name || '')}</td>
            <td>${escapeHtml(r.from_name || '')}<div style="color:#64748b;">${escapeHtml(r.from_email || '')}</div></td>
            <td style="min-width:200px;">${escapeHtml(r.subject || '')}${r.job_title ? `<div style="color:#64748b;">${escapeHtml(r.job_title)}${r.job_company ? ' · ' + escapeHtml(r.job_company) : ''}</div>` : ''}</td>
            <td><span style="display:inline-block; padding:2px 8px; border-radius:999px; font-weight:700; background:${RQ_COLORS[r.status] || '#f1f5f9'}; white-space:nowrap;">${escapeHtml(r.status_label)}</span></td>
            <td>${r.match_pct != null ? r.match_pct + '%' : '-'}</td>
            <td style="max-width:170px; word-break:break-all;">${r.resume_version_id ? `<a href="/api/optimized-resumes/${r.resume_version_id}/download">${escapeHtml(r.resume_filename || 'tailored resume')}</a>` : (r.status === 'drafted' ? '<span style="color:#94a3b8;">original</span>' : '-')}</td>
            <td style="min-width:220px; color:#475569;">${escapeHtml(r.reason || '')}</td>
        </tr>`).join('') : `<tr><td colspan="8" style="text-align:center; color:#64748b; padding:18px;">${want ? 'Nothing with this result yet - choose "All".' : 'No requirement emails checked yet - click "Scan Gmail now".'}</td></tr>`;
    clearTimeout(rq.poll);
    if (d.scanning && $('modal-reqs').style.display === 'flex') {
        $('rq-msg').textContent = 'Scanning Gmail and tailoring resumes... (about a minute per requirement)';
        rq.poll = setTimeout(rqLoad, 8000);
    } else if ($('rq-msg').textContent.startsWith('Scanning')) {
        $('rq-msg').textContent = 'Scan finished.';
    }
}

async function rqScan() {
    const $ = id => document.getElementById(id);
    const cid = $('rq-consultant').value;
    const res = await fetch('/api/requirements/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cid ? { candidate_id: parseInt(cid) } : {}) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { $('rq-msg').textContent = d.error || 'Could not start the scan.'; return; }
    $('rq-msg').textContent = d.started ? 'Scanning Gmail and tailoring resumes... (about a minute per requirement)' : (d.message || '');
    if (d.started) setTimeout(rqLoad, 3000);
}

// =========================================================================
// USA / India teams (regions.py): admins switch the team they look at; recruiters are locked to theirs.
// The Jobs market follows the team (US jobs for USA, India jobs for India).
// =========================================================================
const TEAM_JOB_COUNTRY = { USA: 'United States', India: 'India' };

async function setRecruiterRegion(id, region) {
    const res = await fetch(`/api/admin/recruiters/${id}/region`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ region }) });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) { showToast(d.error || 'Could not change the team.', 'error'); return; }
    showToast(`Moved to the ${d.region} team`, 'success');
}

function applyTeamLock() {
    const country = TEAM_JOB_COUNTRY[window.VIEW_REGION];
    const sel = document.getElementById('filter-country');
    if (!sel || !country) return;
    sel.value = country;
    sel.disabled = true;
    sel.title = `Jobs follow your team (${window.VIEW_REGION})`;
    if (typeof syncJobsMarketUi === 'function') syncJobsMarketUi();
}

document.addEventListener('DOMContentLoaded', () => {
    const sw = document.getElementById('view-region-switch');
    if (sw) sw.addEventListener('change', async () => {
        await fetch('/api/view-region', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ region: sw.value }) });
        window.location.reload();
    });
    applyTeamLock();
});
