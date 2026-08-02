/*
 * app.js — all four views.
 *
 * Every action is a fetch() whose result is written into the DOM. Nothing on
 * this page navigates, submits a form, or reloads.
 *
 * The four tabs share the same shape — load a list, render a table, read a
 * form, show the server's error — so those four things are written once here
 * and reused by every tab.
 */

/* =========================== shared helpers =========================== */

/** Escape anything from the database before it reaches innerHTML. */
function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value)
   .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
   .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function euro(n) { return '€' + Number(n).toFixed(2); }

/**
 * Render rows into a <tbody>.
 *
 *   cells   array of functions, one per column, each returning cell HTML
 *   opts.rowClass   optional function returning a CSS class for the <tr>
 *   opts.actions    { dataAttribute: handler(id) } wired to buttons in a cell
 *   opts.empty      text shown when there are no rows
 *   opts.count      optional element id to write "N things" into
 */
function renderTable(tbodyId, rows, cells, opts) {
  opts = opts || {};
  const body = document.getElementById(tbodyId);

  if (opts.count) {
    document.getElementById(opts.count).textContent =
      `${rows.length} ${opts.noun}${rows.length === 1 ? '' : 's'}`;
  }

  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="${cells.length}" class="muted">` +
      `${esc(opts.empty || 'Nothing to show.')}</td></tr>`;
    return;
  }

  body.innerHTML = rows.map(row =>
    `<tr class="${opts.rowClass ? esc(opts.rowClass(row)) : ''}">` +
    cells.map(cell => `<td>${cell(row)}</td>`).join('') + '</tr>').join('');

  Object.entries(opts.actions || {}).forEach(([attribute, handler]) => {
    body.querySelectorAll(`[data-${attribute}]`).forEach(button => {
      button.addEventListener('click',
        () => handler(Number(button.dataset[attribute])));
    });
  });
}

/** Collect trimmed values from a { key: elementId } map. */
function readForm(fields) {
  const values = {};
  Object.entries(fields).forEach(([key, id]) => {
    values[key] = document.getElementById(id).value.trim();
  });
  return values;
}

/** Blank the given inputs, leaving <select> elements at their first option. */
function clearFields(ids) {
  ids.forEach(id => {
    const element = document.getElementById(id);
    if (element.tagName === 'SELECT') element.selectedIndex = 0;
    else element.value = '';
  });
}

function showMessage(boxId, text, kind, details) {
  const box = document.getElementById(boxId);
  let html = esc(text);
  if (details && Object.keys(details).length) {
    html += '<span class="detail">' + Object.entries(details)
     .map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(' · ') + '</span>';
  }
  box.innerHTML = html;
  box.className = 'msg ' + kind;
}

function clearMessage(boxId) {
  const box = document.getElementById(boxId);
  box.className = 'msg';
  box.textContent = '';
}

/** Show the server's own error text and details — the UI never restates rules. */
function showError(boxId, error) {
  showMessage(boxId, error.message, 'error', error.details);
}

/** Run an API call, routing any failure to the tab's message box. */
async function guard(boxId, work) {
  try {
    return await work();
  } catch (error) {
    showError(boxId, error);
    return undefined;
  }
}

/** Put a tab's form back into "add" mode. */
function resetForm(state, ids, titleId, title, saveId, saveText, cancelId) {
  clearFields(ids);
  state.editingId = null;
  document.getElementById(titleId).textContent = title;
  document.getElementById(saveId).textContent = saveText;
  document.getElementById(cancelId).style.display = 'none';
}

function beginEdit(state, id, titleId, title, saveId, saveText, cancelId, boxId) {
  state.editingId = id;
  document.getElementById(titleId).textContent = title;
  document.getElementById(saveId).textContent = saveText;
  document.getElementById(cancelId).style.display = 'inline-block';
  clearMessage(boxId);
  window.scrollTo(0, 0);
}

function on(id, event, handler) {
  document.getElementById(id).addEventListener(event, handler);
}

/* ================================ tabs ================================ */

document.querySelectorAll('nav button').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('section').forEach(s => s.classList.remove('active'));
    button.classList.add('active');
    document.getElementById('tab-' + button.dataset.tab).classList.add('active');
  });
});

/* =============================== plans ================================ */

const PLAN_FIELDS = { name: 'plan-name', price_eur: 'plan-price',
                      duration_days: 'plan-duration',
                      description: 'plan-description' };

const Plans = {
  editingId: null,
  rows: [],

  async load() {
    const plans = await guard('plans-msg', () => API.get('/api/plans'));
    if (!plans) return;
    this.rows = plans;
    renderTable('plans-body', plans, [
      p => esc(p.name),
      p => euro(p.price_eur),
      p => `${esc(p.duration_days)} days`,
      p => esc(p.description),
      p => esc(p.member_count),
      p => `<button data-edit="${p.id}">Edit</button> ` +
           `<button data-delete="${p.id}">Delete</button>`,
    ], {
      empty: 'No plans yet. Add one above.',
      actions: {
        edit: id => this.startEdit(this.rows.find(p => p.id === id)),
        delete: id => this.remove(id),
      },
    });
  },

  clearForm() {
    resetForm(this, Object.values(PLAN_FIELDS), 'plan-form-title', 'Add a plan',
              'plan-save', 'Save plan', 'plan-cancel');
  },

  startEdit(plan) {
    document.getElementById('plan-name').value = plan.name;
    document.getElementById('plan-price').value = plan.price_eur;
    document.getElementById('plan-duration').value = plan.duration_days;
    document.getElementById('plan-description').value = plan.description || '';
    beginEdit(this, plan.id, 'plan-form-title', 'Editing: ' + plan.name,
              'plan-save', 'Update plan', 'plan-cancel', 'plans-msg');
  },

  async save() {
    clearMessage('plans-msg');
    const payload = readForm(PLAN_FIELDS);
    // 409 duplicate name, 400 validation — the server's reason is shown as-is.
    const plan = await guard('plans-msg', () => this.editingId === null
      ? API.post('/api/plans', payload)
      : API.put(`/api/plans/${this.editingId}`, payload));
    if (!plan) return;
    showMessage('plans-msg',
      `${this.editingId === null ? 'Created' : 'Updated'} "${plan.name}".`,
      'success');
    this.clearForm();
    await this.load();
  },

  async remove(planId) {
    clearMessage('plans-msg');
    // 409 when members are on the plan — details carry the member count.
    const done = await guard('plans-msg', () => API.del(`/api/plans/${planId}`));
    if (!done) return;
    showMessage('plans-msg', 'Plan deleted.', 'success');
    if (this.editingId === planId) this.clearForm();
    await this.load();
  },
};

on('plan-save', 'click', () => Plans.save());
on('plan-cancel', 'click', () => { Plans.clearForm(); clearMessage('plans-msg'); });

Plans.load();
