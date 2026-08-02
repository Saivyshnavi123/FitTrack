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
    Members.loadPlans();
  },

  async remove(planId) {
    clearMessage('plans-msg');
    // 409 when members are on the plan — details carry the member count.
    const done = await guard('plans-msg', () => API.del(`/api/plans/${planId}`));
    if (!done) return;
    showMessage('plans-msg', 'Plan deleted.', 'success');
    if (this.editingId === planId) this.clearForm();
    await this.load();
    Members.loadPlans();
  },
};

on('plan-save', 'click', () => Plans.save());
on('plan-cancel', 'click', () => { Plans.clearForm(); clearMessage('plans-msg'); });

/* ============================== members =============================== */

const MEMBER_FIELDS = { first_name: 'member-first', last_name: 'member-last',
                        email: 'member-email', phone: 'member-phone',
                        plan_id: 'member-plan', start_date: 'member-start' };

const Members = {
  editingId: null,
  rows: [],

  /** Plan options come from the API, so a plan added on the Plans tab is
      selectable here without a code change. */
  async loadPlans() {
    const plans = await guard('members-msg', () => API.get('/api/plans'));
    if (!plans) return;
    const options = plans
     .map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
    document.getElementById('member-plan').innerHTML = options;
    document.getElementById('member-filter-plan').innerHTML =
      '<option value="">All plans</option>' + options;
  },

  async load() {
    const planId = document.getElementById('member-filter-plan').value;
    const members = await guard('members-msg', () =>
      API.get('/api/members' + (planId ? `?plan_id=${planId}` : '')));
    if (!members) return;
    this.rows = members;

    renderTable('members-body', members, [
      m => `${esc(m.first_name)} ${esc(m.last_name)}`,
      m => esc(m.email),
      m => esc(m.phone),
      m => esc(m.plan_name),
      m => esc(m.start_date),
      m => esc(m.expiry_date) +
           (m.is_expired ? '' : ` <span class="muted">(${m.days_remaining}d)</span>`),
      m => esc(m.status),
      m => `<button data-renew="${m.id}">Renew</button> ` +
           `<button data-edit="${m.id}">Edit</button> ` +
           `<button data-delete="${m.id}">Delete</button>`,
    ], {
      empty: 'No members yet.',
      count: 'members-count', noun: 'member',
      rowClass: m => m.status,           // "expired" / "inactive" flags the row
      actions: {
        edit: id => this.startEdit(this.rows.find(m => m.id === id)),
        delete: id => this.remove(id),
        renew: id => this.renew(id),
      },
    });
  },

  clearForm() {
    resetForm(this, Object.values(MEMBER_FIELDS).concat('member-active'),
              'member-form-title', 'Add a member',
              'member-save', 'Save member', 'member-cancel');
  },

  startEdit(member) {
    document.getElementById('member-first').value = member.first_name;
    document.getElementById('member-last').value = member.last_name;
    document.getElementById('member-email').value = member.email;
    document.getElementById('member-phone').value = member.phone || '';
    document.getElementById('member-plan').value = member.plan_id;
    document.getElementById('member-start').value = member.start_date;
    document.getElementById('member-active').value =
      member.is_active ? 'true' : 'false';
    beginEdit(this, member.id, 'member-form-title',
              `Editing: ${member.first_name} ${member.last_name}`,
              'member-save', 'Update member', 'member-cancel', 'members-msg');
  },

  async save() {
    clearMessage('members-msg');
    const payload = readForm(MEMBER_FIELDS);
    payload.is_active = document.getElementById('member-active').value === 'true';
    // 409 duplicate email, 422 unknown plan, 400 validation.
    const member = await guard('members-msg', () => this.editingId === null
      ? API.post('/api/members', payload)
      : API.put(`/api/members/${this.editingId}`, payload));
    if (!member) return;
    showMessage('members-msg',
      `${this.editingId === null ? 'Added' : 'Updated'} ` +
      `${member.first_name} ${member.last_name}. Expires ${member.expiry_date}.`,
      'success');
    this.clearForm();
    await this.load();
  },

  async renew(memberId) {
    clearMessage('members-msg');
    const member = await guard('members-msg',
      () => API.post(`/api/members/${memberId}/renew`));
    if (!member) return;
    showMessage('members-msg',
      `${member.first_name} ${member.last_name} renewed from ` +
      `${member.renewed_from} — now expires ${member.expiry_date}.`, 'success');
    await this.load();
  },

  async remove(memberId) {
    clearMessage('members-msg');
    const done = await guard('members-msg',
      () => API.del(`/api/members/${memberId}`));
    if (!done) return;
    showMessage('members-msg',
      'Member deleted. Their bookings were removed with them.', 'success');
    if (this.editingId === memberId) this.clearForm();
    await this.load();
  },

  async init() { await this.loadPlans(); await this.load(); },
};

on('member-save', 'click', () => Members.save());
on('member-cancel', 'click', () => { Members.clearForm(); clearMessage('members-msg'); });
on('member-filter-plan', 'change', () => Members.load());
on('member-reset', 'click', () => {
  document.getElementById('member-filter-plan').value = '';
  Members.load();
});

/* ============================== classes =============================== */

const CLASS_FIELDS = { name: 'class-name', instructor: 'class-instructor',
                       class_date: 'class-date', start_time: 'class-start',
                       end_time: 'class-end', capacity: 'class-capacity' };

const Classes = {
  editingId: null,
  rows: [],

  query() {
    const params = new URLSearchParams();
    const date = document.getElementById('class-filter-date').value;
    const instructor = document.getElementById('class-filter-instructor').value.trim();
    if (date) params.set('date', date);
    if (instructor) params.set('instructor', instructor);
    if (document.getElementById('class-filter-upcoming').value) {
      params.set('upcoming', 'true');
    }
    params.set('include_cancelled', 'true');
    return '/api/classes?' + params.toString();
  },

  async load() {
    const classes = await guard('classes-msg', () => API.get(this.query()));
    if (!classes) return;
    this.rows = classes;

    renderTable('classes-body', classes, [
      c => esc(c.class_date),
      c => `${esc(c.start_time)}–${esc(c.end_time)}`,
      c => esc(c.name),
      c => esc(c.instructor),
      c => esc(c.room),
      c => c.is_cancelled ? '<span class="muted">cancelled</span>'
                          : `${c.spaces_left} of ${c.capacity}` +
                            (c.is_full ? ' (full)' : ''),
      c => `<button data-edit="${c.id}">Edit</button> ` +
           (c.is_cancelled ? '' : `<button data-cancel="${c.id}">Cancel</button> `) +
           `<button data-delete="${c.id}">Delete</button>`,
    ], {
      empty: 'No classes match those filters.',
      count: 'classes-count', noun: 'class',
      rowClass: c => c.is_cancelled ? 'cancelled' : (c.is_full ? 'full' : ''),
      actions: {
        edit: id => this.startEdit(this.rows.find(c => c.id === id)),
        cancel: id => this.cancelClass(id),
        delete: id => this.remove(id),
      },
    });
  },

  readForm() {
    return Object.assign(readForm(CLASS_FIELDS), {
      room: document.getElementById('class-room').value,
    });
  },

  clearForm() {
    resetForm(this, Object.values(CLASS_FIELDS), 'class-form-title',
              'Schedule a class', 'class-save', 'Save class', 'class-cancel-edit');
  },

  startEdit(item) {
    document.getElementById('class-name').value = item.name;
    document.getElementById('class-instructor').value = item.instructor;
    document.getElementById('class-date').value = item.class_date;
    document.getElementById('class-start').value = item.start_time;
    document.getElementById('class-end').value = item.end_time;
    document.getElementById('class-capacity').value = item.capacity;
    document.getElementById('class-room').value = item.room || 'Churchtown';
    this.clearAdvisory();
    beginEdit(this, item.id, 'class-form-title', 'Editing: ' + item.name,
              'class-save', 'Update class', 'class-cancel-edit', 'classes-msg');
  },

  clearAdvisory() {
    const box = document.getElementById('classes-advisory');
    box.className = 'msg';
    box.innerHTML = '';
  },

  /**
   * Render the holiday advisory prominently.
   *
   * This is the Good Friday case. The class WAS created — the gym trades that
   * day — but the manager is told which holiday it falls on and why it was
   * allowed. Showing it only in the JSON would hide the most interesting
   * decision the system makes.
   */
  showAdvisory(item) {
    const box = document.getElementById('classes-advisory');
    if (item.warning) {
      showMessage('classes-advisory',
        'Scheduled without a closure check. ' + item.warning, 'warning');
      return;
    }
    if (!item.advisory) { this.clearAdvisory(); return; }
    const check = item.holiday_check || {};
    box.className = 'msg advisory';
    box.innerHTML =
      `<b>Advisory — ${esc(check.holiday_name || 'public holiday')} on ` +
      `${esc(item.class_date)}</b><br>${esc(item.advisory)}` +
      `<span class="detail">Holiday types returned by Nager.Date: ` +
      `${esc((check.types || []).join(', '))}` +
      (check.local_name ? ` · Irish name: ${esc(check.local_name)}` : '') +
      `</span>`;
  },

  async save() {
    clearMessage('classes-msg');
    this.clearAdvisory();
    const editing = this.editingId;
    // 409 closure day or instructor clash, 422 bad times, 400 validation.
    const item = await guard('classes-msg', () => editing === null
      ? API.post('/api/classes', this.readForm())
      : API.put(`/api/classes/${editing}`, this.readForm()));
    if (!item) return;

    showMessage('classes-msg',
      editing === null
        ? `Scheduled "${item.name}" on ${item.class_date}.`
        : `Updated "${item.name}".`,
      'success');
    this.showAdvisory(item);            // Good Friday lands here
    this.clearForm();
    await this.load();
  },

  async cancelClass(classId) {
    clearMessage('classes-msg');
    const item = await guard('classes-msg',
      () => API.request('PATCH', `/api/classes/${classId}/cancel`));
    if (!item) return;
    showMessage('classes-msg',
      `"${item.name}" cancelled. Its place count no longer applies.`, 'success');
    await this.load();
  },

  async remove(classId) {
    clearMessage('classes-msg');
    const done = await guard('classes-msg', () => API.del(`/api/classes/${classId}`));
    if (!done) return;
    showMessage('classes-msg',
      'Class deleted, along with its bookings.', 'success');
    if (this.editingId === classId) this.clearForm();
    await this.load();
  },

  async init() { await this.load(); },
};

on('class-save', 'click', () => Classes.save());
on('class-cancel-edit', 'click', () => {
  Classes.clearForm(); clearMessage('classes-msg'); Classes.clearAdvisory();
});
on('class-filter-date', 'change', () => Classes.load());
on('class-filter-instructor', 'input', () => Classes.load());
on('class-filter-upcoming', 'change', () => Classes.load());
on('class-reset', 'click', () => {
  document.getElementById('class-filter-date').value = '';
  document.getElementById('class-filter-instructor').value = '';
  document.getElementById('class-filter-upcoming').value = 'true';
  Classes.load();
});

/* ============================== bookings ============================== */

const Bookings = {
  async loadOptions() {
    const [members, classes] = await Promise.all([
      API.get('/api/members'),
      API.get('/api/classes?include_cancelled=true'),
    ]);

    const memberOptions = members.map(m =>
      `<option value="${m.id}">${esc(m.last_name)}, ${esc(m.first_name)}` +
      ` — ${esc(m.plan_name)} (${esc(m.status)})</option>`).join('');
    document.getElementById('booking-member').innerHTML =
      '<option value="">Choose a member…</option>' + memberOptions;
    document.getElementById('booking-filter-member').innerHTML =
      '<option value="">All members</option>' + memberOptions;

    document.getElementById('booking-class').innerHTML =
      '<option value="">Choose a class…</option>' + classes.map(c =>
        `<option value="${c.id}">${esc(c.class_date)} ${esc(c.start_time)} ` +
        `${esc(c.name)} — ${esc(c.room)} (${c.spaces_left}/${c.capacity} free` +
        `${c.is_cancelled ? ', CANCELLED' : ''})</option>`).join('');
  },

  query() {
    const params = new URLSearchParams();
    const member = document.getElementById('booking-filter-member').value;
    const status = document.getElementById('booking-filter-status').value;
    if (member) params.set('member_id', member);
    if (status) params.set('status', status);
    return '/api/bookings?' + params.toString();
  },

  async load() {
    const bookings = await guard('bookings-msg', () => API.get(this.query()));
    if (!bookings) return;

    renderTable('bookings-body', bookings, [
      b => `${esc(b.first_name)} ${esc(b.last_name)}`,
      b => esc(b.class_name),
      b => esc(b.class_date),
      b => `${esc(b.start_time)}–${esc(b.end_time)}`,
      b => esc(b.room),
      b => esc(b.status.replace('_', ' ')),
      b => b.status === 'cancelled' ? '' :
           `<button data-attended="${b.id}">Attended</button> ` +
           `<button data-noshow="${b.id}">No show</button> ` +
           `<button data-cancel="${b.id}">Cancel</button>`,
    ], {
      empty: 'No bookings match those filters.',
      count: 'bookings-count', noun: 'booking',
      rowClass: b => b.status === 'cancelled' ? 'cancelled-booking' : '',
      actions: {
        cancel: id => this.cancel(id),
        attended: id => this.setStatus(id, 'attended'),
        noshow: id => this.setStatus(id, 'no_show'),
      },
    });
  },

  async save() {
    clearMessage('bookings-msg');
    const member = document.getElementById('booking-member').value;
    const klass = document.getElementById('booking-class').value;
    if (!member || !klass) {
      showMessage('bookings-msg', 'Choose a member and a class first.', 'error');
      return;
    }
    // The server's own rule message, so the UI never restates the rules.
    const booking = await guard('bookings-msg', () =>
      API.post('/api/bookings', { member_id: member, class_id: klass }));
    if (!booking) return;
    showMessage('bookings-msg',
      `Booked ${booking.first_name} ${booking.last_name} into ` +
      `${booking.class_name} on ${booking.class_date}. ` +
      `${booking.spaces_left} place${booking.spaces_left === 1 ? '' : 's'} left.` +
      (booking.reactivated
        ? ' (Their earlier cancelled booking was reactivated.)' : ''),
      'success');
    await this.refresh();
  },

  /** Cancelling frees the place; the class list reloads so spaces update. */
  async cancel(bookingId) {
    clearMessage('bookings-msg');
    const booking = await guard('bookings-msg',
      () => API.del(`/api/bookings/${bookingId}`));
    if (!booking) return;
    showMessage('bookings-msg',
      `Cancelled. ${booking.class_name} now has ${booking.spaces_left} ` +
      `place${booking.spaces_left === 1 ? '' : 's'} free.`, 'success');
    await this.refresh();
  },

  async setStatus(bookingId, status) {
    clearMessage('bookings-msg');
    const done = await guard('bookings-msg', () =>
      API.request('PATCH', `/api/bookings/${bookingId}/status`, { status }));
    if (done) await this.load();
  },

  async refresh() {
    await this.loadOptions();
    await this.load();
    Classes.load();                 // spaces-left changed on the Classes tab
  },

  async init() {
    await guard('bookings-msg', async () => {
      await this.loadOptions();
      await this.load();
    });
  },
};

on('booking-save', 'click', () => Bookings.save());
on('booking-filter-member', 'change', () => Bookings.load());
on('booking-filter-status', 'change', () => Bookings.load());
on('booking-reset', 'click', () => {
  document.getElementById('booking-filter-member').value = '';
  document.getElementById('booking-filter-status').value = '';
  Bookings.load();
});

/* Members and classes may have changed on another tab. */
document.querySelector('nav button[data-tab="bookings"]')
 .addEventListener('click', () => Bookings.loadOptions().catch(() => {}));

Plans.load();
Members.init();
Classes.init();
Bookings.init();
