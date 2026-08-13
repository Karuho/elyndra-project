"use strict";

const state = {
  token: document.querySelector('meta[name="elyndra-token"]').content,
  bootstrap: null,
  chats: [],
  pinnedChats: [],
  activeChatId: null,
  activeChat: null,
  draftChat: true,
  contextChatId: null,
  pendingAttachments: [],
  sending: false,
  uploading: false,
  searchTimer: null,
  processingTimer: null,
  processingStarted: 0,
  inspectorActive: false,
  inspectorView: "overview",
  inspectorOverview: null,
  inspectorItems: [],
  inspectorTimer: null,
  alexandriaActive: false,
  personalActive: false,
  profileActive: false,
  controlActive: false,
  accountData: null,
  personalData: null,
  personalTimer: null,
  controlOverview: null,
  controlProjects: null,
  controlAudit: [],
  controlPhpVerifications: [],
  controlWebVerifications: [],
  controlPythonVerifications: [],
  controlJavaVerifications: [],
  controlKotlinVerifications: [],
  controlDotnetVerifications: [],
  controlNativeVerifications: [],
  controlRubyVerifications: [],
  controlGoVerifications: [],
  controlRustVerifications: [],
  controlSwiftVerifications: [],
  controlDartVerifications: [],
  controlSqlVerifications: [],
  controlActionRuns: [],
  controlChangeProposals: [],
  controlValidationCycles: [],
  controlDevelopmentSessions: [],
  controlEthics: null,
  controlPackages: [],
  alexandriaOverview: null,
  alexandriaLibraries: [],
  alexandriaSelectedId: null,
  alexandriaSearchTimer: null,
  libraryDialogMode: "create",
  libraryEditingId: null,
  diagnostics: new URLSearchParams(window.location.search).get("diagnostics") === "1",
};

const elements = {
  authScreen: document.getElementById("auth-screen"),
  appShell: document.getElementById("app-shell"),
  authTitle: document.getElementById("auth-title"),
  authCopy: document.getElementById("auth-copy"),
  authTabLogin: document.getElementById("auth-tab-login"),
  authTabRegister: document.getElementById("auth-tab-register"),
  loginForm: document.getElementById("login-form"),
  loginName: document.getElementById("login-name"),
  loginPassword: document.getElementById("login-password"),
  registerForm: document.getElementById("register-form"),
  registerUsername: document.getElementById("register-username"),
  registerEmail: document.getElementById("register-email"),
  registerPassword: document.getElementById("register-password"),
  registerPasswordConfirmation: document.getElementById("register-password-confirmation"),
  registerBirthDate: document.getElementById("register-birth-date"),
  registerPreferredName: document.getElementById("register-preferred-name"),
  registerDeveloperMode: document.getElementById("register-developer-mode"),
  registerTelemetry: document.getElementById("register-telemetry"),
  registerApprove: document.getElementById("register-approve"),
  sidebar: document.getElementById("sidebar"),
  openSidebar: document.getElementById("open-sidebar"),
  closeSidebar: document.getElementById("close-sidebar"),
  sidebarAccountButton: document.getElementById("sidebar-account-button"),
  sidebarAccountAvatar: document.getElementById("sidebar-account-avatar"),
  sidebarAccountName: document.getElementById("sidebar-account-name"),
  sidebarAccountMode: document.getElementById("sidebar-account-mode"),
  accountContextMenu: document.getElementById("account-context-menu"),
  newChat: document.getElementById("new-chat"),
  toggleChatSearch: document.getElementById("toggle-chat-search"),
  chatSearchBox: document.getElementById("chat-search-box"),
  connectionLocal: document.getElementById("connection-local"),
  connectionOnline: document.getElementById("connection-online"),
  pinnedSection: document.getElementById("pinned-section"),
  pinnedList: document.getElementById("pinned-list"),
  pinnedCount: document.getElementById("pinned-count"),
  historyFilter: document.getElementById("history-filter"),
  search: document.getElementById("chat-search"),
  chatList: document.getElementById("chat-list"),
  chatCount: document.getElementById("chat-count"),
  chatTitle: document.getElementById("chat-title"),
  chatSubtitle: document.getElementById("chat-subtitle"),
  renameChat: document.getElementById("rename-chat"),
  chatActions: document.getElementById("chat-actions"),
  contextMenu: document.getElementById("chat-context-menu"),
  conversation: document.getElementById("conversation"),
  welcome: document.getElementById("welcome"),
  welcomeTitle: document.getElementById("welcome-title"),
  composer: document.getElementById("composer"),
  input: document.getElementById("message-input"),
  send: document.getElementById("send-message"),
  attach: document.getElementById("attach-file"),
  fileInput: document.getElementById("file-input"),
  attachmentTray: document.getElementById("attachment-tray"),
  processing: document.getElementById("processing"),
  processingText: document.getElementById("processing-text"),
  processingTime: document.getElementById("processing-time"),
  openMemory: document.getElementById("open-memory"),
  memoryBadge: document.getElementById("memory-badge"),
  openAlexandria: document.getElementById("open-alexandria"),
  alexandriaBadge: document.getElementById("alexandria-badge"),
  openPersonal: document.getElementById("open-personal"),
  openProfile: document.getElementById("open-profile"),
  openControl: document.getElementById("open-control"),
  runtimeVersion: document.getElementById("runtime-version"),
  profile: document.getElementById("profile"),
  profileOverview: document.getElementById("profile-overview"),
  profileForm: document.getElementById("profile-form"),
  profileUsername: document.getElementById("profile-username"),
  profilePreferredName: document.getElementById("profile-preferred-name"),
  profilePronouns: document.getElementById("profile-pronouns"),
  profileSex: document.getElementById("profile-sex"),
  profileGenderIdentity: document.getElementById("profile-gender-identity"),
  profileSexualOrientation: document.getElementById("profile-sexual-orientation"),
  profileTimezone: document.getElementById("profile-timezone"),
  profileLanguage: document.getElementById("profile-language"),
  profileBirthdayGreeting: document.getElementById("profile-birthday-greeting"),
  profileDeveloperMode: document.getElementById("profile-developer-mode"),
  profileTelemetry: document.getElementById("profile-telemetry"),
  profileSecurity: document.getElementById("profile-security"),
  profileTelemetryPreview: document.getElementById("profile-telemetry-preview"),
  changeEmailForm: document.getElementById("change-email-form"),
  changeEmail: document.getElementById("change-email"),
  changeEmailPassword: document.getElementById("change-email-password"),
  changePasswordForm: document.getElementById("change-password-form"),
  currentPassword: document.getElementById("current-password"),
  newPassword: document.getElementById("new-password"),
  newPasswordConfirmation: document.getElementById("new-password-confirmation"),
  accountExportForm: document.getElementById("account-export-form"),
  accountExportPassword: document.getElementById("account-export-password"),
  accountExportPassphrase: document.getElementById("account-export-passphrase"),
  logoutButton: document.getElementById("logout-button"),
  openControl: document.getElementById("open-control"),
  personal: document.getElementById("personal"),
  personalOverview: document.getElementById("personal-overview"),
  personalDailyBrief: document.getElementById("personal-daily-brief"),
  personalWellbeingSummary: document.getElementById("personal-wellbeing-summary"),
  refreshPersonal: document.getElementById("refresh-personal"),
  personalCommitmentForm: document.getElementById("personal-commitment-form"),
  personalCommitmentTitle: document.getElementById("personal-commitment-title"),
  personalCommitmentDate: document.getElementById("personal-commitment-date"),
  personalCommitmentTime: document.getElementById("personal-commitment-time"),
  personalCommitmentPriority: document.getElementById("personal-commitment-priority"),
  personalRoutineForm: document.getElementById("personal-routine-form"),
  personalRoutineTitle: document.getElementById("personal-routine-title"),
  personalRoutineDate: document.getElementById("personal-routine-date"),
  personalRoutineTime: document.getElementById("personal-routine-time"),
  personalRoutineRecurrence: document.getElementById("personal-routine-recurrence"),
  personalBirthdayForm: document.getElementById("personal-birthday-form"),
  personalBirthdayPerson: document.getElementById("personal-birthday-person"),
  personalBirthdayMonth: document.getElementById("personal-birthday-month"),
  personalBirthdayDay: document.getElementById("personal-birthday-day"),
  personalBirthdayYear: document.getElementById("personal-birthday-year"),
  personalWellbeingForm: document.getElementById("personal-wellbeing-form"),
  personalWellbeingDate: document.getElementById("personal-wellbeing-date"),
  personalWellbeingMood: document.getElementById("personal-wellbeing-mood"),
  personalWellbeingEnergy: document.getElementById("personal-wellbeing-energy"),
  personalWellbeingStress: document.getElementById("personal-wellbeing-stress"),
  personalWellbeingFocus: document.getElementById("personal-wellbeing-focus"),
  personalWellbeingSleep: document.getElementById("personal-wellbeing-sleep"),
  personalWellbeingActivity: document.getElementById("personal-wellbeing-activity"),
  personalWellbeingNote: document.getElementById("personal-wellbeing-note"),
  personalOrganizerItems: document.getElementById("personal-organizer-items"),
  personalReminders: document.getElementById("personal-reminders"),
  personalCoachingPlans: document.getElementById("personal-coaching-plans"),
  personalRoutineCheckinForm: document.getElementById("personal-routine-checkin-form"),
  personalRoutineCheckinId: document.getElementById("personal-routine-checkin-id"),
  personalRoutineCheckinDate: document.getElementById("personal-routine-checkin-date"),
  personalRoutineCheckinStatus: document.getElementById("personal-routine-checkin-status"),
  personalRoutineCheckinNote: document.getElementById("personal-routine-checkin-note"),
  personalReminderForm: document.getElementById("personal-reminder-form"),
  personalReminderItemId: document.getElementById("personal-reminder-item-id"),
  personalReminderMinutes: document.getElementById("personal-reminder-minutes"),
  personalReminderReviewForm: document.getElementById("personal-reminder-review-form"),
  personalReminderReviewId: document.getElementById("personal-reminder-review-id"),
  personalReminderDecision: document.getElementById("personal-reminder-decision"),
  personalCoachingForm: document.getElementById("personal-coaching-form"),
  personalCoachingTitle: document.getElementById("personal-coaching-title"),
  personalCoachingFocus: document.getElementById("personal-coaching-focus"),
  personalCoachingStart: document.getElementById("personal-coaching-start"),
  personalCoachingReview: document.getElementById("personal-coaching-review"),
  personalCoachingObjective: document.getElementById("personal-coaching-objective"),
  personalCoachingActions: document.getElementById("personal-coaching-actions"),
  personalCoachingStatusForm: document.getElementById("personal-coaching-status-form"),
  personalCoachingStatusId: document.getElementById("personal-coaching-status-id"),
  personalCoachingStatus: document.getElementById("personal-coaching-status"),
  personalCoachingActionForm: document.getElementById("personal-coaching-action-form"),
  personalCoachingActionId: document.getElementById("personal-coaching-action-id"),
  personalCoachingActionStatus: document.getElementById("personal-coaching-action-status"),
  personalAutomationPolicies: document.getElementById("personal-automation-policies"),
  personalAutomations: document.getElementById("personal-automations"),
  personalAutomationRuns: document.getElementById("personal-automation-runs"),
  personalAutomationInbox: document.getElementById("personal-automation-inbox"),
  personalAutomationScan: document.getElementById("personal-automation-scan"),
  personalAutomationPolicyForm: document.getElementById("personal-automation-policy-form"),
  personalAutomationPolicyTitle: document.getElementById("personal-automation-policy-title"),
  personalAutomationPolicyAction: document.getElementById("personal-automation-policy-action"),
  personalAutomationPolicyLevel: document.getElementById("personal-automation-policy-level"),
  personalAutomationPolicyLimit: document.getElementById("personal-automation-policy-limit"),
  personalAutomationPolicyWindowStart: document.getElementById("personal-automation-policy-window-start"),
  personalAutomationPolicyWindowEnd: document.getElementById("personal-automation-policy-window-end"),
  personalAutomationForm: document.getElementById("personal-automation-form"),
  personalAutomationPolicyId: document.getElementById("personal-automation-policy-id"),
  personalAutomationTitle: document.getElementById("personal-automation-title"),
  personalAutomationSchedule: document.getElementById("personal-automation-schedule"),
  personalAutomationStart: document.getElementById("personal-automation-start"),
  personalAutomationTime: document.getElementById("personal-automation-time"),
  personalAutomationWeekday: document.getElementById("personal-automation-weekday"),
  personalAutomationMonthDay: document.getElementById("personal-automation-month-day"),
  personalAutomationParams: document.getElementById("personal-automation-params"),
  personalAutomationRunForm: document.getElementById("personal-automation-run-form"),
  personalAutomationRunId: document.getElementById("personal-automation-run-id"),
  personalAutomationInboxForm: document.getElementById("personal-automation-inbox-form"),
  personalAutomationInboxId: document.getElementById("personal-automation-inbox-id"),
  personalAutomationInboxStatus: document.getElementById("personal-automation-inbox-status"),
  personalSchedulerStatus: document.getElementById("personal-scheduler-status"),
  personalSchedulerInterval: document.getElementById("personal-scheduler-interval"),
  personalSchedulerStart: document.getElementById("personal-scheduler-start"),
  personalSchedulerCycle: document.getElementById("personal-scheduler-cycle"),
  personalSchedulerStop: document.getElementById("personal-scheduler-stop"),
  personalNotificationsEnable: document.getElementById("personal-notifications-enable"),
  personalLocalNotifications: document.getElementById("personal-local-notifications"),
  personalNotificationForm: document.getElementById("personal-notification-form"),
  personalNotificationId: document.getElementById("personal-notification-id"),
  personalNotificationStatus: document.getElementById("personal-notification-status"),
  personalIntentStatus: document.getElementById("personal-intent-status"),
  personalIntentResolutions: document.getElementById("personal-intent-resolutions"),
  personalIntentProposals: document.getElementById("personal-intent-proposals"),
  personalIntentProposeForm: document.getElementById("personal-intent-propose-form"),
  personalIntentPhrase: document.getElementById("personal-intent-phrase"),
  personalIntentName: document.getElementById("personal-intent-name"),
  personalIntentReviewForm: document.getElementById("personal-intent-review-form"),
  personalIntentProposalId: document.getElementById("personal-intent-proposal-id"),
  personalIntentDecision: document.getElementById("personal-intent-decision"),
  inspector: document.getElementById("inspector"),
  inspectorTabs: document.getElementById("inspector-tabs"),
  inspectorToolbar: document.getElementById("inspector-toolbar"),
  inspectorSearch: document.getElementById("inspector-search"),
  inspectorFilter: document.getElementById("inspector-filter"),
  inspectorContent: document.getElementById("inspector-content"),
  overviewGrid: document.getElementById("overview-grid"),
  refreshInspector: document.getElementById("refresh-inspector"),
  alexandria: document.getElementById("alexandria"),
  control: document.getElementById("control"),
  controlOverview: document.getElementById("control-overview"),
  refreshControl: document.getElementById("refresh-control"),
  trustProjectForm: document.getElementById("trust-project-form"),
  trustProjectPath: document.getElementById("trust-project-path"),
  controlProjects: document.getElementById("control-projects"),
  phpProfileForm: document.getElementById("php-profile-form"),
  profileProjectRoot: document.getElementById("profile-project-root"),
  profilePhpstanConfig: document.getElementById("profile-phpstan-config"),
  profilePhpstanLevel: document.getElementById("profile-phpstan-level"),
  profilePhpunitConfig: document.getElementById("profile-phpunit-config"),
  profilePhpunitTestsuite: document.getElementById("profile-phpunit-testsuite"),
  profileTimeout: document.getElementById("profile-timeout"),
  profileOutputLimit: document.getElementById("profile-output-limit"),
  profileComposerStrict: document.getElementById("profile-composer-strict"),
  profileComposerEnabled: document.getElementById("profile-composer-enabled"),
  profileSyntaxEnabled: document.getElementById("profile-syntax-enabled"),
  profilePhpstanEnabled: document.getElementById("profile-phpstan-enabled"),
  profilePhpunitEnabled: document.getElementById("profile-phpunit-enabled"),
  profileFailFast: document.getElementById("profile-fail-fast"),
  profileRequireTools: document.getElementById("profile-require-tools"),
  profileMaxPhpFiles: document.getElementById("profile-max-php-files"),
  profileExcludePaths: document.getElementById("profile-exclude-paths"),
  clearProfileForm: document.getElementById("clear-profile-form"),
  controlProfiles: document.getElementById("control-profiles"),
  controlPhpVerifications: document.getElementById("control-php-verifications"),
  controlWebVerifications: document.getElementById("control-web-verifications"),
  controlPythonVerifications: document.getElementById("control-python-verifications"),
  controlJavaVerifications: document.getElementById("control-java-verifications"),
  controlKotlinVerifications: document.getElementById("control-kotlin-verifications"),
  controlDotnetVerifications: document.getElementById("control-dotnet-verifications"),
  controlNativeVerifications: document.getElementById("control-native-verifications"),
  controlRubyVerifications: document.getElementById("control-ruby-verifications"),
  controlGoVerifications: document.getElementById("control-go-verifications"),
  controlRustVerifications: document.getElementById("control-rust-verifications"),
  controlSwiftVerifications: document.getElementById("control-swift-verifications"),
  controlDartVerifications: document.getElementById("control-dart-verifications"),
  controlSqlVerifications: document.getElementById("control-sql-verifications"),
  controlActionRuns: document.getElementById("control-action-runs"),
  controlChangeProposals: document.getElementById("control-change-proposals"),
  controlValidationCycles: document.getElementById("control-validation-cycles"),
  controlDevelopmentSessions: document.getElementById("control-development-sessions"),
  controlEthics: document.getElementById("control-ethics"),
  pythonProfileForm: document.getElementById("python-profile-form"),
  pythonProfileProjectRoot: document.getElementById("python-profile-project-root"),
  pythonProfileRuffConfig: document.getElementById("python-profile-ruff-config"),
  pythonProfileMypyConfig: document.getElementById("python-profile-mypy-config"),
  pythonProfilePytestPath: document.getElementById("python-profile-pytest-path"),
  pythonProfileTimeout: document.getElementById("python-profile-timeout"),
  pythonProfileOutputLimit: document.getElementById("python-profile-output-limit"),
  pythonProfileMaxFiles: document.getElementById("python-profile-max-files"),
  pythonProfileExcludePaths: document.getElementById("python-profile-exclude-paths"),
  pythonProfilePyprojectEnabled: document.getElementById("python-profile-pyproject-enabled"),
  pythonProfileCompileEnabled: document.getElementById("python-profile-compile-enabled"),
  pythonProfileRuffEnabled: document.getElementById("python-profile-ruff-enabled"),
  pythonProfileMypyEnabled: document.getElementById("python-profile-mypy-enabled"),
  pythonProfilePytestEnabled: document.getElementById("python-profile-pytest-enabled"),
  pythonProfileFailFast: document.getElementById("python-profile-fail-fast"),
  pythonProfileRequireTools: document.getElementById("python-profile-require-tools"),
  clearPythonProfileForm: document.getElementById("clear-python-profile-form"),
  controlPackages: document.getElementById("control-packages"),
  packageInstallForm: document.getElementById("package-install-form"),
  packageInstallPath: document.getElementById("package-install-path"),
  packageCreateForm: document.getElementById("package-create-form"),
  packageCreateDestination: document.getElementById("package-create-destination"),
  packageCreateId: document.getElementById("package-create-id"),
  packageCreateName: document.getElementById("package-create-name"),
  packageCreateVersion: document.getElementById("package-create-version"),
  packageCreateDomain: document.getElementById("package-create-domain"),
  packageCreateLanguage: document.getElementById("package-create-language"),
  packageCreateLicense: document.getElementById("package-create-license"),
  packageCreateTier: document.getElementById("package-create-tier"),
  packageCreateSources: document.getElementById("package-create-sources"),
  packageCreateTags: document.getElementById("package-create-tags"),
  packageExportForm: document.getElementById("package-export-form"),
  packageExportId: document.getElementById("package-export-id"),
  packageExportDestination: document.getElementById("package-export-destination"),
  controlAuditSearch: document.getElementById("control-audit-search"),
  controlAuditAction: document.getElementById("control-audit-action"),
  controlAuditOutcome: document.getElementById("control-audit-outcome"),
  refreshControlAudit: document.getElementById("refresh-control-audit"),
  controlAudit: document.getElementById("control-audit"),
  alexandriaOverview: document.getElementById("alexandria-overview"),
  alexandriaSearch: document.getElementById("alexandria-search"),
  searchAlexandria: document.getElementById("search-alexandria"),
  alexandriaLibraries: document.getElementById("alexandria-libraries"),
  alexandriaLanguagePacks: document.getElementById("alexandria-language-packs"),
  alexandriaDetail: document.getElementById("alexandria-detail"),
  createLibrary: document.getElementById("create-library"),
  alexandriaFile: document.getElementById("alexandria-file"),
  libraryDialog: document.getElementById("library-dialog"),
  libraryDialogTitle: document.getElementById("library-dialog-title"),
  libraryDialogConfirm: document.getElementById("library-dialog-confirm"),
  libraryName: document.getElementById("library-name"),
  libraryDescription: document.getElementById("library-description"),
  libraryDomain: document.getElementById("library-domain"),
  libraryLanguage: document.getElementById("library-language"),
  libraryVersion: document.getElementById("library-version"),
  libraryLicense: document.getElementById("library-license"),
  composerArea: document.getElementById("composer-area"),
  dropOverlay: document.getElementById("drop-overlay"),
  deleteDialog: document.getElementById("delete-dialog"),
  deleteDialogTitle: document.getElementById("delete-dialog-title"),
  deleteDialogText: document.getElementById("delete-dialog-text"),
  deleteCancel: document.getElementById("delete-cancel"),
  deleteConfirm: document.getElementById("delete-confirm"),
  editDialog: document.getElementById("edit-dialog"),
  editDialogTitle: document.getElementById("edit-dialog-title"),
  editDialogContent: document.getElementById("edit-dialog-content"),
  toast: document.getElementById("toast"),
};

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (method !== "GET" && method !== "HEAD") headers["X-Elyndra-Token"] = state.token;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, method, headers });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { error: `Respuesta HTTP inválida (${response.status}).` };
  }
  if (!response.ok) throw new Error(payload.error || `Error HTTP ${response.status}.`);
  return payload;
}

async function downloadAccountExport(password, exportPassphrase) {
  const response = await fetch("/api/account/export", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Elyndra-Token": state.token,
    },
    body: JSON.stringify({
      password,
      export_passphrase: exportPassphrase,
      approved: true,
    }),
  });
  if (!response.ok) {
    let message = `Error HTTP ${response.status}.`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch {
      // The export endpoint normally returns JSON only on errors.
    }
    throw new Error(message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "elyndra-encrypted-export.json";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function streamApi(path, options = {}) {
  const headers = {
    Accept: "application/x-ndjson",
    "Content-Type": "application/json",
    "X-Elyndra-Token": state.token,
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, method: "POST", headers });
  if (!response.ok || !response.body) {
    let message = `Error HTTP ${response.status}.`;
    try {
      const payload = await response.json();
      message = payload.error || message;
    } catch {
      // Keep the deterministic HTTP fallback.
    }
    throw new Error(message);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let completed = null;
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      if (event.type === "status") options.onStatus?.(event);
      if (event.type === "token") options.onToken?.(event.text || "");
      if (event.type === "error") throw new Error(event.error || "Error de streaming.");
      if (event.type === "done") completed = event.response;
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = JSON.parse(buffer);
    if (event.type === "done") completed = event.response;
    if (event.type === "error") throw new Error(event.error || "Error de streaming.");
  }
  if (!completed) throw new Error("Elyndra cerró el stream sin respuesta final.");
  return completed;
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function formatRelativeDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const difference = Date.now() - date.getTime();
  const minutes = Math.floor(difference / 60000);
  if (minutes < 1) return "ahora";
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `hace ${days} d`;
  return new Intl.DateTimeFormat("es-CL", { day: "2-digit", month: "short" }).format(date);
}

function formatElapsed(milliseconds) {
  if (milliseconds < 1000) return `${milliseconds} ms`;
  if (milliseconds < 60000) return `${(milliseconds / 1000).toFixed(1)} s`;
  const minutes = Math.floor(milliseconds / 60000);
  const seconds = Math.round((milliseconds % 60000) / 1000);
  return `${minutes} min ${String(seconds).padStart(2, "0")} s`;
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function attachmentStatusLabel(status) {
  const labels = {
    valid: "Validado",
    invalid: "Inválido",
    partial: "Validación parcial",
    unavailable: "Validador no disponible",
    not_checked: "No validado",
    extracted: "Texto extraído",
    empty: "Sin texto extraíble",
    failed: "Extracción fallida",
    not_applicable: "No aplica",
  };
  return labels[status] || status || "Sin estado";
}

function attachmentIcon(attachment) {
  if (attachment.kind === "image") return "IMG";
  const extension = attachment.filename.split(".").pop()?.toUpperCase() || "DOC";
  return extension.slice(0, 4);
}

function appendInlineMarkdown(container, value) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let cursor = 0;
  for (const match of value.matchAll(pattern)) {
    if (match.index > cursor) {
      container.append(document.createTextNode(value.slice(cursor, match.index)));
    }
    const token = match[0];
    if (token.startsWith("`")) {
      container.append(createElement("code", "inline-code", token.slice(1, -1)));
    } else {
      container.append(createElement("strong", "", token.slice(2, -2)));
    }
    cursor = match.index + token.length;
  }
  if (cursor < value.length) {
    container.append(document.createTextNode(value.slice(cursor)));
  }
}

function renderMarkdown(container, text) {
  const lines = String(text).replaceAll("\r\n", "\n").split("\n");
  let code = null;
  let list = null;
  let listType = "";
  const flushList = () => {
    if (list) container.append(list);
    list = null;
    listType = "";
  };
  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      flushList();
      if (code) {
        container.append(code);
        code = null;
      } else {
        code = createElement("pre", "message-code");
      }
      continue;
    }
    if (code) {
      code.textContent += `${line}\n`;
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      flushList();
      const level = Math.min(heading[1].length + 2, 5);
      const element = document.createElement(`h${level}`);
      appendInlineMarkdown(element, heading[2]);
      container.append(element);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const nextType = ordered ? "ol" : "ul";
      if (!list || listType !== nextType) {
        flushList();
        list = document.createElement(nextType);
        list.className = "message-list";
        listType = nextType;
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, (ordered || unordered)[1]);
      list.append(item);
      continue;
    }
    flushList();
    if (!line.trim()) {
      container.append(document.createElement("br"));
      continue;
    }
    const paragraph = createElement("p", "message-paragraph");
    appendInlineMarkdown(paragraph, line);
    container.append(paragraph);
  }
  flushList();
  if (code) container.append(code);
}

function shortSummary(summary) {
  if (!summary) return "Sin mensajes todavía";
  return summary.replaceAll("\n", " ").replace(/\s+/g, " ").trim().slice(0, 138);
}

function showChatWorkspace() {
  state.inspectorActive = false;
  state.alexandriaActive = false;
  state.personalActive = false;
  state.profileActive = false;
  state.controlActive = false;
  elements.openMemory.classList.remove("active");
  elements.openAlexandria.classList.remove("active");
  elements.openPersonal.classList.remove("active");
  elements.openProfile.classList.remove("active");
  elements.openControl.classList.remove("active");
  elements.inspector.hidden = true;
  elements.alexandria.hidden = true;
  elements.personal.hidden = true;
  elements.profile.hidden = true;
  elements.control.hidden = true;
  elements.conversation.hidden = false;
  elements.composerArea.hidden = false;
  elements.renameChat.hidden = false;
  elements.chatActions.hidden = false;
}

function showInspectorWorkspace() {
  state.inspectorActive = true;
  state.alexandriaActive = false;
  state.personalActive = false;
  state.profileActive = false;
  state.controlActive = false;
  elements.openMemory.classList.add("active");
  elements.openAlexandria.classList.remove("active");
  elements.openPersonal.classList.remove("active");
  elements.openProfile.classList.remove("active");
  elements.openControl.classList.remove("active");
  elements.inspector.hidden = false;
  elements.alexandria.hidden = true;
  elements.personal.hidden = true;
  elements.profile.hidden = true;
  elements.control.hidden = true;
  elements.conversation.hidden = true;
  elements.composerArea.hidden = true;
  elements.renameChat.hidden = true;
  elements.chatActions.hidden = true;
  elements.chatTitle.textContent = "Memoria de Elyndra";
  elements.chatSubtitle.textContent = "Inspector local · revisable · bajo tu control";
}

function showAlexandriaWorkspace() {
  state.inspectorActive = false;
  state.alexandriaActive = true;
  state.personalActive = false;
  state.profileActive = false;
  state.controlActive = false;
  elements.openMemory.classList.remove("active");
  elements.openAlexandria.classList.add("active");
  elements.openPersonal.classList.remove("active");
  elements.openProfile.classList.remove("active");
  elements.openControl.classList.remove("active");
  elements.inspector.hidden = true;
  elements.alexandria.hidden = false;
  elements.personal.hidden = true;
  elements.profile.hidden = true;
  elements.control.hidden = true;
  elements.conversation.hidden = true;
  elements.composerArea.hidden = true;
  elements.renameChat.hidden = true;
  elements.chatActions.hidden = true;
  elements.chatTitle.textContent = "Alejandría";
  elements.chatSubtitle.textContent = "Bibliotecas locales · versionadas · bajo demanda";
}

function showControlWorkspace() {
  state.inspectorActive = false;
  state.alexandriaActive = false;
  state.personalActive = false;
  state.profileActive = false;
  state.controlActive = true;
  elements.openMemory.classList.remove("active");
  elements.openAlexandria.classList.remove("active");
  elements.openPersonal.classList.remove("active");
  elements.openProfile.classList.remove("active");
  elements.openControl.classList.add("active");
  elements.inspector.hidden = true;
  elements.alexandria.hidden = true;
  elements.personal.hidden = true;
  elements.profile.hidden = true;
  elements.control.hidden = false;
  elements.conversation.hidden = true;
  elements.composerArea.hidden = true;
  elements.renameChat.hidden = true;
  elements.chatActions.hidden = true;
  elements.chatTitle.textContent = "Centro de control";
  elements.chatSubtitle.textContent = "Permisos · toolchains · paquetes de conocimiento";
}

function showPersonalWorkspace() {
  state.inspectorActive = false;
  state.alexandriaActive = false;
  state.personalActive = true;
  state.profileActive = false;
  state.controlActive = false;
  elements.openMemory.classList.remove("active");
  elements.openAlexandria.classList.remove("active");
  elements.openPersonal.classList.add("active");
  elements.openProfile.classList.remove("active");
  elements.openControl.classList.remove("active");
  elements.inspector.hidden = true;
  elements.alexandria.hidden = true;
  elements.personal.hidden = false;
  elements.profile.hidden = true;
  elements.control.hidden = true;
  elements.conversation.hidden = true;
  elements.composerArea.hidden = true;
  elements.renameChat.hidden = true;
  elements.chatActions.hidden = true;
  elements.chatTitle.textContent = "Asistente personal";
  elements.chatSubtitle.textContent = state.bootstrap?.developer_mode
    ? `Organización · bienestar · runtime ${state.bootstrap?.version || "local"}`
    : "Organización y bienestar local";
}

function showProfileWorkspace() {
  state.inspectorActive = false;
  state.alexandriaActive = false;
  state.personalActive = false;
  state.profileActive = true;
  state.controlActive = false;
  elements.openMemory.classList.remove("active");
  elements.openAlexandria.classList.remove("active");
  elements.openPersonal.classList.remove("active");
  elements.openProfile.classList.add("active");
  elements.openControl.classList.remove("active");
  elements.inspector.hidden = true;
  elements.alexandria.hidden = true;
  elements.personal.hidden = true;
  elements.profile.hidden = false;
  elements.control.hidden = true;
  elements.conversation.hidden = true;
  elements.composerArea.hidden = true;
  elements.renameChat.hidden = true;
  elements.chatActions.hidden = true;
  elements.chatTitle.textContent = "Perfil";
  elements.chatSubtitle.textContent = "Cuenta · privacidad · seguridad local";
}

function renderProfile() {
  const data = state.accountData || {};
  const account = data.account || {};
  const security = data.security || {};
  const telemetry = data.telemetry || {};
  elements.profileOverview.replaceChildren();
  for (const [label, value] of [
    ["Usuario", account.username || ""],
    ["Edad", account.age ?? ""],
    ["Modo", account.developer_mode ? "Desarrollador" : "Usuario"],
    ["Telemetría", account.telemetry_enabled ? "Activada" : "Desactivada"],
  ]) {
    const card = createElement("article", "overview-card");
    card.append(createElement("span", "overview-label", label));
    card.append(createElement("strong", "overview-value", String(value)));
    elements.profileOverview.append(card);
  }
  elements.profileUsername.value = account.username || "";
  elements.profilePreferredName.value = account.preferred_name || "";
  elements.profilePronouns.value = account.pronouns || "";
  elements.profileSex.value = account.sex || "";
  elements.profileGenderIdentity.value = account.gender_identity || "";
  elements.profileSexualOrientation.value = account.sexual_orientation || "";
  elements.profileTimezone.value = account.timezone || "UTC";
  elements.profileLanguage.value = account.language || "es-CL";
  elements.profileBirthdayGreeting.checked = Boolean(account.birthday_greeting_enabled);
  elements.profileDeveloperMode.checked = Boolean(account.developer_mode);
  elements.profileTelemetry.checked = Boolean(account.telemetry_enabled);
  elements.profileSecurity.replaceChildren(
    createElement("div", "control-item", `Contraseña: ${security.password_hash || "argon2id"}`),
    createElement("div", "control-item", `2FA: ${security.two_factor_status || "disponible más adelante"}`),
    createElement("div", "control-item", "Exportación cifrada local: disponible en web y CLI"),
    createElement("div", "control-item", "Respaldo remoto: no implementado"),
  );
  elements.profileTelemetryPreview.replaceChildren(
    createElement("pre", "control-code", JSON.stringify(telemetry, null, 2)),
  );
}

async function loadProfile() {
  state.accountData = await api("/api/account");
  renderProfile();
}

async function openProfile({ updateUrl = true } = {}) {
  showProfileWorkspace();
  if (updateUrl && window.location.pathname !== "/profile") {
    window.history.pushState({ profile: true }, "", "/profile");
  }
  await loadProfile();
  closeSidebar();
}

function showPendingBrowserNotifications(items) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  for (const item of items.filter((entry) => entry.status === "pending")) {
    const key = `elyndra-notification-${item.public_id}`;
    if (window.sessionStorage.getItem(key)) continue;
    new Notification(item.title, { body: item.body, tag: item.public_id });
    window.sessionStorage.setItem(key, "shown");
  }
}

function visibleTechnicalId(publicId) {
  return state.bootstrap?.developer_mode ? `${publicId} · ` : "";
}

function applyDeveloperModeVisibility() {
  const developer = Boolean(state.bootstrap?.developer_mode);
  if (elements.runtimeVersion) elements.runtimeVersion.hidden = !developer;
  for (const element of document.querySelectorAll("[data-developer-technical]")) {
    element.hidden = !developer;
  }
}

function renderPersonal() {
  const data = state.personalData || {};
  const organizer = data.organizer || {};
  const wellbeing = data.wellbeing || {};
  elements.personalOverview.replaceChildren();
  const overviewValues = [
    ["Elementos activos", organizer.active_items || 0],
    ["Rutinas", organizer.active_routines || 0],
    ["Check-ins", wellbeing.checkins || 0],
    ["Planes activos", wellbeing.active_plans || 0],
    ["Automatizaciones", data.automation?.active_automations || 0],
    ["Bandeja sin leer", data.automation?.unread_inbox || 0],
    ["Scheduler", data.scheduler?.running ? "Activo" : "Detenido"],
    ["Notificaciones", data.scheduler?.pending_notifications || 0],
    ["Intenciones", data.semantic_intents?.ontology_intents || 0],
    ["Ejemplos revisados", data.semantic_intents?.reviewed_examples || 0],
  ];
  if (state.bootstrap?.developer_mode) {
    overviewValues.unshift(["Runtime web", data.runtime_version || "desconocido"]);
  }
  for (const [label, value] of overviewValues) {
    const card = createElement("article", "overview-card");
    card.append(createElement("span", "overview-label", label));
    card.append(createElement("strong", "overview-value", String(value)));
    elements.personalOverview.append(card);
  }

  elements.personalDailyBrief.replaceChildren();
  const brief = data.daily_brief || {};
  const scheduled = brief.scheduled || [];
  if (!scheduled.length) {
    elements.personalDailyBrief.append(createElement("p", "empty-list", "Sin elementos programados para hoy."));
  } else {
    for (const item of scheduled) {
      elements.personalDailyBrief.append(createElement(
        "div",
        "control-item",
        `${item.time || "Sin hora"} · ${item.item_type} · ${item.title}`,
      ));
    }
  }

  elements.personalOrganizerItems.replaceChildren();
  const organizerItems = data.organizer_items || [];
  if (!organizerItems.length) {
    elements.personalOrganizerItems.append(
      createElement("p", "empty-list", "Sin elementos personales."),
    );
  } else {
    for (const item of organizerItems) {
      elements.personalOrganizerItems.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(item.public_id)}${item.item_type} · ${item.status} · ${item.title}`,
      ));
    }
  }

  elements.personalReminders.replaceChildren();
  const reminders = data.reminders || [];
  if (!reminders.length) {
    elements.personalReminders.append(
      createElement("p", "empty-list", "Sin recordatorios."),
    );
  } else {
    for (const reminder of reminders) {
      elements.personalReminders.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(reminder.public_id)}${reminder.status} · ${reminder.minutes_before} min · ${reminder.item_title || ""}`,
      ));
    }
  }

  elements.personalCoachingPlans.replaceChildren();
  const plans = data.coaching_plans || [];
  if (!plans.length) {
    elements.personalCoachingPlans.append(
      createElement("p", "empty-list", "Sin planes de coaching."),
    );
  } else {
    for (const plan of plans) {
      const details = createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(plan.public_id)}${plan.status} · ${plan.title}`,
      );
      elements.personalCoachingPlans.append(details);
    }
  }

  elements.personalAutomationPolicies.replaceChildren();
  const automationPolicies = data.automation_policies || [];
  if (!automationPolicies.length) {
    elements.personalAutomationPolicies.append(
      createElement("p", "empty-list", "Sin políticas de automatización."),
    );
  } else {
    for (const policy of automationPolicies) {
      elements.personalAutomationPolicies.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(policy.public_id)}${policy.status} · ${policy.autonomy_level} · ${policy.action_type}`,
      ));
    }
  }

  elements.personalAutomations.replaceChildren();
  const automations = data.automations || [];
  if (!automations.length) {
    elements.personalAutomations.append(
      createElement("p", "empty-list", "Sin automatizaciones."),
    );
  } else {
    for (const automation of automations) {
      elements.personalAutomations.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(automation.public_id)}${automation.status} · ${automation.schedule_kind} · ${automation.title}`,
      ));
    }
  }

  elements.personalAutomationRuns.replaceChildren();
  const automationRuns = data.automation_runs || [];
  if (!automationRuns.length) {
    elements.personalAutomationRuns.append(
      createElement("p", "empty-list", "Sin ejecuciones de automatización."),
    );
  } else {
    for (const run of automationRuns) {
      elements.personalAutomationRuns.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(run.public_id)}${run.status} · ${run.occurrence_at} · ${run.title}`,
      ));
    }
  }

  elements.personalAutomationInbox.replaceChildren();
  const automationInbox = data.automation_inbox || [];
  if (!automationInbox.length) {
    elements.personalAutomationInbox.append(
      createElement("p", "empty-list", "Bandeja local vacía."),
    );
  } else {
    for (const item of automationInbox) {
      const entry = createElement("article", "control-item");
      entry.append(
        createElement("strong", "", `${visibleTechnicalId(item.public_id)}${item.status} · ${item.title}`),
        createElement("p", "inspector-note", item.body),
      );
      elements.personalAutomationInbox.append(entry);
    }
  }

  elements.personalSchedulerStatus.replaceChildren();
  const scheduler = data.scheduler || {};
  elements.personalSchedulerStatus.append(
    createElement(
      "div",
      "control-item",
      `Estado: ${scheduler.running ? "activo" : "detenido"} · `
        + `bloqueo=${scheduler.interprocess_lock ? "sí" : "no"} · `
        + `pendientes=${scheduler.pending_notifications || 0}`,
    ),
  );
  const session = scheduler.latest_session;
  if (session) {
    elements.personalSchedulerStatus.append(createElement(
      "div",
      "control-item",
      `${visibleTechnicalId(session.public_id)}${session.status} · ciclos=${session.scans_count}`,
    ));
  }

  elements.personalLocalNotifications.replaceChildren();
  const localNotifications = data.local_notifications || [];
  if (!localNotifications.length) {
    elements.personalLocalNotifications.append(
      createElement("p", "empty-list", "Sin notificaciones locales."),
    );
  } else {
    for (const item of localNotifications) {
      const entry = createElement("article", "control-item");
      entry.append(
        createElement("strong", "", `${visibleTechnicalId(item.public_id)}${item.status} · ${item.title}`),
        createElement("p", "inspector-note", item.body),
      );
      elements.personalLocalNotifications.append(entry);
    }
  }
  showPendingBrowserNotifications(localNotifications);

  elements.personalIntentStatus.replaceChildren();
  const semantic = data.semantic_intents || {};
  elements.personalIntentStatus.append(createElement(
    "div",
    "control-item",
    `Intenciones=${semantic.ontology_intents || 0} · `
      + `ejemplos revisados=${semantic.reviewed_examples || 0} · `
      + `fallbacks tutor=${semantic.tutor_fallbacks || 0} · `
      + `aprendizaje silencioso=${semantic.silent_learning ? "sí" : "no"}`,
  ));

  elements.personalIntentResolutions.replaceChildren();
  const intentResolutions = data.intent_resolutions || [];
  if (!intentResolutions.length) {
    elements.personalIntentResolutions.append(
      createElement("p", "empty-list", "Sin resoluciones semánticas registradas."),
    );
  } else {
    for (const resolution of intentResolutions) {
      elements.personalIntentResolutions.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(resolution.public_id)}${resolution.intent} · `
          + `${Number(resolution.confidence || 0).toFixed(2)} · ${resolution.source}`,
      ));
    }
  }

  elements.personalIntentProposals.replaceChildren();
  const intentProposals = data.intent_learning_proposals || [];
  if (!intentProposals.length) {
    elements.personalIntentProposals.append(
      createElement("p", "empty-list", "Sin propuestas de aprendizaje lingüístico."),
    );
  } else {
    for (const proposal of intentProposals) {
      elements.personalIntentProposals.append(createElement(
        "div",
        "control-item",
        `${visibleTechnicalId(proposal.public_id)}${proposal.status} · ${proposal.intent} · ${proposal.phrase}`,
      ));
    }
  }

  elements.personalWellbeingSummary.replaceChildren();
  const summary = data.wellbeing_summary || {};
  const metrics = summary.metrics || {};
  if (!summary.checkins) {
    elements.personalWellbeingSummary.append(createElement("p", "empty-list", "Sin check-ins en los últimos siete días."));
  } else {
    for (const [label, key] of [["Ánimo", "mood"], ["Energía", "energy"], ["Estrés", "stress"], ["Concentración", "focus"]]) {
      if (metrics[key] !== null && metrics[key] !== undefined) {
        elements.personalWellbeingSummary.append(createElement("div", "control-item", `${label}: ${Number(metrics[key]).toFixed(1)}/5`));
      }
    }
    for (const signal of summary.signals || []) {
      elements.personalWellbeingSummary.append(createElement("p", "inspector-note", signal));
    }
  }
}

async function loadPersonal() {
  state.personalData = await api("/api/personal/overview");
  renderPersonal();
}

async function openPersonal({ updateUrl = true } = {}) {
  showPersonalWorkspace();
  if (updateUrl && window.location.pathname !== "/personal") {
    window.history.pushState({ personal: true }, "", "/personal");
  }
  try {
    await loadPersonal();
  } catch (error) {
    showError(error.message);
  }
  closeSidebar();
}

async function confirmedPersonalWrite(title, summary, path, payload, form) {
  const accepted = await confirmElyndraAction(title, summary, "Confirmar y guardar");
  if (!accepted) return;
  await api(path, {
    method: "POST",
    body: JSON.stringify({ ...payload, approved: true }),
  });
  form?.reset();
  await loadPersonal();
  showNotice("Cambio personal guardado localmente.");
}

function renderControlOverview() {
  const overview = state.controlOverview || {};
  const cards = [
    ["Raíces configuradas", (overview.configured_roots || []).length],
    ["Proyectos confiables", overview.trusted_projects || 0],
    ["Perfiles PHP", overview.php_profiles || 0],
    ["Perfiles web", overview.web_profiles || 0],
    ["Perfiles Python", overview.python_profiles || 0],
    ["Perfiles Java", overview.java_profiles || 0],
    ["Perfiles Kotlin", overview.kotlin_profiles || 0],
    ["Perfiles C#/.NET", overview.dotnet_profiles || 0],
    ["Perfiles C/C++", overview.native_profiles || 0],
    ["Perfiles Ruby", overview.ruby_profiles || 0],
    ["Perfiles Go", overview.go_profiles || 0],
    ["Perfiles Rust", overview.rust_profiles || 0],
    ["Perfiles Swift", overview.swift_profiles || 0],
    ["Perfiles Dart/Flutter", overview.dart_profiles || 0],
    ["Perfiles SQL", overview.sql_profiles || 0],
    ["Verificaciones PHP", overview.php_verifications || 0],
    ["Verificaciones web", overview.web_verifications || 0],
    ["Verificaciones Python", overview.python_verifications || 0],
    ["Verificaciones Java", overview.java_verifications || 0],
    ["Verificaciones Kotlin", overview.kotlin_verifications || 0],
    ["Verificaciones C#/.NET", overview.dotnet_verifications || 0],
    ["Verificaciones C/C++", overview.native_verifications || 0],
    ["Verificaciones Ruby", overview.ruby_verifications || 0],
    ["Verificaciones Go", overview.go_verifications || 0],
    ["Verificaciones Rust", overview.rust_verifications || 0],
    ["Verificaciones Swift", overview.swift_verifications || 0],
    ["Verificaciones Dart/Flutter", overview.dart_verifications || 0],
    ["Verificaciones SQL", overview.sql_verifications || 0],
    ["Planes supervisados", overview.assistant_action_runs || 0],
    ["Propuestas de cambios", overview.assistant_change_proposals || 0],
    ["Cambios pendientes", overview.assistant_pending_changes || 0],
    ["Ciclos validación/reparación", overview.assistant_validation_cycles || 0],
    ["Sesiones de desarrollo", overview.assistant_development_sessions || 0],
    ["Tutores locales", overview.tutors?.enabled_tutors || 0],
    ["Benchmarks de tutores", overview.tutors?.benchmark_runs || 0],
    ["Selecciones de tutor", overview.tutors?.selections || 0],
    ["Paquetes Alejandría", overview.alexandria_packages || 0],
    ["Ejecuciones de skills", overview.skill_executions || 0],
    ["Eventos de aprobación", overview.approval_events || 0],
  ];
  elements.controlOverview.replaceChildren();
  for (const [label, value] of cards) {
    const card = createElement("article", "overview-card");
    card.append(createElement("strong", "", String(value)), createElement("span", "", label));
    elements.controlOverview.append(card);
  }
}

function controlProjectCard(item, removable) {
  const card = createElement("article", "control-card");
  const head = createElement("div", "memory-item-head");
  const label = item.source === "configured_root" ? "Raíz configurada" : "Proyecto confiable";
  head.append(createElement("span", "memory-kind", label));
  head.append(createElement("span", "memory-status approved", "persistente"));
  card.append(head, createElement("div", "document-path", item.path));
  if (item.profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil PHP guardado."));
  }
  if (item.web_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil web guardado."));
  }
  if (item.python_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Python guardado."));
  }
  if (item.java_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Java guardado."));
  }
  if (item.kotlin_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Kotlin guardado."));
  }
  if (item.dotnet_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil C#/.NET guardado."));
  }
  if (item.native_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil C/C++ guardado."));
  }
  if (item.ruby_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Ruby guardado."));
  }
  if (item.go_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Go guardado."));
  }
  if (item.rust_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Rust guardado."));
  }
  if (item.swift_profile) {
    card.append(createElement("div", "inspector-note", "Este proyecto tiene un perfil Swift guardado."));
  }
  const actions = [
    {
      label: item.profile ? "Editar perfil" : "Crear perfil",
      className: "primary",
      run: () => fillProfileForm(item.profile || { project_root: item.path }),
    },
    {
      label: item.python_profile ? "Editar Python" : "Perfil Python",
      run: () => fillPythonProfileForm(item.python_profile || { project_root: item.path }),
    },
  ];
  if (removable) {
    actions.push({
      label: "Revocar confianza",
      className: "danger",
      run: () => removeTrustedProject(item.path),
    });
  }
  card.append(inspectorActions(actions));
  return card;
}

function renderControlProjects() {
  const payload = state.controlProjects || {};
  elements.controlProjects.replaceChildren();
  const configured = payload.configured_roots || [];
  const trusted = payload.trusted_projects || [];
  for (const item of configured) elements.controlProjects.append(controlProjectCard(item, false));
  for (const item of trusted) elements.controlProjects.append(controlProjectCard(item, true));
  if (!configured.length && !trusted.length) {
    elements.controlProjects.append(createElement("div", "inspector-empty", "No hay raíces autorizadas."));
  }

  elements.controlProfiles.replaceChildren();
  for (const profile of payload.profiles || []) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", "Perfil PHP"));
    head.append(createElement("span", "memory-status approved", `#${profile.id}`));
    card.append(head, createElement("div", "document-path", profile.project_root));
    card.append(inspectorItemMeta([
      `PHPStan: ${profile.phpstan_config || "auto"} · nivel ${profile.phpstan_level || "auto"}`,
      `PHPUnit: ${profile.phpunit_config || "auto"} · suite ${profile.phpunit_testsuite || "auto"}`,
      `Composer strict: ${profile.composer_strict ? "sí" : "no"}`,
      `Etapas: ${[
        profile.composer_enabled ? "composer" : "",
        profile.syntax_scan_enabled ? "syntax" : "",
        profile.phpstan_enabled ? "phpstan" : "",
        profile.phpunit_enabled ? "phpunit" : "",
      ].filter(Boolean).join(", ") || "ninguna"}`,
      `Fail-fast: ${profile.fail_fast ? "sí" : "no"} · herramientas obligatorias: ${profile.require_tools ? "sí" : "no"}`,
      `Máximo PHP: ${profile.max_php_files || 2000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
      `Timeout: ${profile.timeout_seconds || "global"}`,
      `Salida: ${profile.max_output_chars || "global"}`,
    ]));
    card.append(inspectorActions([
      { label: "Editar", run: () => fillProfileForm(profile) },
      { label: "Eliminar", className: "danger", run: () => removePhpProfile(profile.project_root) },
    ]));
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.web_profiles || []) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", "Perfil web"));
    head.append(createElement("span", "memory-status approved", `#${profile.id}`));
    card.append(head, createElement("div", "document-path", profile.project_root));
    card.append(inspectorItemMeta([
      `Etapas: ${[
        profile.html_enabled ? "html" : "",
        profile.css_enabled ? "css" : "",
        profile.javascript_enabled ? "javascript" : "",
        profile.typescript_enabled ? "typescript" : "",
        profile.framework_checks_enabled ? "framework" : "",
        profile.eslint_enabled ? "eslint" : "",
        profile.stylelint_enabled ? "stylelint" : "",
      ].filter(Boolean).join(", ") || "ninguna"}`,
      `Preset: ${profile.framework_preset || "auto"} · Fail-fast: ${profile.fail_fast ? "sí" : "no"} · herramientas obligatorias: ${profile.require_tools ? "sí" : "no"}`,
      `ESLint config: ${profile.eslint_config || "auto"} · Stylelint config: ${profile.stylelint_config || "auto"}`,
      `Máximo archivos: ${profile.max_files || 3000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
      `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
      "Administración disponible desde CLI web-profile-set/delete.",
    ]));
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.python_profiles || []) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", "Perfil Python"));
    head.append(createElement("span", "memory-status approved", `#${profile.id}`));
    card.append(head, createElement("div", "document-path", profile.project_root));
    card.append(inspectorItemMeta([
      `Etapas: ${[
        profile.pyproject_enabled ? "pyproject" : "",
        profile.compile_enabled ? "compile" : "",
        profile.ruff_enabled ? "ruff" : "",
        profile.mypy_enabled ? "mypy" : "",
        profile.pytest_enabled ? "pytest" : "",
      ].filter(Boolean).join(", ") || "ninguna"}`,
      `Ruff config: ${profile.ruff_config || "auto"} · mypy config: ${profile.mypy_config || "auto"}`,
      `Pytest path: ${profile.pytest_path || "auto"} · Fail-fast: ${profile.fail_fast ? "sí" : "no"}`,
      `Máximo Python: ${profile.max_python_files || 3000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
      `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
    ]));
    card.append(inspectorActions([
      { label: "Editar", run: () => fillPythonProfileForm(profile) },
      { label: "Eliminar", className: "danger", run: () => removePythonProfile(profile.project_root) },
    ]));
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.java_profiles || []) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", "Perfil Java"));
    head.append(createElement("span", "memory-status approved", `#${profile.id}`));
    card.append(head, createElement("div", "document-path", profile.project_root));
    card.append(inspectorItemMeta([
      `Etapas: ${[
        profile.descriptor_enabled ? "descriptor" : "",
        profile.javac_enabled ? "javac" : "",
        profile.build_enabled ? "build" : "",
        profile.tests_enabled ? "tests" : "",
      ].filter(Boolean).join(", ") || "ninguna"}`,
      `Build: ${profile.build_tool || "auto"} · release: ${profile.java_release || "auto"}`,
      `Máximo Java: ${profile.max_java_files || 3000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
      `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
      "Administración disponible desde CLI java-profile-set/delete.",
    ]));
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.kotlin_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Kotlin"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Build: ${profile.build_tool || "auto"} · JVM target: ${profile.jvm_target || "auto"}`,
        `Etapas: ${[
          profile.descriptor_enabled ? "descriptor" : "",
          profile.kotlinc_enabled ? "kotlinc" : "",
          profile.build_enabled ? "build" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Máximo Kotlin: ${profile.max_kotlin_files || 3000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI kotlin-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.dotnet_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil C#/.NET"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Configuración: ${profile.configuration || "Release"} · máximo .NET: ${profile.max_dotnet_files || 3000}`,
        `Etapas: ${[
          profile.descriptor_enabled ? "descriptor" : "",
          profile.format_enabled ? "format" : "",
          profile.build_enabled ? "build" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI dotnet-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.native_profiles || []) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", "Perfil C/C++"));
    head.append(createElement("span", "memory-status approved", `#${profile.id}`));
    card.append(head, createElement("div", "document-path", profile.project_root));
    card.append(inspectorItemMeta([
      `Etapas: ${[
        profile.descriptor_enabled ? "descriptor" : "",
        profile.c_syntax_enabled ? "c-syntax" : "",
        profile.cpp_syntax_enabled ? "cpp-syntax" : "",
        profile.static_enabled ? "static" : "",
        profile.build_enabled ? "build" : "",
        profile.tests_enabled ? "tests" : "",
      ].filter(Boolean).join(", ") || "ninguna"}`,
      `Compilador: ${profile.compiler || "auto"} · C: ${profile.c_standard || "c17"} · C++: ${profile.cpp_standard || "c++20"}`,
      `Máximo: ${profile.max_native_files || 3000} · exclusiones: ${(profile.exclude_paths || []).join(", ") || "-"}`,
      `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
      "Administración disponible desde CLI native-profile-set/delete.",
    ]));
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.ruby_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Ruby"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Tests: ${profile.test_framework || "auto"} · máximo Ruby: ${profile.max_ruby_files || 3000}`,
        `Etapas: ${[
          profile.descriptor_enabled ? "descriptor" : "",
          profile.bundle_enabled ? "bundle" : "",
          profile.syntax_enabled ? "syntax" : "",
          profile.rubocop_enabled ? "rubocop" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI ruby-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.go_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Go"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Tests: ${profile.test_mode || "auto"} · máximo Go: ${profile.max_go_files || 3000}`,
        `Etapas: ${[
          profile.module_enabled ? "module" : "",
          profile.fmt_enabled ? "fmt" : "",
          profile.vet_enabled ? "vet" : "",
          profile.build_enabled ? "build" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI go-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.rust_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Rust"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Features: ${profile.feature_mode || "default"} · máximo Rust: ${profile.max_rust_files || 3000}`,
        `Etapas: ${[
          profile.manifest_enabled ? "manifest" : "",
          profile.fmt_enabled ? "fmt" : "",
          profile.check_enabled ? "check" : "",
          profile.clippy_enabled ? "clippy" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI rust-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.swift_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Swift"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Configuración: ${profile.configuration || "debug"} · máximo Swift: ${profile.max_swift_files || 3000}`,
        `Etapas: ${[
          profile.manifest_enabled ? "manifest" : "",
          profile.syntax_enabled ? "syntax" : "",
          profile.format_enabled ? "format" : "",
          profile.build_enabled ? "build" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI swift-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.dart_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil Dart/Flutter"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Runner: ${profile.test_runner || "auto"} · máximo Dart: ${profile.max_dart_files || 3000}`,
        `Etapas: ${[
          profile.descriptor_enabled ? "descriptor" : "",
          profile.format_enabled ? "format" : "",
          profile.analyze_enabled ? "analyze" : "",
          profile.tests_enabled ? "tests" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Timeout: ${profile.timeout_seconds || "global"} · salida: ${profile.max_output_chars || "global"}`,
        "Administración disponible desde CLI dart-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  for (const profile of payload.sql_profiles || []) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", "Perfil SQL"),
      createElement("div", "document-path", profile.project_root),
      inspectorItemMeta([
        `Dialecto: ${profile.dialect || "auto"} · máximo SQL: ${profile.max_sql_files || 3000}`,
        `Etapas: ${[
          profile.static_enabled ? "static" : "",
          profile.migrations_enabled ? "migrations" : "",
          profile.schema_enabled ? "schema" : "",
        ].filter(Boolean).join(", ") || "ninguna"}`,
        `Mutaciones: ${profile.allow_mutating_sql ? "permitidas" : "bloqueadas"} · destructivas: ${profile.allow_destructive_migrations ? "permitidas" : "bloqueadas"}`,
        "Administración disponible desde CLI sql-profile-set/delete.",
      ]),
    );
    elements.controlProfiles.append(card);
  }
  if (
    !(payload.profiles || []).length
    && !(payload.web_profiles || []).length
    && !(payload.python_profiles || []).length
    && !(payload.java_profiles || []).length
    && !(payload.kotlin_profiles || []).length
    && !(payload.dotnet_profiles || []).length
    && !(payload.native_profiles || []).length
    && !(payload.ruby_profiles || []).length
    && !(payload.go_profiles || []).length
    && !(payload.rust_profiles || []).length
    && !(payload.swift_profiles || []).length
    && !(payload.dart_profiles || []).length
    && !(payload.sql_profiles || []).length
  ) {
    elements.controlProfiles.append(createElement("div", "inspector-empty", "No hay perfiles guardados."));
  }
}

function renderVerificationItems(container, items, emptyText) {
  container.replaceChildren();
  for (const item of items) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.status || "unknown"));
    head.append(createElement("span", "memory-status approved", item.public_id.slice(0, 8)));
    card.append(head, createElement("div", "document-path", item.project_root));
    const stages = ((item.summary || {}).stages || [])
      .map((stage) => `${stage.name}: ${stage.status}`)
      .join(" · ");
    card.append(inspectorItemMeta([
      `Inicio: ${item.started_at}`,
      `Duración: ${item.duration_ms || "-"} ms`,
      stages || "Sin etapas registradas",
    ]));
    container.append(card);
  }
  if (!items.length) container.append(createElement("div", "inspector-empty", emptyText));
}

function renderControlPhpVerifications() {
  renderVerificationItems(
    elements.controlPhpVerifications,
    state.controlPhpVerifications,
    "No hay verificaciones PHP guardadas.",
  );
}

function renderControlWebVerifications() {
  renderVerificationItems(
    elements.controlWebVerifications,
    state.controlWebVerifications,
    "No hay verificaciones web guardadas.",
  );
}

function renderControlPythonVerifications() {
  renderVerificationItems(
    elements.controlPythonVerifications,
    state.controlPythonVerifications,
    "No hay verificaciones Python guardadas.",
  );
}

function renderControlJavaVerifications() {
  renderVerificationItems(
    elements.controlJavaVerifications,
    state.controlJavaVerifications,
    "No hay verificaciones Java guardadas.",
  );
}

function renderControlKotlinVerifications() {
  renderVerificationItems(
    elements.controlKotlinVerifications,
    state.controlKotlinVerifications,
    "No hay verificaciones Kotlin guardadas.",
  );
}

function renderControlDotnetVerifications() {
  renderVerificationItems(
    elements.controlDotnetVerifications,
    state.controlDotnetVerifications,
    "No hay verificaciones C#/.NET guardadas.",
  );
}

function renderControlNativeVerifications() {
  renderVerificationItems(
    elements.controlNativeVerifications,
    state.controlNativeVerifications,
    "No hay verificaciones C/C++ guardadas.",
  );
}

function renderControlRubyVerifications() {
  renderVerificationItems(
    elements.controlRubyVerifications,
    state.controlRubyVerifications,
    "No hay verificaciones Ruby guardadas.",
  );
}

function renderControlGoVerifications() {
  renderVerificationItems(
    elements.controlGoVerifications,
    state.controlGoVerifications,
    "No hay verificaciones Go guardadas.",
  );
}

function renderControlRustVerifications() {
  renderVerificationItems(
    elements.controlRustVerifications,
    state.controlRustVerifications,
    "No hay verificaciones Rust guardadas.",
  );
}

function renderControlSwiftVerifications() {
  renderVerificationItems(
    elements.controlSwiftVerifications,
    state.controlSwiftVerifications,
    "No hay verificaciones Swift guardadas.",
  );
}

function renderControlDartVerifications() {
  renderVerificationItems(
    elements.controlDartVerifications,
    state.controlDartVerifications,
    "No hay verificaciones Dart/Flutter guardadas.",
  );
}

function renderControlSqlVerifications() {
  renderVerificationItems(
    elements.controlSqlVerifications,
    state.controlSqlVerifications,
    "No hay verificaciones SQL guardadas.",
  );
}

function renderControlActionRuns() {
  elements.controlActionRuns.replaceChildren();
  for (const item of state.controlActionRuns) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.status || "unknown"));
    head.append(createElement("span", "memory-status approved", item.plan_id || item.public_id.slice(0, 8)));
    const steps = ((item.plan || {}).steps || [])
      .map((step) => step.skill_name)
      .join(" → ");
    card.append(
      head,
      createElement("strong", "", (item.plan || {}).summary || "Plan supervisado"),
      inspectorItemMeta([
        `Fuente: ${item.source || "desconocida"}`,
        `Inicio: ${item.started_at}`,
        `Duración: ${item.duration_ms || "-"} ms`,
        steps || "Sin pasos registrados",
      ]),
    );
    elements.controlActionRuns.append(card);
  }
  if (!state.controlActionRuns.length) {
    elements.controlActionRuns.append(
      createElement("div", "inspector-empty", "No hay planes supervisados ejecutados."),
    );
  }
}

function renderControlChangeProposals() {
  elements.controlChangeProposals.replaceChildren();
  for (const item of state.controlChangeProposals) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.status || "unknown"));
    head.append(createElement("span", "memory-status approved", item.public_id.slice(0, 10)));
    const proposal = item.proposal || {};
    const files = (proposal.changes || [])
      .map((change) => change.relative_path)
      .join(", ");
    card.append(
      head,
      createElement("strong", "", proposal.summary || "Propuesta de cambios"),
      inspectorItemMeta([
        `Proyecto: ${item.project_root}`,
        `Fuente: ${item.source || "desconocida"}`,
        `Creada: ${item.created_at}`,
        files || "Sin archivos registrados",
      ]),
    );
    if (item.diff) {
      const diff = createElement("pre", "document-preview", item.diff);
      card.append(diff);
    }
    elements.controlChangeProposals.append(card);
  }
  if (!state.controlChangeProposals.length) {
    elements.controlChangeProposals.append(
      createElement("div", "inspector-empty", "No hay propuestas de cambios guardadas."),
    );
  }
}

function renderControlValidationCycles() {
  elements.controlValidationCycles.replaceChildren();
  for (const item of state.controlValidationCycles) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.status || "unknown"));
    head.append(createElement("span", "memory-status approved", item.public_id.slice(0, 10)));
    const steps = ((item.plan || {}).steps || [])
      .map((step) => step.skill_name)
      .join(" → ");
    card.append(
      head,
      createElement("strong", "", "Ciclo supervisado de validación y reparación"),
      inspectorItemMeta([
        `Cambio origen: ${item.source_change_proposal_id}`,
        `Proyecto: ${item.project_root}`,
        `Ejecución: ${item.validation_run_id || "pendiente"}`,
        `Reparación: ${item.repair_proposal_id || "ninguna"}`,
        steps || "Sin pasos registrados",
      ]),
    );
    elements.controlValidationCycles.append(card);
  }
  if (!state.controlValidationCycles.length) {
    elements.controlValidationCycles.append(
      createElement("div", "inspector-empty", "No hay ciclos de validación y reparación."),
    );
  }
}

function renderControlEthics() {
  elements.controlEthics.replaceChildren();
  const payload = state.controlEthics || {};
  const status = payload.status || {};
  const summary = createElement("article", "control-card");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", "núcleo constitucional"));
  head.append(createElement("span", "memory-status approved", "activo"));
  summary.append(
    head,
    createElement("strong", "", "Ética profesional y humana"),
    inspectorItemMeta([
      `Desactivable: ${status.core_disableable ? "sí" : "no"}`,
      `Consejo proactivo: ${status.proactive_advice ? "sí" : "no"}`,
      `Denuncia automática: ${status.automatic_reporting ? "sí" : "no"}`,
      `Ataques de red: ${status.network_attacks ? "sí" : "no"}`,
      `Revisiones registradas: ${(payload.reviews || []).length}`,
    ]),
  );
  elements.controlEthics.append(summary);
  for (const principle of (payload.principles || [])) {
    const card = createElement("article", "control-card");
    card.append(
      createElement("strong", "", principle.title || principle.id),
      createElement("p", "", principle.description || ""),
    );
    elements.controlEthics.append(card);
  }
}

function renderControlDevelopmentSessions() {
  elements.controlDevelopmentSessions.replaceChildren();
  for (const item of state.controlDevelopmentSessions) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.status || "unknown"));
    head.append(createElement("span", "memory-status approved", item.public_id.slice(0, 10)));
    card.append(
      head,
      createElement("strong", "", item.objective || "Sesión de desarrollo"),
      inspectorItemMeta([
        `Proyecto: ${item.project_root}`,
        `Cambio raíz: ${item.root_change_proposal_id}`,
        `Cambio actual: ${item.current_change_proposal_id}`,
        `Ciclo actual: ${item.current_validation_cycle_id || "ninguno"}`,
        `Siguiente: ${(item.guidance?.actions || [])[0]?.label || "sin acción pendiente"}`,
        `Actualizada: ${item.updated_at}`,
      ]),
    );
    const actions = item.guidance?.actions || [];
    if (actions.length) {
      const next = createElement("div", "document-path");
      next.append(createElement("strong", "", "Acciones sugeridas · no ejecutadas"));
      for (const action of actions.slice(0, 3)) {
        next.append(createElement("code", "", action.command || action.label));
      }
      card.append(next);
    }
    elements.controlDevelopmentSessions.append(card);
  }
  if (!state.controlDevelopmentSessions.length) {
    elements.controlDevelopmentSessions.append(
      createElement("div", "inspector-empty", "No hay sesiones de desarrollo guardadas."),
    );
  }
}

function renderControlPackages() {
  elements.controlPackages.replaceChildren();
  for (const item of state.controlPackages) {
    const card = createElement("article", "control-card");
    const head = createElement("div", "memory-item-head");
    head.append(createElement("span", "memory-kind", item.tier || "optional"));
    head.append(createElement(
      "span",
      `memory-status ${item.enabled ? "approved" : "rejected"}`,
      item.enabled ? "activo" : "inactivo",
    ));
    card.append(
      head,
      createElement("strong", "", `${item.name} · ${item.version}`),
      createElement("div", "document-path", item.package_id),
      inspectorItemMeta([
        `Dominio: ${item.domain}`,
        `Licencia: ${item.license_id}`,
        `Fuentes: ${item.source_count || (item.metadata || {}).source_count || 0}`,
        "Las fuentes instaladas comienzan como no revisadas.",
      ]),
    );
    elements.controlPackages.append(card);
  }
  if (!state.controlPackages.length) {
    elements.controlPackages.append(
      createElement("div", "inspector-empty", "No hay paquetes opcionales instalados."),
    );
  }
}

async function installAlexandriaPackage(event) {
  event.preventDefault();
  const path = elements.packageInstallPath.value.trim();
  if (!path) return;
  const accepted = await confirmElyndraAction(
    "Instalar paquete de Alejandría",
    `¿Validar e instalar el paquete local ${path}? No se usará red ni se ejecutará código.`,
    "Instalar paquete",
  );
  if (!accepted) return;
  try {
    await api("/api/control/alexandria-packages/install", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    elements.packageInstallForm.reset();
    await loadControl();
    showNotice("Paquete de Alejandría instalado.");
  } catch (error) {
    showError(error.message);
  }
}

async function createAlexandriaPackage(event) {
  event.preventDefault();
  const sources = elements.packageCreateSources.value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
  const payload = {
    destination: elements.packageCreateDestination.value.trim(),
    package_id: elements.packageCreateId.value.trim(),
    name: elements.packageCreateName.value.trim(),
    version: elements.packageCreateVersion.value.trim(),
    domain: elements.packageCreateDomain.value.trim(),
    language: elements.packageCreateLanguage.value.trim() || "es",
    license_id: elements.packageCreateLicense.value.trim(),
    tier: elements.packageCreateTier.value,
    sources,
    tags: elements.packageCreateTags.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  };
  if (!payload.destination || !payload.package_id || !payload.name || !sources.length) return;
  const accepted = await confirmElyndraAction(
    "Crear paquete de Alejandría",
    `¿Copiar ${sources.length} fuente(s) y crear ${payload.package_id}?`,
    "Crear paquete",
  );
  if (!accepted) return;
  try {
    await api("/api/control/alexandria-packages/create", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    elements.packageCreateForm.reset();
    elements.packageCreateLanguage.value = "es";
    await loadControl();
    showNotice("Paquete local creado y verificado.");
  } catch (error) {
    showError(error.message);
  }
}

async function exportAlexandriaPackage(event) {
  event.preventDefault();
  const packageId = elements.packageExportId.value.trim();
  const destination = elements.packageExportDestination.value.trim();
  if (!packageId || !destination) return;
  const accepted = await confirmElyndraAction(
    "Exportar paquete de Alejandría",
    `¿Exportar ${packageId} hacia ${destination}?`,
    "Exportar paquete",
  );
  if (!accepted) return;
  try {
    await api("/api/control/alexandria-packages/export", {
      method: "POST",
      body: JSON.stringify({ package_id: packageId, destination }),
    });
    elements.packageExportForm.reset();
    await loadControl();
    showNotice("Paquete exportado.");
  } catch (error) {
    showError(error.message);
  }
}

function renderControlAudit() {
  elements.controlAudit.replaceChildren();
  for (const item of state.controlAudit) elements.controlAudit.append(renderAuditItem(item));
  if (!state.controlAudit.length) {
    elements.controlAudit.append(createElement("div", "inspector-empty", "No hay eventos para este filtro."));
  }
}

async function loadControlAudit() {
  const params = new URLSearchParams();
  const query = elements.controlAuditSearch.value.trim();
  const action = elements.controlAuditAction.value.trim();
  const outcome = elements.controlAuditOutcome.value;
  if (query) params.set("q", query);
  if (action) params.set("action", action);
  if (outcome) params.set("outcome", outcome);
  const payload = await api(`/api/control/audit${params.size ? `?${params}` : ""}`);
  state.controlAudit = payload.items || [];
  renderControlAudit();
}

async function loadControl() {
  const [
    overview,
    projects,
    phpVerifications,
    webVerifications,
    pythonVerifications,
    javaVerifications,
    kotlinVerifications,
    dotnetVerifications,
    nativeVerifications,
    rubyVerifications,
    goVerifications,
    rustVerifications,
    swiftVerifications,
    dartVerifications,
    sqlVerifications,
    actionRuns,
    changeProposals,
    validationCycles,
    developmentSessions,
    ethics,
    packages,
  ] = await Promise.all([
    api("/api/control/overview"),
    api("/api/control/projects"),
    api("/api/control/php-verifications"),
    api("/api/control/web-verifications"),
    api("/api/control/python-verifications"),
    api("/api/control/java-verifications"),
    api("/api/control/kotlin-verifications"),
    api("/api/control/dotnet-verifications"),
    api("/api/control/native-verifications"),
    api("/api/control/ruby-verifications"),
    api("/api/control/go-verifications"),
    api("/api/control/rust-verifications"),
    api("/api/control/swift-verifications"),
    api("/api/control/dart-verifications"),
    api("/api/control/sql-verifications"),
    api("/api/control/action-runs"),
    api("/api/control/change-proposals"),
    api("/api/control/validation-cycles"),
    api("/api/control/development-sessions"),
    api("/api/control/ethics"),
    api("/api/control/alexandria-packages"),
  ]);
  state.controlOverview = overview;
  state.controlProjects = projects;
  state.controlPhpVerifications = phpVerifications.items || [];
  state.controlWebVerifications = webVerifications.items || [];
  state.controlPythonVerifications = pythonVerifications.items || [];
  state.controlJavaVerifications = javaVerifications.items || [];
  state.controlKotlinVerifications = kotlinVerifications.items || [];
  state.controlDotnetVerifications = dotnetVerifications.items || [];
  state.controlNativeVerifications = nativeVerifications.items || [];
  state.controlRubyVerifications = rubyVerifications.items || [];
  state.controlGoVerifications = goVerifications.items || [];
  state.controlRustVerifications = rustVerifications.items || [];
  state.controlSwiftVerifications = swiftVerifications.items || [];
  state.controlDartVerifications = dartVerifications.items || [];
  state.controlSqlVerifications = sqlVerifications.items || [];
  state.controlActionRuns = actionRuns.items || [];
  state.controlChangeProposals = changeProposals.items || [];
  state.controlValidationCycles = validationCycles.items || [];
  state.controlDevelopmentSessions = developmentSessions.items || [];
  state.controlEthics = ethics;
  state.controlPackages = packages.items || [];
  renderControlOverview();
  renderControlProjects();
  renderControlPhpVerifications();
  renderControlWebVerifications();
  renderControlPythonVerifications();
  renderControlJavaVerifications();
  renderControlKotlinVerifications();
  renderControlDotnetVerifications();
  renderControlNativeVerifications();
  renderControlRubyVerifications();
  renderControlGoVerifications();
  renderControlRustVerifications();
  renderControlSwiftVerifications();
  renderControlDartVerifications();
  renderControlSqlVerifications();
  renderControlActionRuns();
  renderControlChangeProposals();
  renderControlValidationCycles();
  renderControlDevelopmentSessions();
  renderControlEthics();
  renderControlPackages();
  await loadControlAudit();
}

async function openControl({ updateUrl = true } = {}) {
  showControlWorkspace();
  if (updateUrl && window.location.pathname !== "/control") {
    window.history.pushState({ control: true }, "", "/control");
  }
  try {
    await loadControl();
  } catch (error) {
    showError(error.message);
  }
  closeSidebar();
}

function clearProfileForm() {
  elements.phpProfileForm.reset();
  elements.profileProjectRoot.value = "";
  elements.profileComposerEnabled.checked = true;
  elements.profileSyntaxEnabled.checked = true;
  elements.profilePhpstanEnabled.checked = true;
  elements.profilePhpunitEnabled.checked = true;
}

function fillProfileForm(profile) {
  elements.profileProjectRoot.value = profile.project_root || "";
  elements.profilePhpstanConfig.value = profile.phpstan_config || "";
  elements.profilePhpstanLevel.value = profile.phpstan_level || "";
  elements.profilePhpunitConfig.value = profile.phpunit_config || "";
  elements.profilePhpunitTestsuite.value = profile.phpunit_testsuite || "";
  elements.profileTimeout.value = profile.timeout_seconds || "";
  elements.profileOutputLimit.value = profile.max_output_chars || "";
  elements.profileComposerStrict.checked = Boolean(profile.composer_strict);
  elements.profileComposerEnabled.checked = profile.composer_enabled !== false;
  elements.profileSyntaxEnabled.checked = profile.syntax_scan_enabled !== false;
  elements.profilePhpstanEnabled.checked = profile.phpstan_enabled !== false;
  elements.profilePhpunitEnabled.checked = profile.phpunit_enabled !== false;
  elements.profileFailFast.checked = Boolean(profile.fail_fast);
  elements.profileRequireTools.checked = Boolean(profile.require_tools);
  elements.profileMaxPhpFiles.value = profile.max_php_files || 2000;
  elements.profileExcludePaths.value = (profile.exclude_paths || []).join(", ");
  elements.profileProjectRoot.focus();
}

async function trustProject(event) {
  event.preventDefault();
  const path = elements.trustProjectPath.value.trim();
  if (!path) return;
  const accepted = await confirmElyndraAction(
    "Confiar en proyecto",
    `¿Conceder autorización persistente únicamente al proyecto ${path}?`,
    "Confiar",
  );
  if (!accepted) return;
  try {
    await api("/api/control/trusted-projects", {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    elements.trustProjectForm.reset();
    await loadControl();
    showNotice("Proyecto confiable guardado localmente.");
  } catch (error) {
    showError(error.message);
  }
}

async function removeTrustedProject(path) {
  const accepted = await confirmElyndraAction(
    "Revocar confianza",
    `¿Revocar la autorización persistente de ${path}?`,
    "Revocar",
  );
  if (!accepted) return;
  try {
    await api(`/api/control/trusted-projects?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    await loadControl();
    showNotice("Confianza revocada.");
  } catch (error) {
    showError(error.message);
  }
}

async function savePhpProfile(event) {
  event.preventDefault();
  const projectRoot = elements.profileProjectRoot.value.trim();
  if (!projectRoot) return;
  const accepted = await confirmElyndraAction(
    "Guardar perfil PHP",
    `¿Guardar parámetros seguros para ${projectRoot}? Esto no ejecutará herramientas.`,
    "Guardar perfil",
  );
  if (!accepted) return;
  const payload = {
    project_root: projectRoot,
    phpstan_config: elements.profilePhpstanConfig.value.trim(),
    phpstan_level: elements.profilePhpstanLevel.value,
    phpunit_config: elements.profilePhpunitConfig.value.trim(),
    phpunit_testsuite: elements.profilePhpunitTestsuite.value.trim(),
    composer_strict: elements.profileComposerStrict.checked,
    composer_enabled: elements.profileComposerEnabled.checked,
    syntax_scan_enabled: elements.profileSyntaxEnabled.checked,
    phpstan_enabled: elements.profilePhpstanEnabled.checked,
    phpunit_enabled: elements.profilePhpunitEnabled.checked,
    fail_fast: elements.profileFailFast.checked,
    require_tools: elements.profileRequireTools.checked,
    max_php_files: elements.profileMaxPhpFiles.value || null,
    exclude_paths: elements.profileExcludePaths.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    timeout_seconds: elements.profileTimeout.value || null,
    max_output_chars: elements.profileOutputLimit.value || null,
  };
  try {
    await api("/api/control/php-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    clearProfileForm();
    await loadControl();
    showNotice("Perfil PHP guardado.");
  } catch (error) {
    showError(error.message);
  }
}

async function removePhpProfile(path) {
  const accepted = await confirmElyndraAction(
    "Eliminar perfil PHP",
    `¿Eliminar los parámetros guardados para ${path}?`,
    "Eliminar perfil",
  );
  if (!accepted) return;
  try {
    await api(`/api/control/php-profiles?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    await loadControl();
    showNotice("Perfil PHP eliminado.");
  } catch (error) {
    showError(error.message);
  }
}

function clearPythonProfileForm() {
  elements.pythonProfileForm.reset();
  elements.pythonProfileProjectRoot.value = "";
  elements.pythonProfilePyprojectEnabled.checked = true;
  elements.pythonProfileCompileEnabled.checked = true;
  elements.pythonProfileRuffEnabled.checked = true;
  elements.pythonProfileMypyEnabled.checked = true;
  elements.pythonProfilePytestEnabled.checked = true;
}

function fillPythonProfileForm(profile) {
  elements.pythonProfileProjectRoot.value = profile.project_root || "";
  elements.pythonProfileRuffConfig.value = profile.ruff_config || "";
  elements.pythonProfileMypyConfig.value = profile.mypy_config || "";
  elements.pythonProfilePytestPath.value = profile.pytest_path || "";
  elements.pythonProfileTimeout.value = profile.timeout_seconds || "";
  elements.pythonProfileOutputLimit.value = profile.max_output_chars || "";
  elements.pythonProfileMaxFiles.value = profile.max_python_files || 3000;
  elements.pythonProfileExcludePaths.value = (profile.exclude_paths || []).join(", ");
  elements.pythonProfilePyprojectEnabled.checked = profile.pyproject_enabled !== false;
  elements.pythonProfileCompileEnabled.checked = profile.compile_enabled !== false;
  elements.pythonProfileRuffEnabled.checked = profile.ruff_enabled !== false;
  elements.pythonProfileMypyEnabled.checked = profile.mypy_enabled !== false;
  elements.pythonProfilePytestEnabled.checked = profile.pytest_enabled !== false;
  elements.pythonProfileFailFast.checked = Boolean(profile.fail_fast);
  elements.pythonProfileRequireTools.checked = Boolean(profile.require_tools);
  elements.pythonProfileProjectRoot.focus();
}

async function savePythonProfile(event) {
  event.preventDefault();
  const projectRoot = elements.pythonProfileProjectRoot.value.trim();
  if (!projectRoot) return;
  const accepted = await confirmElyndraAction(
    "Guardar perfil Python",
    `¿Guardar parámetros seguros para ${projectRoot}? Esto no ejecutará herramientas.`,
    "Guardar perfil",
  );
  if (!accepted) return;
  const payload = {
    project_root: projectRoot,
    ruff_config: elements.pythonProfileRuffConfig.value.trim(),
    mypy_config: elements.pythonProfileMypyConfig.value.trim(),
    pytest_path: elements.pythonProfilePytestPath.value.trim(),
    pyproject_enabled: elements.pythonProfilePyprojectEnabled.checked,
    compile_enabled: elements.pythonProfileCompileEnabled.checked,
    ruff_enabled: elements.pythonProfileRuffEnabled.checked,
    mypy_enabled: elements.pythonProfileMypyEnabled.checked,
    pytest_enabled: elements.pythonProfilePytestEnabled.checked,
    fail_fast: elements.pythonProfileFailFast.checked,
    require_tools: elements.pythonProfileRequireTools.checked,
    max_python_files: elements.pythonProfileMaxFiles.value || null,
    exclude_paths: elements.pythonProfileExcludePaths.value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean),
    timeout_seconds: elements.pythonProfileTimeout.value || null,
    max_output_chars: elements.pythonProfileOutputLimit.value || null,
  };
  try {
    await api("/api/control/python-profiles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    clearPythonProfileForm();
    await loadControl();
    showNotice("Perfil Python guardado.");
  } catch (error) {
    showError(error.message);
  }
}

async function removePythonProfile(path) {
  const accepted = await confirmElyndraAction(
    "Eliminar perfil Python",
    `¿Eliminar los parámetros guardados para ${path}?`,
    "Eliminar perfil",
  );
  if (!accepted) return;
  try {
    await api(`/api/control/python-profiles?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    await loadControl();
    showNotice("Perfil Python eliminado.");
  } catch (error) {
    showError(error.message);
  }
}

function inspectorPath(view = state.inspectorView) {
  const paths = {
    memories: "/api/inspector/memories",
    episodes: "/api/inspector/episodes",
    proposals: "/api/inspector/proposals",
    corrections: "/api/inspector/corrections",
    documents: "/api/inspector/documents",
    attachments: "/api/inspector/attachments",
    archives: "/api/inspector/archives",
    audit: "/api/inspector/audit",
  };
  return paths[view] || null;
}

function inspectorLabel(value) {
  const labels = {
    fact: "Hecho",
    preference: "Preferencia",
    rule: "Regla",
    decision: "Decisión",
    pending: "Pendiente",
    outcome: "Resultado",
    problem: "Problema",
    correction: "Corrección",
  };
  return labels[value] || value || "Sin tipo";
}

function renderOverviewCards(overview) {
  const counts = overview?.counts || {};
  const cards = [
    ["Memorias", counts.memories || 0, false],
    ["Episodios", counts.episodes || 0, false],
    ["Propuestas pendientes", counts.proposals || 0, (counts.proposals || 0) > 0],
    ["Correcciones", counts.corrections || 0, false],
    ["Documentos", counts.documents || 0, false],
    ["Adjuntos", counts.attachments || 0, false],
    ["Archivos fríos", counts.archives || 0, false],
    ["Eventos auditados", counts.audit_events || 0, false],
    ["Base local", formatBytes(overview?.database?.size_bytes || 0), false],
  ];
  elements.overviewGrid.replaceChildren();
  for (const [label, value, attention] of cards) {
    const card = createElement("article", `overview-card${attention ? " attention" : ""}`);
    card.append(createElement("strong", "", String(value)), createElement("span", "", label));
    elements.overviewGrid.append(card);
  }
  elements.memoryBadge.textContent = String(counts.proposals || 0);
  elements.memoryBadge.hidden = !(counts.proposals > 0);
}

function inspectorItemMeta(parts) {
  const foot = createElement("div", "memory-item-foot");
  for (const part of parts.filter(Boolean)) foot.append(createElement("span", "", String(part)));
  return foot;
}

function inspectorActions(actions) {
  const row = createElement("div", "memory-item-actions");
  for (const action of actions) {
    const button = createElement("button", `item-button ${action.className || ""}`.trim(), action.label);
    button.type = "button";
    button.addEventListener("click", action.run);
    row.append(button);
  }
  return row;
}

function renderMemoryItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", inspectorLabel(item.kind)));
  head.append(createElement("span", "memory-status approved", `#${item.id}`));
  card.append(head, createElement("p", "memory-item-content", item.content));
  card.append(inspectorItemMeta([
    item.project ? `Proyecto: ${item.project}` : "Sin proyecto",
    `Fuente: ${item.source || "owner"}`,
    `Confianza: ${Math.round(Number(item.confidence || 0) * 100)}%`,
    formatRelativeDate(item.updated_at),
  ]));
  card.append(inspectorActions([
    { label: "Editar", run: () => editInspectorItem("memories", item) },
    { label: "Olvidar", className: "danger", run: () => forgetInspectorItem("memories", item) },
  ]));
  return card;
}

function renderEpisodeItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", inspectorLabel(item.kind)));
  head.append(createElement("span", "memory-status approved", `Importancia ${item.importance}`));
  card.append(head, createElement("p", "memory-item-content", item.content));
  card.append(inspectorItemMeta([
    item.chat_title || item.chat_public_id,
    item.project ? `Proyecto: ${item.project}` : "",
    formatRelativeDate(item.updated_at),
  ]));
  card.append(inspectorActions([
    { label: "Abrir chat", run: () => openChat(item.chat_public_id) },
    { label: "Editar", run: () => editInspectorItem("episodes", item) },
    { label: "Olvidar", className: "danger", run: () => forgetInspectorItem("episodes", item) },
  ]));
  return card;
}

function renderProposalItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", inspectorLabel(item.kind)));
  head.append(createElement("span", `memory-status ${item.status}`, item.status));
  card.append(head, createElement("p", "memory-item-content", item.content));
  card.append(createElement("div", "inspector-note", `Motivo: ${item.reason}`));
  card.append(inspectorItemMeta([
    item.chat_title || "Sin chat asociado",
    `Confianza: ${Math.round(Number(item.confidence || 0) * 100)}%`,
    formatRelativeDate(item.updated_at),
  ]));
  if (item.status === "pending") {
    card.append(inspectorActions([
      { label: "Editar", run: () => editInspectorItem("proposals", item) },
      { label: "Aprobar", className: "primary", run: () => reviewProposal(item.id, "approve") },
      { label: "Rechazar", className: "danger", run: () => reviewProposal(item.id, "reject") },
    ]));
  }
  return card;
}

function renderCorrectionItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", "Corrección"));
  head.append(createElement("span", "memory-status approved", `#${item.id}`));
  card.append(head);
  card.append(createElement("div", "inspector-note", `Pregunta: ${item.user_text}`));
  card.append(createElement("p", "memory-item-content", `Respuesta original: ${item.original_response}`));
  card.append(createElement("p", "memory-item-content", `Corrección: ${item.corrected_response}`));
  card.append(inspectorItemMeta([item.chat_title || "Sin chat", formatRelativeDate(item.updated_at)]));
  return card;
}

function renderDocumentItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", item.source_type || "Documento"));
  head.append(createElement("span", "memory-status approved", `${item.chunks} fragmentos`));
  card.append(head, createElement("p", "memory-item-content", item.title));
  card.append(createElement("div", "document-path", item.source_path));
  card.append(inspectorItemMeta([
    item.project ? `Proyecto: ${item.project}` : "Sin proyecto",
    formatBytes(Number(item.size_bytes || 0)),
    formatRelativeDate(item.updated_at),
  ]));
  card.append(createElement(
    "div",
    "inspector-note",
    "Indexado para recuperación local. Haber leído el contenido no significa que su sintaxis o exactitud haya sido validada.",
  ));
  return card;
}

function renderAttachmentItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", attachmentIcon(item)));
  head.append(
    createElement(
      "span",
      `memory-status ${item.validation_status}`,
      attachmentStatusLabel(item.validation_status),
    ),
  );
  card.append(head, createElement("p", "memory-item-content", item.filename));
  card.append(
    inspectorItemMeta([
      item.chat_title || item.chat_id || "Chat no disponible",
      formatBytes(Number(item.size_bytes || 0)),
      attachmentStatusLabel(item.extraction_status),
      item.processor || "Sin procesador",
    ]),
  );
  const messages = item.diagnostics?.messages || [];
  if (messages.length) {
    card.append(createElement("div", "inspector-note", messages.join(" ")));
  }
  card.append(
    inspectorActions([
      {
        label: "Abrir chat",
        run: () => item.chat_id && openChat(item.chat_id),
      },
      {
        label: "Reprocesar",
        className: "primary",
        run: () => reprocessAttachment(item.id),
      },
    ]),
  );
  return card;
}

async function reprocessAttachment(attachmentId) {
  try {
    await api(`/api/attachments/${encodeURIComponent(attachmentId)}/reprocess`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshInspector();
    showNotice("Adjunto reprocesado localmente.");
  } catch (error) {
    showError(error.message);
  }
}

function renderArchiveItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", "Archivo frío"));
  head.append(createElement("span", "memory-status approved", `${item.turn_count} turnos`));
  card.append(head, createElement("p", "memory-item-content", item.chat_title || item.chat_public_id));
  card.append(createElement("div", "document-path", item.path));
  card.append(inspectorItemMeta([
    formatBytes(Number(item.size_bytes || 0)),
    `SHA-256: ${String(item.sha256 || "").slice(0, 16)}…`,
    formatRelativeDate(item.created_at),
  ]));
  return card;
}

function renderAuditItem(item) {
  const card = createElement("article", "memory-item");
  const head = createElement("div", "memory-item-head");
  head.append(createElement("span", "memory-kind", item.action));
  head.append(createElement("span", `memory-outcome ${item.outcome}`, item.outcome));
  card.append(head);
  if (item.target) card.append(createElement("p", "memory-item-content", `Objetivo: ${item.target}`));
  const details = item.details && Object.keys(item.details).length
    ? JSON.stringify(item.details, null, 2)
    : "Sin detalles adicionales.";
  card.append(createElement("pre", "audit-details", details));
  card.append(inspectorItemMeta([item.actor, formatRelativeDate(item.created_at)]));
  return card;
}

function renderInspectorOverview() {
  elements.inspectorToolbar.hidden = true;
  const content = createElement("div", "inspector-section");
  const head = createElement("div", "inspector-section-head");
  const title = createElement("div");
  title.append(createElement("h3", "", "Estado de la memoria"));
  title.append(createElement("p", "", "Resumen de lo almacenado en disco y disponible para recuperación selectiva."));
  head.append(title);
  content.append(head);
  content.append(createElement(
    "div",
    "inspector-note",
    "La RAM conserva solo el contexto activo. Memorias, episodios, documentos y transcripciones permanecen en disco hasta que una consulta los necesita.",
  ));
  const database = createElement("div", "database-card");
  database.append(
    createElement("strong", "", "Base privada local"),
    createElement("code", "", state.inspectorOverview?.database?.path || ""),
    createElement("span", "memory-item-foot", `Tamaño actual: ${formatBytes(state.inspectorOverview?.database?.size_bytes || 0)}`),
  );
  content.append(database);
  elements.inspectorContent.replaceChildren(content);
}

function renderInspectorItems() {
  if (state.inspectorView === "overview") {
    renderInspectorOverview();
    return;
  }
  elements.inspectorToolbar.hidden = false;
  elements.inspectorContent.replaceChildren();
  if (!state.inspectorItems.length) {
    elements.inspectorContent.append(createElement("div", "inspector-empty", "No hay elementos para mostrar."));
    return;
  }
  const renderers = {
    memories: renderMemoryItem,
    episodes: renderEpisodeItem,
    proposals: renderProposalItem,
    corrections: renderCorrectionItem,
    documents: renderDocumentItem,
    attachments: renderAttachmentItem,
    archives: renderArchiveItem,
    audit: renderAuditItem,
  };
  const renderer = renderers[state.inspectorView];
  for (const item of state.inspectorItems) elements.inspectorContent.append(renderer(item));
}

function configureInspectorFilter() {
  const options = {
    episodes: [
      ["", "Todos los episodios"],
      ["decision", "Decisiones"],
      ["pending", "Pendientes"],
      ["outcome", "Resultados"],
      ["problem", "Problemas"],
      ["correction", "Correcciones"],
    ],
    proposals: [
      ["pending", "Pendientes"],
      ["approved", "Aprobadas"],
      ["rejected", "Rechazadas"],
      ["all", "Todas"],
    ],
  };
  const values = options[state.inspectorView] || [];
  elements.inspectorFilter.replaceChildren();
  elements.inspectorFilter.hidden = !values.length;
  for (const [value, label] of values) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    elements.inspectorFilter.append(option);
  }
}

async function loadInspectorOverview() {
  state.inspectorOverview = await api("/api/inspector/overview");
  renderOverviewCards(state.inspectorOverview);
}

async function loadInspectorView(view = state.inspectorView) {
  state.inspectorView = view;
  for (const button of elements.inspectorTabs.querySelectorAll("button[data-view]")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  configureInspectorFilter();
  if (view === "overview") {
    renderInspectorOverview();
    return;
  }
  elements.inspectorContent.replaceChildren(createElement("div", "inspector-loading", "Cargando memoria local…"));
  const path = inspectorPath(view);
  const params = new URLSearchParams();
  const query = elements.inspectorSearch.value.trim();
  if (query && ["memories", "episodes"].includes(view)) params.set("q", query);
  if (view === "episodes" && elements.inspectorFilter.value) params.set("kind", elements.inspectorFilter.value);
  if (view === "proposals") params.set("status", elements.inspectorFilter.value || "pending");
  const payload = await api(`${path}${params.size ? `?${params}` : ""}`);
  state.inspectorItems = payload.items || [];
  if (query && !["memories", "episodes"].includes(view)) {
    const folded = query.toLocaleLowerCase();
    state.inspectorItems = state.inspectorItems.filter((item) => JSON.stringify(item).toLocaleLowerCase().includes(folded));
  }
  renderInspectorItems();
}

async function openMemoryInspector({ updateUrl = true } = {}) {
  showInspectorWorkspace();
  if (updateUrl && window.location.pathname !== "/memory") window.history.pushState({ memory: true }, "", "/memory");
  try {
    await loadInspectorOverview();
    await loadInspectorView(state.inspectorView);
  } catch (error) {
    showError(error.message);
  }
  closeSidebar();
}

async function refreshInspector() {
  await loadInspectorOverview();
  await loadInspectorView(state.inspectorView);
}

function confirmElyndraAction(title, text, confirmLabel = "Confirmar") {
  return new Promise((resolve) => {
    elements.deleteDialogTitle.textContent = title;
    elements.deleteDialogText.textContent = text;
    elements.deleteConfirm.textContent = confirmLabel;
    const onClose = () => resolve(elements.deleteDialog.returnValue === "confirm");
    elements.deleteDialog.addEventListener("close", onClose, { once: true });
    elements.deleteDialog.showModal();
  });
}

function editElyndraText(title, current) {
  return new Promise((resolve) => {
    elements.editDialogTitle.textContent = title;
    elements.editDialogContent.value = current;
    const onClose = () => {
      const accepted = elements.editDialog.returnValue === "confirm";
      resolve(accepted ? elements.editDialogContent.value.trim() : null);
    };
    elements.editDialog.addEventListener("close", onClose, { once: true });
    elements.editDialog.showModal();
    elements.editDialogContent.focus();
    elements.editDialogContent.select();
  });
}

async function editInspectorItem(resource, item) {
  const content = await editElyndraText("Editar memoria", item.content);
  if (!content || content === item.content) return;
  try {
    await api(`/api/inspector/${resource}/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({ content, kind: item.kind, project: item.project }),
    });
    await refreshInspector();
    showNotice("Cambio guardado localmente.");
  } catch (error) {
    showError(error.message);
  }
}

async function forgetInspectorItem(resource, item) {
  const accepted = await confirmElyndraAction(
    "Olvidar elemento",
    `¿Olvidar este elemento? ${item.content}`,
    "Olvidar",
  );
  if (!accepted) return;
  try {
    await api(`/api/inspector/${resource}/${item.id}`, { method: "DELETE" });
    await refreshInspector();
    showNotice("Elemento olvidado.");
  } catch (error) {
    showError(error.message);
  }
}

async function reviewProposal(proposalId, action) {
  const wording = action === "approve" ? "aprobar" : "rechazar";
  const accepted = await confirmElyndraAction(
    `${wording[0].toUpperCase()}${wording.slice(1)} propuesta`,
    `¿Deseas ${wording} esta propuesta de memoria?`,
    wording === "aprobar" ? "Aprobar memoria" : "Rechazar propuesta",
  );
  if (!accepted) return;
  try {
    await api(`/api/inspector/proposals/${proposalId}/${action}`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await refreshInspector();
    showNotice(action === "approve" ? "Memoria aprobada." : "Propuesta rechazada.");
  } catch (error) {
    showError(error.message);
  }
}

function renderAlexandriaOverview() {
  const overview = state.alexandriaOverview;
  if (!overview) return;
  const counts = overview.counts || {};
  const cards = [
    [Number(counts.libraries || 0), "Bibliotecas"],
    [Number(counts.enabled_libraries || 0), "Activas"],
    [Number(counts.sources || 0), "Fuentes"],
    [Number(counts.units || 0), "Unidades"],
    [Number(counts.reviewed_units || 0), "Revisadas"],
    [formatBytes(Number(overview.storage?.size_bytes || 0)), "Disco local"],
  ];
  elements.alexandriaOverview.replaceChildren();
  for (const [value, label] of cards) {
    const card = createElement("div", "overview-card");
    card.append(createElement("strong", "", String(value)), createElement("span", "", label));
    elements.alexandriaOverview.append(card);
  }
  elements.alexandriaBadge.textContent = String(Number(counts.libraries || 0));
  elements.alexandriaBadge.hidden = !Number(counts.libraries || 0);
}

function createLibraryCard(library) {
  const button = createElement("button", "library-card");
  button.type = "button";
  if (library.public_id === state.alexandriaSelectedId) button.classList.add("active");
  if (!library.enabled) button.classList.add("disabled");
  const head = createElement("div", "library-card-head");
  head.append(
    createElement("strong", "", library.name),
    createElement("span", `library-state ${library.enabled ? "enabled" : "disabled"}`, library.enabled ? "Activa" : "Desactivada"),
  );
  button.append(
    head,
    createElement("p", "", library.description || `Dominio: ${library.domain}`),
    createElement("small", "", `${library.source_count} fuentes · ${library.unit_count} unidades · ${library.reviewed_units} revisadas`),
  );
  button.addEventListener("click", () => selectAlexandriaLibrary(library.public_id));
  return button;
}

function renderAlexandriaLibraries() {
  elements.alexandriaLibraries.replaceChildren();
  if (!state.alexandriaLibraries.length) {
    elements.alexandriaLibraries.append(
      createElement("div", "empty-inspector", "Alejandría todavía no tiene bibliotecas."),
    );
    return;
  }
  for (const library of state.alexandriaLibraries) {
    elements.alexandriaLibraries.append(createLibraryCard(library));
  }
}

function sourceTrustLabel(source) {
  if (source.unit_count && source.reviewed_units === source.unit_count) return "Revisada";
  return "No revisada";
}

function renderAlexandriaDetail(payload) {
  const library = payload.library;
  const detail = createElement("div", "library-detail-content");
  const header = createElement("div", "library-detail-head");
  const title = createElement("div", "");
  title.append(
    createElement("p", "inspector-kicker", `${library.domain} · ${library.language}`),
    createElement("h3", "", library.name),
    createElement("p", "", library.description || "Sin descripción."),
  );
  const controls = createElement("div", "library-actions");
  const edit = createElement("button", "quiet-button", "Editar biblioteca");
  edit.type = "button";
  edit.addEventListener("click", () => openLibraryDialog(library));
  const toggle = createElement("button", "quiet-button", library.enabled ? "Desactivar" : "Activar");
  toggle.type = "button";
  toggle.addEventListener("click", async () => {
    try {
      await api(`/api/alexandria/libraries/${library.public_id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: !library.enabled }),
      });
      await loadAlexandria();
      await selectAlexandriaLibrary(library.public_id);
    } catch (error) {
      showError(error.message);
    }
  });
  const importButton = createElement("button", "primary-button", "Importar fuente");
  importButton.type = "button";
  importButton.addEventListener("click", () => elements.alexandriaFile.click());
  const remove = createElement("button", "danger-button compact", "Eliminar biblioteca");
  remove.type = "button";
  remove.addEventListener("click", () => deleteAlexandriaLibrary(library));
  controls.append(edit, toggle, importButton, remove);
  header.append(title, controls);
  detail.append(header);

  const meta = createElement("div", "library-meta-grid");
  for (const [label, value] of [
    ["Versión", library.version],
    ["Licencia", library.license_id],
    ["Fuentes", library.source_count],
    ["Unidades", library.unit_count],
  ]) {
    const item = createElement("div", "library-meta");
    item.append(createElement("span", "", label), createElement("strong", "", String(value)));
    meta.append(item);
  }
  detail.append(meta);

  const sourcesHeading = createElement("div", "section-heading");
  sourcesHeading.append(createElement("h3", "", "Fuentes"));
  detail.append(sourcesHeading);
  const sourceList = createElement("div", "source-list");
  if (!payload.sources.length) {
    sourceList.append(createElement("div", "empty-inspector", "Importa un documento o archivo de código para comenzar."));
  }
  for (const source of payload.sources) {
    const card = createElement("article", "source-card");
    const sourceHead = createElement("div", "source-card-head");
    sourceHead.append(
      createElement("strong", "", source.title),
      createElement("span", `source-trust ${sourceTrustLabel(source) === "Revisada" ? "reviewed" : "unreviewed"}`, sourceTrustLabel(source)),
    );
    card.append(
      sourceHead,
      createElement("p", "", `${source.filename} · ${formatBytes(source.size_bytes)} · ${source.processor || "procesador local"}`),
      createElement("small", "", `${source.unit_count} unidades · validación ${source.validation_status}`),
    );
    if (sourceTrustLabel(source) !== "Revisada") {
      const review = createElement("button", "quiet-button compact", "Marcar como revisada");
      review.type = "button";
      review.addEventListener("click", () => reviewAlexandriaSource(source.id));
      card.append(review);
    }
    sourceList.append(card);
  }
  detail.append(sourceList);
  elements.alexandriaDetail.replaceChildren(detail);
}

async function loadAlexandria() {
  const query = elements.alexandriaSearch.value.trim();
  const [overview, libraries, languagePacks] = await Promise.all([
    api("/api/alexandria/overview"),
    api(`/api/alexandria/libraries${query ? `?q=${encodeURIComponent(query)}` : ""}`),
    api("/api/alexandria/language-packs"),
  ]);
  state.alexandriaOverview = overview;
  state.alexandriaLibraries = libraries.items || [];
  renderAlexandriaOverview();
  renderAlexandriaLibraries();
  const core = languagePacks.spanish_core || {};
  const coreCard = document.createElement("article");
  coreCard.className = "library-card";
  coreCard.append(
    createElement(
      "strong",
      "",
      `Núcleo léxico español completo: ${core.installed ? "instalado" : "no instalado"}`,
    ),
    createElement("span", "", `${core.bundle_id || "elyndra-es-core"} · ${core.bundle_version || ""}`),
    createElement(
      "small",
      "",
      core.installed
        ? "Los cuatro packs requeridos están presentes."
        : `Faltan: ${(core.missing || []).join(", ")}. Selecciona un manifest local; no hay descarga online.`,
    ),
  );
  const inspectBundle = createElement("button", "quiet-button compact", "Inspeccionar bundle local");
  inspectBundle.type = "button";
  inspectBundle.addEventListener("click", async () => {
    const manifest = window.prompt("Ruta local a elyndra-language-bundle.json");
    if (!manifest) return;
    try {
      const response = await api("/api/alexandria/language-bundles/inspect", {
        method: "POST", body: JSON.stringify({ manifest }),
      });
      showNotice(`Bundle ${response.item.bundle_id} ${response.item.bundle_version} verificado.`);
    } catch (error) {
      showError(error.message);
    }
  });
  const installBundle = createElement("button", "quiet-button compact", "Instalar bundle local");
  installBundle.type = "button";
  installBundle.addEventListener("click", async () => {
    const manifest = window.prompt("Ruta local al manifest verificado");
    if (!manifest || !window.confirm("¿Instalar y habilitar los cuatro packs verificados?")) return;
    try {
      await api("/api/alexandria/language-bundles/install", {
        method: "POST", body: JSON.stringify({ manifest, approved: true, enable: true }),
      });
      showNotice("Bundle español instalado y habilitado.");
      await loadAlexandria();
    } catch (error) {
      showError(error.message);
    }
  });
  coreCard.append(inspectBundle, installBundle);
  const cards = (languagePacks.items || []).map((pack) => {
    const card = document.createElement("article");
    card.className = "library-card";
    const title = document.createElement("strong");
    title.textContent = `${pack.logical_pack_id} · ${pack.version}`;
    const detail = document.createElement("span");
    detail.textContent = `${pack.status} · ${pack.verification_status} · prioridad ${pack.query_priority}`;
    card.append(title, detail);
    return card;
  });
  elements.alexandriaLanguagePacks.replaceChildren(coreCard, ...cards);
}

async function selectAlexandriaLibrary(libraryId) {
  state.alexandriaSelectedId = libraryId;
  renderAlexandriaLibraries();
  try {
    const payload = await api(`/api/alexandria/libraries/${encodeURIComponent(libraryId)}`);
    renderAlexandriaDetail(payload);
  } catch (error) {
    showError(error.message);
  }
}

async function openAlexandria({ updateUrl = true } = {}) {
  showAlexandriaWorkspace();
  if (updateUrl && window.location.pathname !== "/alexandria") {
    window.history.pushState({ alexandria: true }, "", "/alexandria");
  }
  try {
    await loadAlexandria();
    if (state.alexandriaSelectedId) await selectAlexandriaLibrary(state.alexandriaSelectedId);
  } catch (error) {
    showError(error.message);
  }
  closeSidebar();
}

function openLibraryDialog(library = null) {
  state.libraryDialogMode = library ? "edit" : "create";
  state.libraryEditingId = library?.public_id || null;
  elements.libraryDialogTitle.textContent = library
    ? "Editar biblioteca"
    : "Nueva biblioteca";
  elements.libraryDialogConfirm.textContent = library
    ? "Guardar cambios"
    : "Crear biblioteca";
  elements.libraryName.value = library?.name || "";
  elements.libraryDescription.value = library?.description || "";
  elements.libraryDomain.value = library?.domain || "general";
  elements.libraryLanguage.value = library?.language || "auto";
  elements.libraryVersion.value = library?.version || "1";
  elements.libraryLicense.value = library?.license_id || "unverified";
  elements.libraryDialog.showModal();
  elements.libraryName.focus();
  elements.libraryName.select();
}

async function saveAlexandriaLibrary() {
  const name = elements.libraryName.value.trim();
  if (!name) {
    showError("La biblioteca necesita un nombre.");
    return;
  }
  const payload = {
    name,
    description: elements.libraryDescription.value.trim(),
    domain: elements.libraryDomain.value.trim(),
    language: elements.libraryLanguage.value.trim(),
    version: elements.libraryVersion.value.trim(),
    license_id: elements.libraryLicense.value.trim(),
  };
  try {
    if (state.libraryDialogMode === "edit" && state.libraryEditingId) {
      const response = await api(
        `/api/alexandria/libraries/${state.libraryEditingId}`,
        { method: "PATCH", body: JSON.stringify(payload) },
      );
      state.alexandriaSelectedId = response.library.public_id;
      showNotice("Biblioteca actualizada.");
    } else {
      const response = await api("/api/alexandria/libraries", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      state.alexandriaSelectedId = response.library.public_id;
      showNotice("Biblioteca creada localmente.");
    }
    await loadAlexandria();
    await selectAlexandriaLibrary(state.alexandriaSelectedId);
  } catch (error) {
    showError(error.message);
  }
}

async function deleteAlexandriaLibrary(library) {
  const accepted = await confirmElyndraAction(
    "Eliminar biblioteca",
    `¿Eliminar definitivamente “${library.name}”? Se borrarán sus fuentes, `
      + "unidades indexadas y archivos locales. Esta acción no se puede deshacer.",
    "Eliminar definitivamente",
  );
  if (!accepted) return;
  try {
    await api(`/api/alexandria/libraries/${library.public_id}`, {
      method: "DELETE",
    });
    state.alexandriaSelectedId = null;
    elements.alexandriaDetail.replaceChildren(
      createElement("div", "empty-inspector", "Selecciona o crea una biblioteca."),
    );
    await loadAlexandria();
    showNotice("Biblioteca eliminada definitivamente.");
  } catch (error) {
    showError(error.message);
  }
}

async function importAlexandriaFile(file) {
  if (!state.alexandriaSelectedId || !file) return;
  if (file.size > 5 * 1024 * 1024) {
    showError("La fuente supera el límite web de 5 MiB.");
    return;
  }
  try {
    const dataBase64 = await fileToBase64(file);
    await api(`/api/alexandria/libraries/${state.alexandriaSelectedId}/sources`, {
      method: "POST",
      body: JSON.stringify({ filename: file.name, data_base64: dataBase64 }),
    });
    await loadAlexandria();
    await selectAlexandriaLibrary(state.alexandriaSelectedId);
    showNotice("Fuente importada. Comienza como no revisada.");
  } catch (error) {
    showError(error.message);
  } finally {
    elements.alexandriaFile.value = "";
  }
}

async function reviewAlexandriaSource(sourceId) {
  const accepted = await confirmElyndraAction(
    "Revisar fuente",
    "Esto marcará sus unidades como revisadas por el propietario. No modifica el modelo.",
    "Marcar como revisada",
  );
  if (!accepted) return;
  try {
    await api(`/api/alexandria/sources/${sourceId}/review`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    await loadAlexandria();
    await selectAlexandriaLibrary(state.alexandriaSelectedId);
    showNotice("Fuente marcada como revisada.");
  } catch (error) {
    showError(error.message);
  }
}

async function searchAlexandriaContent() {
  const query = elements.alexandriaSearch.value.trim();
  if (!query) {
    await loadAlexandria();
    return;
  }
  try {
    const payload = await api(`/api/alexandria/search?q=${encodeURIComponent(query)}`);
    const wrapper = createElement("div", "alexandria-search-results");
    wrapper.append(createElement("h3", "", `Resultados para “${query}”`));
    if (!payload.items.length) wrapper.append(createElement("p", "", "Sin coincidencias."));
    for (const item of payload.items) {
      const card = createElement("article", "source-card");
      card.append(
        createElement("strong", "", `${item.library_name} · ${item.source_title}`),
        createElement("small", "", `${item.heading} · ${item.review_status === "reviewed" ? "revisada" : "no revisada"}`),
        createElement("p", "", item.excerpt),
      );
      wrapper.append(card);
    }
    elements.alexandriaDetail.replaceChildren(wrapper);
  } catch (error) {
    showError(error.message);
  }
}

function currentChat(chatId = state.activeChatId) {
  return [...state.pinnedChats, ...state.chats].find((chat) => chat.id === chatId)
    || (state.activeChat?.id === chatId ? state.activeChat : null);
}

function createChatRow(chat) {
  const row = createElement("div", "chat-row");
  if (chat.id === state.activeChatId) row.classList.add("active");
  const button = createElement("button", "chat-item");
  button.type = "button";
  button.dataset.chatId = chat.id;
  button.append(
    createElement("span", "chat-item-title", chat.title),
    createElement("span", "chat-item-summary", shortSummary(chat.summary)),
    createElement(
      "span",
      "chat-item-time",
      `${chat.turn_count} turnos · ${formatRelativeDate(chat.updated_at)}`,
    ),
  );
  button.addEventListener("click", () => openChat(chat.id));
  button.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    showContextMenu(chat.id, event.clientX, event.clientY);
  });

  const menuButton = createElement("button", "chat-menu-button", "⋯");
  menuButton.type = "button";
  menuButton.setAttribute("aria-label", `Acciones de ${chat.title}`);
  menuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const rect = menuButton.getBoundingClientRect();
    showContextMenu(chat.id, rect.right, rect.bottom + 5);
  });
  row.append(button, menuButton);
  return row;
}

function renderChatCollection(container, chats, emptyText) {
  container.replaceChildren();
  if (!chats.length) {
    container.append(createElement("div", "empty-history", emptyText));
    return;
  }
  for (const chat of chats) container.append(createChatRow(chat));
}

function renderChatLists() {
  elements.chatCount.textContent = String(state.chats.length);
  elements.pinnedCount.textContent = `${state.pinnedChats.length}/5`;
  elements.pinnedSection.hidden = elements.historyFilter.value !== "active" || !state.pinnedChats.length;
  renderChatCollection(elements.pinnedList, state.pinnedChats, "");
  const emptyText = elements.search.value
    ? "No hay coincidencias."
    : elements.historyFilter.value === "archived"
      ? "No hay conversaciones archivadas."
      : "Todavía no hay conversaciones.";
  renderChatCollection(elements.chatList, state.chats, emptyText);
}

function renderWelcome({ updateUrl = true } = {}) {
  showChatWorkspace();
  state.activeChatId = null;
  state.activeChat = null;
  state.draftChat = true;
  clearPendingAttachments(false);
  elements.conversation.replaceChildren(elements.welcome);
  elements.welcome.hidden = false;
  elements.chatTitle.textContent = state.bootstrap?.default_chat_title || "Nuevo chat";
  elements.chatSubtitle.textContent = "Privado · local · bajo tu control";
  elements.renameChat.disabled = true;
  elements.renameChat.hidden = true;
  elements.chatActions.disabled = true;
  elements.chatActions.hidden = true;
  setComposerAvailable(true);
  if (updateUrl) updateChatUrl(null, true);
  renderChatLists();
}

function renderAttachmentPreview(container, attachments, { removable = false } = {}) {
  for (const attachment of attachments || []) {
    const card = createElement("div", `attachment-card ${attachment.kind}`);
    if (attachment.kind === "image") {
      const image = document.createElement("img");
      image.src = attachment.content_url;
      image.alt = attachment.filename;
      image.loading = "lazy";
      card.append(image);
    } else {
      card.append(createElement("span", "attachment-icon", attachmentIcon(attachment)));
    }
    const info = createElement("div", "attachment-info");
    info.append(
      createElement("strong", "", attachment.filename),
      createElement("small", "", formatBytes(attachment.size_bytes)),
    );
    const statusRow = createElement("div", "attachment-status-row");
    statusRow.append(
      createElement(
        "span",
        `attachment-status ${attachment.extraction_status || "not_checked"}`,
        attachmentStatusLabel(attachment.extraction_status),
      ),
      createElement(
        "span",
        `attachment-status ${attachment.validation_status || "not_checked"}`,
        attachmentStatusLabel(attachment.validation_status),
      ),
    );
    info.append(statusRow);
    if (attachment.secrets_redacted) {
      info.append(
        createElement(
          "small",
          "attachment-warning",
          "Secretos redactados para el modelo",
        ),
      );
    }
    card.append(info);
    if (removable) {
      const remove = createElement("button", "attachment-remove", "×");
      remove.type = "button";
      remove.setAttribute("aria-label", `Quitar ${attachment.filename}`);
      remove.addEventListener("click", () => removePendingAttachment(attachment.id));
      card.append(remove);
    }
    container.append(card);
  }
}

function looksLikeCode(value) {
  const text = String(value).trim();
  if (!text) return false;
  if (text.startsWith("<?php") || text.startsWith("#!/") || text.startsWith("SELECT ")) return true;
  if (/^(?:function|class|interface|const|let|var|def|import|from)\b/m.test(text)) return true;
  const symbols = (text.match(/[{};$<>]/g) || []).length;
  return text.includes("\n") && symbols >= 4;
}

function renderUserText(container, text) {
  if (looksLikeCode(text)) {
    const pre = createElement("pre", "user-code-block");
    const code = createElement("code", "", String(text).trim());
    pre.append(code);
    container.append(pre);
    return;
  }
  container.textContent = text;
}

function appendMessage(role, text, meta = "", attachments = []) {
  const row = createElement("article", `message-row ${role}`);
  if (role === "assistant") row.append(createElement("div", "avatar", "E"));
  const content = createElement("div", "message-content");
  content.append(createElement("div", "message-author", role === "assistant" ? "Elyn" : "Tú"));
  if (attachments.length) {
    const attachmentList = createElement("div", "message-attachments");
    renderAttachmentPreview(attachmentList, attachments);
    content.append(attachmentList);
  }
  const messageText = createElement("div", "message-text");
  if (role === "assistant") renderMarkdown(messageText, text);
  else renderUserText(messageText, text);
  content.append(messageText);
  if (meta) content.append(createElement("div", "message-meta", meta));
  row.append(content);
  elements.conversation.append(row);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return row;
}

function setComposerAvailable(available) {
  const disabled = !available || state.sending || state.uploading;
  elements.input.disabled = disabled;
  elements.attach.disabled = disabled;
  elements.send.disabled = disabled || (!elements.input.value.trim() && !state.pendingAttachments.length);
  elements.input.placeholder = available
    ? `Escribe un mensaje para ${state.bootstrap?.agent_name || "Elyn"}…`
    : "Este chat está archivado. Restáuralo para continuar.";
}

function renderAttachmentTray() {
  elements.attachmentTray.replaceChildren();
  elements.attachmentTray.hidden = !state.pendingAttachments.length;
  renderAttachmentPreview(elements.attachmentTray, state.pendingAttachments, { removable: true });
  setComposerAvailable(state.activeChat?.status !== "archived");
}

function renderChatDetail(detail, { updateUrl = true } = {}) {
  showChatWorkspace();
  const { chat, turns, summary, pending_attachments: pendingAttachments = [] } = detail;
  state.activeChatId = chat.id;
  state.activeChat = chat;
  state.pendingAttachments = pendingAttachments;
  renderAttachmentTray();
  elements.welcome.hidden = true;
  elements.conversation.replaceChildren();
  elements.chatTitle.textContent = chat.title;
  elements.chatSubtitle.textContent = `${chat.turn_count} turnos · ${chat.transcript_mode === "full" ? "historial completo local" : "resumen local"}${chat.status === "archived" ? " · archivado" : ""}`;
  elements.renameChat.hidden = false;
  elements.renameChat.disabled = chat.status !== "active";
  elements.chatActions.hidden = false;
  elements.chatActions.disabled = false;
  setComposerAvailable(chat.status === "active");

  if (turns.length) {
    for (const turn of turns) {
      appendMessage("user", turn.user_text, "", turn.attachments || []);
      appendMessage("assistant", turn.assistant_text);
    }
  } else if (summary) {
    const card = createElement("div", "summary-card");
    card.append(createElement("strong", "", "Resumen persistente"), createElement("p", "", summary));
    elements.conversation.append(card);
  } else {
    elements.conversation.append(elements.welcome);
    elements.welcome.hidden = false;
    elements.welcomeTitle.textContent = `Hola, soy ${state.bootstrap?.agent_name || "Elyn"}.`;
  }
  renderChatLists();
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  if (updateUrl) updateChatUrl(chat.id);
}

function renderSidebarAccount() {
  const account = state.bootstrap?.auth?.account || {};
  const displayName = String(account.preferred_name || account.username || "Usuario").trim();
  const initial = Array.from(displayName)[0]?.toUpperCase() || "U";
  elements.sidebarAccountAvatar.textContent = initial;
  elements.sidebarAccountName.textContent = displayName;
  elements.sidebarAccountMode.textContent = account.developer_mode
    ? "Modo desarrollador"
    : "Usuario local";
}

async function loadBootstrap() {
  state.bootstrap = await api("/api/bootstrap");
  document.title = `${state.bootstrap.project_name} · ${state.bootstrap.agent_name}`;
  const localMode = Boolean(state.bootstrap.offline || !state.bootstrap.network_allowed);
  elements.connectionLocal.classList.toggle("active", localMode);
  elements.connectionLocal.setAttribute("aria-pressed", String(localMode));
  elements.connectionOnline.classList.toggle("active", !localMode);
  elements.connectionOnline.setAttribute("aria-pressed", String(!localMode));
  const owner = state.bootstrap.owner_name ? `, ${state.bootstrap.owner_name}` : "";
  elements.welcomeTitle.textContent = `¿En qué trabajamos hoy${owner}?`;
  elements.chatTitle.textContent = state.bootstrap.default_chat_title;
  renderSidebarAccount();
  const developer = Boolean(state.bootstrap.developer_mode);
  elements.openAlexandria.hidden = !developer;
  elements.openControl.hidden = !developer;
  applyDeveloperModeVisibility();
}

async function loadChats(query = "") {
  const status = elements.historyFilter.value;
  const requests = [api(`/api/chats?q=${encodeURIComponent(query)}&status=${encodeURIComponent(status)}`)];
  if (status === "active") requests.push(api("/api/chats?status=pinned"));
  const [history, pinned] = await Promise.all(requests);
  state.chats = history.chats;
  state.pinnedChats = status === "active" ? pinned.chats : [];
  renderChatLists();
}

async function createChat() {
  elements.historyFilter.value = "active";
  const payload = await api("/api/chats", {
    method: "POST",
    body: JSON.stringify({ transcript_mode: "full" }),
  });
  state.draftChat = false;
  await loadChats(elements.search.value);
  renderChatDetail(payload);
  return payload;
}

function openNewChat() {
  const globalWorkspaceActive = state.inspectorActive
    || state.alexandriaActive
    || state.personalActive
    || state.profileActive
    || state.controlActive;
  if (!state.activeChatId && state.draftChat && !globalWorkspaceActive) {
    elements.input.focus();
    closeSidebar();
    return;
  }
  state.activeChatId = null;
  state.activeChat = null;
  state.draftChat = true;
  state.pendingAttachments = [];
  renderAttachmentTray();
  renderWelcome({ updateUrl: false });
  updateChatUrl(null);
  elements.input.focus();
  closeSidebar();
}

async function openChat(chatId, options = {}) {
  try {
    const detail = await api(`/api/chats/${encodeURIComponent(chatId)}`);
    renderChatDetail(detail, options);
    closeSidebar();
  } catch (error) {
    showError(error.message);
    if (options.updateUrl === false) renderWelcome();
  }
}

function updateChatUrl(chatId, replace = false) {
  const path = chatId ? `/chat/${encodeURIComponent(chatId)}` : "/";
  const method = replace ? "replaceState" : "pushState";
  if (window.location.pathname !== path) window.history[method]({ chatId }, "", path);
}

function chatIdFromLocation() {
  const match = window.location.pathname.match(/^\/chat\/(chat_[A-Za-z0-9_-]+)\/?$/);
  return match ? match[1] : null;
}

function startProcessing() {
  state.processingStarted = performance.now();
  elements.processing.hidden = false;
  elements.processingText.textContent = "Elyn está formulando una respuesta…";
  elements.processingTime.textContent = "0.0 s";
  state.processingTimer = window.setInterval(() => {
    const seconds = (performance.now() - state.processingStarted) / 1000;
    elements.processingTime.textContent = `${seconds.toFixed(1)} s`;
    if (seconds > 30) elements.processingText.textContent = "El modelo local sigue redactando…";
    else if (seconds > 12) elements.processingText.textContent = "Procesando el contexto local…";
    else if (seconds > 3) elements.processingText.textContent = "Buscando en Alejandría…";
  }, 100);
}

function stopProcessing() {
  if (state.processingTimer) window.clearInterval(state.processingTimer);
  state.processingTimer = null;
  elements.processing.hidden = true;
}

function setSending(value) {
  state.sending = value;
  setComposerAvailable(state.activeChat?.status !== "archived");
}

async function ensureActiveChat() {
  if (!state.activeChatId) await createChat();
  if (state.activeChat?.status !== "active") throw new Error("Restaura el chat antes de continuar.");
}

function fileToBase64(file) {
  return file.arrayBuffer().then((buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
    }
    return window.btoa(binary);
  });
}

async function attachFiles(fileList) {
  const files = [...fileList];
  elements.fileInput.value = "";
  if (!files.length) return;
  const maxCount = state.bootstrap?.attachment_max_count || 5;
  if (state.pendingAttachments.length + files.length > maxCount) {
    showError(`Cada mensaje admite como máximo ${maxCount} adjuntos.`);
    return;
  }
  try {
    await ensureActiveChat();
    state.uploading = true;
    setComposerAvailable(true);
    for (const file of files) {
      if (file.size > (state.bootstrap?.attachment_max_bytes || 5 * 1024 * 1024)) {
        throw new Error(`${file.name} supera el límite local de 5 MiB.`);
      }
      const dataBase64 = await fileToBase64(file);
      const payload = await api(`/api/chats/${encodeURIComponent(state.activeChatId)}/attachments`, {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          data_base64: dataBase64,
        }),
      });
      state.pendingAttachments.push(payload.attachment);
      renderAttachmentTray();
    }
  } catch (error) {
    showError(error.message);
  } finally {
    state.uploading = false;
    renderAttachmentTray();
    elements.input.focus();
  }
}

async function removePendingAttachment(attachmentId) {
  try {
    await api(`/api/attachments/${encodeURIComponent(attachmentId)}`, { method: "DELETE" });
    state.pendingAttachments = state.pendingAttachments.filter((item) => item.id !== attachmentId);
    renderAttachmentTray();
  } catch (error) {
    showError(error.message);
  }
}

function clearPendingAttachments(render = true) {
  state.pendingAttachments = [];
  if (render) renderAttachmentTray();
}

function appendResponseDiagnostics(content, meta) {
  if (!state.diagnostics || !meta?.timings) return;
  const timings = meta.timings;
  const details = createElement("details", "response-diagnostics");
  const summary = createElement("summary", "", "Diagnóstico local");
  const lines = [
    ["Planificación", timings.planning_ms],
    ["Alejandría", timings.retrieval_ms],
    ["Síntesis de evidencia", timings.evidence_ms],
    ["Contexto", timings.context_ms],
    ["Generación total", timings.generation_ms],
    ["Carga del modelo", timings.model_load_ms],
    ["Evaluación del prompt", timings.prompt_eval_ms],
    ["Generación del motor", timings.generation_engine_ms],
  ];
  const body = createElement("div", "diagnostic-grid");
  for (const [label, value] of lines) {
    if (value === undefined || value === null) continue;
    body.append(createElement("span", "", label), createElement("code", "", `${value} ms`));
  }
  if (timings.tokens_per_second) {
    body.append(
      createElement("span", "", "Velocidad"),
      createElement("code", "", `${timings.tokens_per_second} tok/s`),
    );
  }
  if (meta.engine === "alexandria-evidence") {
    body.append(
      createElement("span", "", "Motor"),
      createElement("code", "", "Evidencia local"),
    );
    if (meta.evidence_confidence !== undefined) {
      body.append(
        createElement("span", "", "Confianza de cobertura"),
        createElement(
          "code",
          "",
          `${Math.round(Number(meta.evidence_confidence || 0) * 100)}%`,
        ),
      );
    }
  }
  if (meta.development_session_id) {
    body.append(
      createElement("span", "", "Sesión de desarrollo"),
      createElement("code", "", meta.development_session_id),
    );
  }
  if (meta.suggested_actions?.length) {
    body.append(
      createElement("span", "", "Siguiente acción sugerida"),
      createElement(
        "code",
        "",
        meta.suggested_actions[0].command || meta.suggested_actions[0].label,
      ),
    );
  }
  if (meta.alexandria_domains?.length) {
    body.append(
      createElement("span", "", "Dominios"),
      createElement("code", "", meta.alexandria_domains.join(", ")),
    );
  }
  details.append(summary, body);
  content.append(details);
}

async function sendMessage() {
  if (state.sending || state.uploading) return;
  const text = elements.input.value.trim();
  if (!text && !state.pendingAttachments.length) return;
  let optimisticRow = null;
  let streamingRow = null;
  try {
    await ensureActiveChat();
    const outgoingAttachments = [...state.pendingAttachments];
    elements.welcome.hidden = true;
    if (elements.welcome.parentElement === elements.conversation) elements.conversation.replaceChildren();
    optimisticRow = appendMessage(
      "user",
      text || "Analiza y resume los archivos adjuntos.",
      "",
      outgoingAttachments,
    );
    elements.input.value = "";
    resizeComposer();
    setSending(true);
    startProcessing();

    streamingRow = appendMessage("assistant", "");
    streamingRow.classList.add("streaming");
    const streamingContent = streamingRow.querySelector(".message-content");
    const streamingTextNode = streamingRow.querySelector(".message-text");
    let streamedText = "";

    const executeRequest = async (approvalToken = null) => {
      streamedText = "";
      streamingTextNode.textContent = "";
      return streamApi(
        `/api/chats/${encodeURIComponent(state.activeChatId)}/messages/stream`,
        {
          body: JSON.stringify({
            text,
            attachment_ids: outgoingAttachments.map((item) => item.id),
            approval_token: approvalToken,
          }),
          onStatus(event) {
            elements.processingText.textContent = event.message || "Procesando…";
          },
          onToken(token) {
            streamedText += token;
            streamingTextNode.textContent = streamedText;
            elements.conversation.scrollTop = elements.conversation.scrollHeight;
          },
        },
      );
    };

    let response = await executeRequest();
    if (response.meta?.approval_required) {
      stopProcessing();
      const isChangeProposal = response.meta.skill_name === "assistant.change_proposal.apply";
      const isValidationCycle = response.meta.skill_name === "assistant.validation_cycle.run";
      const accepted = await confirmElyndraAction(
        isChangeProposal
          ? "Aplicar cambios controlados"
          : isValidationCycle
            ? "Ejecutar validación supervisada"
            : "Aprobar skill local",
        response.meta.approval_summary || "Esta acción requiere aprobación del propietario.",
        isChangeProposal
          ? "Aplicar propuesta"
          : isValidationCycle
            ? "Ejecutar validación"
            : "Ejecutar skill",
      );
      const approvalToken = response.meta.approval_token;
      if (!approvalToken) throw new Error("Elyndra no entregó una aprobación válida.");
      if (!accepted) {
        await api(`/api/approvals/${encodeURIComponent(approvalToken)}/cancel`, {
          method: "POST",
          body: JSON.stringify({ chat_id: state.activeChatId }),
        });
        optimisticRow.remove();
        streamingRow.remove();
        elements.input.value = text;
        resizeComposer();
        showNotice("Ejecución cancelada. No se inició ningún proceso.");
        return;
      }
      startProcessing();
      elements.processingText.textContent = isChangeProposal
        ? "Aplicando propuesta exacta una sola vez…"
        : isValidationCycle
          ? "Ejecutando plan de validación exacto una sola vez…"
          : "Ejecutando skill autorizada una sola vez…";
      response = await executeRequest(approvalToken);
    }
    if (!response.ok && response.meta?.engine !== "local-skill") {
      throw new Error(response.message || "La operación local falló.");
    }

    stopProcessing();
    clearPendingAttachments();
    streamingRow.classList.remove("streaming");
    streamingTextNode.replaceChildren();
    renderMarkdown(streamingTextNode, response.message);
    streamingContent.append(createElement("div", "message-meta", formatElapsed(response.elapsed_ms)));
    appendResponseDiagnostics(streamingContent, response.meta);
    state.activeChat = response.chat;
    elements.chatTitle.textContent = response.chat.title;
    elements.chatSubtitle.textContent = `${response.chat.turn_count} turnos · ${response.chat.transcript_mode === "full" ? "historial completo local" : "resumen local"}`;
    await loadChats(elements.search.value);
  } catch (error) {
    stopProcessing();
    optimisticRow?.remove();
    streamingRow?.remove();
    showError(error.message);
  } finally {
    setSending(false);
    elements.input.focus();
  }
}

async function renameChat(chatId = state.activeChatId) {
  if (!chatId) return;
  const chat = currentChat(chatId);
  const current = chat?.title || elements.chatTitle.textContent;
  const title = await editElyndraText("Renombrar chat", current);
  if (!title || title === current) return;
  try {
    const response = await api(`/api/chats/${encodeURIComponent(chatId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    if (state.activeChatId === chatId) {
      state.activeChat = response.chat;
      elements.chatTitle.textContent = response.chat.title;
    }
    await loadChats(elements.search.value);
  } catch (error) {
    showError(error.message);
  }
}

function showContextMenu(chatId, x, y) {
  const chat = currentChat(chatId);
  if (!chat) return;
  state.contextChatId = chatId;
  const pinButton = elements.contextMenu.querySelector('[data-action="pin"]');
  const archiveButton = elements.contextMenu.querySelector('[data-action="archive"]');
  const renameButton = elements.contextMenu.querySelector('[data-action="rename"]');
  pinButton.textContent = chat.pinned ? "Desanclar chat" : "Anclar chat";
  pinButton.hidden = chat.status !== "active";
  renameButton.hidden = chat.status !== "active";
  archiveButton.textContent = chat.status === "archived" ? "Restaurar" : "Archivar";
  elements.contextMenu.hidden = false;
  const width = elements.contextMenu.offsetWidth;
  const height = elements.contextMenu.offsetHeight;
  elements.contextMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - width - 8))}px`;
  elements.contextMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - height - 8))}px`;
}

function closeContextMenu() {
  elements.contextMenu.hidden = true;
  state.contextChatId = null;
}

function showAccountMenu() {
  elements.accountContextMenu.hidden = false;
  elements.sidebarAccountButton.setAttribute("aria-expanded", "true");
  const rect = elements.sidebarAccountButton.getBoundingClientRect();
  const width = elements.accountContextMenu.offsetWidth;
  const height = elements.accountContextMenu.offsetHeight;
  elements.accountContextMenu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  elements.accountContextMenu.style.top = `${Math.max(8, rect.top - height - 8)}px`;
}

function closeAccountMenu() {
  elements.accountContextMenu.hidden = true;
  elements.sidebarAccountButton.setAttribute("aria-expanded", "false");
}

async function handleAccountMenuAction(action) {
  closeAccountMenu();
  if (action === "profile") {
    await openProfile();
    return;
  }
  if (action === "register") {
    window.location.assign("/register");
    return;
  }
  if (action === "switch") {
    await logoutWeb();
    return;
  }
  if (action === "logout") await logoutWeb();
}

function confirmPermanentDelete(chat) {
  return confirmElyndraAction(
    "Eliminar chat",
    `¿Eliminar definitivamente “${chat.title}”? Se borrarán el chat, su memoria asociada, sus adjuntos y sus archivos fríos. Esta acción no se puede deshacer.`,
    "Eliminar definitivamente",
  );
}

async function handleContextAction(action) {
  const chatId = state.contextChatId || state.activeChatId;
  const chat = currentChat(chatId);
  closeContextMenu();
  if (!chatId || !chat) return;
  try {
    if (action === "rename") {
      await renameChat(chatId);
      return;
    }
    if (action === "pin") {
      await api(`/api/chats/${encodeURIComponent(chatId)}/pin`, {
        method: "POST",
        body: JSON.stringify({ pinned: !chat.pinned }),
      });
      await loadChats(elements.search.value);
      return;
    }
    if (action === "export") {
      window.open(`/export/chats/${encodeURIComponent(chatId)}`, "_blank", "noopener");
      return;
    }
    if (action === "archive") {
      const endpoint = chat.status === "archived" ? "restore" : "archive";
      await api(`/api/chats/${encodeURIComponent(chatId)}/${endpoint}`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (state.activeChatId === chatId) renderWelcome();
      await loadChats(elements.search.value);
      return;
    }
    if (action === "delete") {
      if (!(await confirmPermanentDelete(chat))) return;
      await api(`/api/chats/${encodeURIComponent(chatId)}`, { method: "DELETE" });
      if (state.activeChatId === chatId) renderWelcome();
      await loadChats(elements.search.value);
      showNotice("Conversación eliminada definitivamente.");
    }
  } catch (error) {
    showError(error.message);
  }
}

function resizeComposer() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 180)}px`;
  setComposerAvailable(state.activeChat?.status !== "archived");
}

function showError(message) {
  elements.toast.classList.add("error");
  showNotice(message);
}

function showNotice(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(showNotice.timer);
  showNotice.timer = window.setTimeout(() => {
    elements.toast.hidden = true;
    elements.toast.classList.remove("error");
  }, 5000);
}

function openSidebar() {
  elements.sidebar.classList.add("open");
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
}

function bindEvents() {
  elements.authTabLogin.addEventListener("click", () => {
    window.location.assign("/login");
  });
  elements.authTabRegister.addEventListener("click", () => {
    window.location.assign("/register");
  });
  elements.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    authenticateWeb("/api/auth/login", {
      login: elements.loginName.value,
      password: elements.loginPassword.value,
      user_agent: navigator.userAgent,
    }).catch((error) => showError(error.message));
  });
  elements.registerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!elements.registerApprove.checked) {
      showError("Debes confirmar el registro y la mayoría de edad.");
      return;
    }
    authenticateWeb("/api/auth/register", {
      username: elements.registerUsername.value,
      email: elements.registerEmail.value,
      password: elements.registerPassword.value,
      password_confirmation: elements.registerPasswordConfirmation.value,
      birth_date: elements.registerBirthDate.value,
      preferred_name: elements.registerPreferredName.value,
      developer_mode: elements.registerDeveloperMode.checked,
      telemetry_enabled: elements.registerTelemetry.checked,
      user_agent: navigator.userAgent,
      approved: true,
    }).catch((error) => showError(error.message));
  });
  elements.openProfile.addEventListener("click", () => openProfile().catch((error) => showError(error.message)));
  elements.sidebarAccountButton.addEventListener("click", (event) => {
    event.stopPropagation();
    if (elements.accountContextMenu.hidden) showAccountMenu();
    else closeAccountMenu();
  });
  elements.accountContextMenu.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-account-action]");
    if (!button) return;
    handleAccountMenuAction(button.dataset.accountAction).catch((error) => showError(error.message));
  });
  elements.logoutButton.addEventListener("click", () => logoutWeb().catch((error) => showError(error.message)));
  elements.profileForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedAccountWrite("Actualizar perfil", "/api/account/profile", {
      preferred_name: elements.profilePreferredName.value,
      pronouns: elements.profilePronouns.value,
      sex: elements.profileSex.value,
      gender_identity: elements.profileGenderIdentity.value,
      sexual_orientation: elements.profileSexualOrientation.value,
      timezone: elements.profileTimezone.value,
      language: elements.profileLanguage.value,
      birthday_greeting_enabled: elements.profileBirthdayGreeting.checked,
      developer_mode: elements.profileDeveloperMode.checked,
      telemetry_enabled: elements.profileTelemetry.checked,
    }).catch((error) => showError(error.message));
  });
  elements.changeEmailForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedAccountWrite("Cambiar correo", "/api/account/email", {
      email: elements.changeEmail.value,
      password: elements.changeEmailPassword.value,
    }).catch((error) => showError(error.message));
  });
  elements.changePasswordForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedAccountWrite("Cambiar contraseña", "/api/account/password", {
      current_password: elements.currentPassword.value,
      new_password: elements.newPassword.value,
      password_confirmation: elements.newPasswordConfirmation.value,
    }, true).catch((error) => showError(error.message));
  });
  elements.accountExportForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!window.confirm("¿Crear y descargar una exportación cifrada local?")) return;
    downloadAccountExport(
      elements.accountExportPassword.value,
      elements.accountExportPassphrase.value,
    ).then(() => {
      elements.accountExportForm.reset();
      showNotice("Exportación cifrada descargada.");
    }).catch((error) => showError(error.message));
  });
  elements.newChat.addEventListener("click", openNewChat);
  elements.toggleChatSearch.addEventListener("click", () => {
    const opening = elements.chatSearchBox.hidden;
    elements.chatSearchBox.hidden = !opening;
    elements.toggleChatSearch.setAttribute("aria-expanded", String(opening));
    if (opening) elements.search.focus();
    else {
      elements.search.value = "";
      loadChats().catch((error) => showError(error.message));
    }
  });
  elements.connectionLocal.addEventListener("click", () => {
    api("/api/online", {
      method: "POST",
      body: JSON.stringify({ action: "mode-set", mode: "local", approved: true }),
    }).then(() => {
      elements.connectionLocal.classList.add("active");
      elements.connectionOnline.classList.remove("active");
      showNotice("Modo local protegido activado.");
    }).catch((error) => showError(error.message));
  });
  elements.connectionOnline.addEventListener("click", async () => {
    try {
      const status = await api("/api/online/status");
      const pending = (status.operations || []).filter(
        (item) => !["completed", "cancelled"].includes(item.operation_state),
      ).length;
      const summary = [
        `Gateway global: ${status.global_gateway_enabled ? "habilitado" : "denegado"}`,
        `Operaciones pendientes: ${pending}`,
        `Cuarentena: ${(status.quarantine || []).length}`,
        "Las descargas se ejecutan únicamente desde CLI y cada acción exige aprobación.",
      ].join("\n");
      if (!window.confirm(`${summary}\n\n¿Activar el modo online controlado para esta cuenta?`)) return;
      await api("/api/online", {
        method: "POST",
        body: JSON.stringify({ action: "mode-set", mode: "online", approved: true }),
      });
      elements.connectionLocal.classList.remove("active");
      elements.connectionOnline.classList.add("active");
      showNotice("Modo online controlado activado; no inicia descargas automáticas.");
    } catch (error) {
      showError(error.message);
    }
  });
  elements.openMemory.addEventListener("click", () => openMemoryInspector());
  elements.openAlexandria.addEventListener("click", () => openAlexandria());
  elements.openControl.addEventListener("click", () => openControl());
  elements.openPersonal.addEventListener("click", () => openPersonal());
  elements.refreshPersonal.addEventListener("click", () => loadPersonal().catch((error) => showError(error.message)));
  elements.personalCommitmentForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Crear compromiso",
      `${elements.personalCommitmentTitle.value} · ${elements.personalCommitmentDate.value}`,
      "/api/personal/commitments",
      {
        title: elements.personalCommitmentTitle.value,
        date: elements.personalCommitmentDate.value,
        time: elements.personalCommitmentTime.value,
        priority: elements.personalCommitmentPriority.value,
      },
      elements.personalCommitmentForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalRoutineForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Crear rutina",
      `${elements.personalRoutineTitle.value} · ${elements.personalRoutineRecurrence.value}`,
      "/api/personal/routines",
      {
        title: elements.personalRoutineTitle.value,
        start_date: elements.personalRoutineDate.value,
        time: elements.personalRoutineTime.value,
        recurrence: elements.personalRoutineRecurrence.value,
      },
      elements.personalRoutineForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalRoutineCheckinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Registrar check-in de rutina",
      `${elements.personalRoutineCheckinId.value} · ${elements.personalRoutineCheckinDate.value}`,
      "/api/personal/routines/checkins",
      {
        routine_id: elements.personalRoutineCheckinId.value,
        date: elements.personalRoutineCheckinDate.value,
        status: elements.personalRoutineCheckinStatus.value,
        note: elements.personalRoutineCheckinNote.value,
      },
      elements.personalRoutineCheckinForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalReminderForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Proponer recordatorio",
      "La propuesta no enviará notificaciones ni se ejecutará en segundo plano.",
      "/api/personal/reminders",
      {
        item_id: elements.personalReminderItemId.value,
        minutes_before: Number(elements.personalReminderMinutes.value),
      },
      elements.personalReminderForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalReminderReviewForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Revisar recordatorio",
      `${elements.personalReminderReviewId.value} · ${elements.personalReminderDecision.value}`,
      "/api/personal/reminders/review",
      {
        reminder_id: elements.personalReminderReviewId.value,
        decision: elements.personalReminderDecision.value,
      },
      elements.personalReminderReviewForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalBirthdayForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Registrar cumpleaños",
      `${elements.personalBirthdayPerson.value} · ${elements.personalBirthdayDay.value}/${elements.personalBirthdayMonth.value}`,
      "/api/personal/birthdays",
      {
        person: elements.personalBirthdayPerson.value,
        month: Number(elements.personalBirthdayMonth.value),
        day: Number(elements.personalBirthdayDay.value),
        year: elements.personalBirthdayYear.value ? Number(elements.personalBirthdayYear.value) : null,
      },
      elements.personalBirthdayForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalWellbeingForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Guardar check-in",
      "Se guardarán métricas personales locales. Esto no constituye diagnóstico.",
      "/api/personal/wellbeing/checkins",
      {
        date: elements.personalWellbeingDate.value,
        mood: Number(elements.personalWellbeingMood.value),
        energy: Number(elements.personalWellbeingEnergy.value),
        stress: Number(elements.personalWellbeingStress.value),
        focus: Number(elements.personalWellbeingFocus.value),
        sleep_hours: elements.personalWellbeingSleep.value || null,
        activity_minutes: elements.personalWellbeingActivity.value || null,
        note: elements.personalWellbeingNote.value,
      },
      elements.personalWellbeingForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalCoachingForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const actions = elements.personalCoachingActions.value
      .split("\n")
      .map((value) => value.trim())
      .filter(Boolean);
    confirmedPersonalWrite(
      "Crear plan de coaching",
      "El plan será local y no ejecutará ninguna acción automáticamente.",
      "/api/personal/coaching/plans",
      {
        title: elements.personalCoachingTitle.value,
        focus: elements.personalCoachingFocus.value,
        objective: elements.personalCoachingObjective.value,
        start_date: elements.personalCoachingStart.value,
        review_date: elements.personalCoachingReview.value || null,
        actions,
      },
      elements.personalCoachingForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalCoachingStatusForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Actualizar plan de coaching",
      `${elements.personalCoachingStatusId.value} · ${elements.personalCoachingStatus.value}`,
      "/api/personal/coaching/plans/status",
      {
        plan_id: elements.personalCoachingStatusId.value,
        status: elements.personalCoachingStatus.value,
      },
      elements.personalCoachingStatusForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalCoachingActionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Actualizar acción de coaching",
      `${elements.personalCoachingActionId.value} · ${elements.personalCoachingActionStatus.value}`,
      "/api/personal/coaching/actions/status",
      {
        action_id: elements.personalCoachingActionId.value,
        status: elements.personalCoachingActionStatus.value,
      },
      elements.personalCoachingActionForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalAutomationPolicyForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Crear política de automatización",
      "La política solo habilita una acción local acotada y no inicia procesos en segundo plano.",
      "/api/personal/automation/policies",
      {
        title: elements.personalAutomationPolicyTitle.value,
        action_type: elements.personalAutomationPolicyAction.value,
        autonomy_level: elements.personalAutomationPolicyLevel.value,
        max_runs_per_day: Number(elements.personalAutomationPolicyLimit.value),
        window_start: elements.personalAutomationPolicyWindowStart.value || null,
        window_end: elements.personalAutomationPolicyWindowEnd.value || null,
      },
      elements.personalAutomationPolicyForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalAutomationForm.addEventListener("submit", (event) => {
    event.preventDefault();
    let params;
    try {
      params = JSON.parse(elements.personalAutomationParams.value || "{}");
    } catch (_error) {
      showError("Los parámetros deben ser un objeto JSON válido.");
      return;
    }
    if (!params || Array.isArray(params) || typeof params !== "object") {
      showError("Los parámetros deben ser un objeto JSON.");
      return;
    }
    const weekdays = elements.personalAutomationWeekday.value.trim()
      ? [elements.personalAutomationWeekday.value.trim()]
      : [];
    confirmedPersonalWrite(
      "Crear automatización",
      "La automatización se materializa solo mediante un escaneo explícito en primer plano.",
      "/api/personal/automations",
      {
        policy_id: elements.personalAutomationPolicyId.value,
        title: elements.personalAutomationTitle.value,
        schedule_kind: elements.personalAutomationSchedule.value,
        start_date: elements.personalAutomationStart.value,
        time_of_day: elements.personalAutomationTime.value,
        weekdays,
        month_day: elements.personalAutomationMonthDay.value
          ? Number(elements.personalAutomationMonthDay.value)
          : null,
        params,
      },
      elements.personalAutomationForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalAutomationScan.addEventListener("click", () => {
    confirmedPersonalWrite(
      "Escanear automatizaciones",
      "Se evaluarán vencimientos ahora. No quedará ningún proceso ejecutándose en segundo plano.",
      "/api/personal/automations/scan",
      {},
      null,
    ).catch((error) => showError(error.message));
  });
  elements.personalAutomationRunForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Aprobar ejecución",
      "Se ejecutará una única acción local previamente congelada.",
      "/api/personal/automations/runs/approve",
      { run_id: elements.personalAutomationRunId.value },
      elements.personalAutomationRunForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalAutomationInboxForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Actualizar bandeja local",
      `${elements.personalAutomationInboxId.value} · ${elements.personalAutomationInboxStatus.value}`,
      "/api/personal/automation/inbox/status",
      {
        inbox_id: elements.personalAutomationInboxId.value,
        status: elements.personalAutomationInboxStatus.value,
      },
      elements.personalAutomationInboxForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalSchedulerStart.addEventListener("click", () => {
    confirmedPersonalWrite(
      "Iniciar scheduler local",
      "El scheduler se ejecutará solo dentro de esta web y se detendrá al cerrarla.",
      "/api/personal/scheduler/start",
      { interval_seconds: Number(elements.personalSchedulerInterval.value || 60) },
      null,
    ).catch((error) => showError(error.message));
  });
  elements.personalSchedulerCycle.addEventListener("click", () => {
    confirmedPersonalWrite(
      "Ejecutar ciclo del scheduler",
      "Se realizará un único ciclo local con bloqueo exclusivo.",
      "/api/personal/scheduler/cycle",
      {},
      null,
    ).catch((error) => showError(error.message));
  });
  elements.personalSchedulerStop.addEventListener("click", () => {
    confirmedPersonalWrite(
      "Detener scheduler local",
      "Se solicitará apagado limpio y se esperará al hilo local.",
      "/api/personal/scheduler/stop",
      {},
      null,
    ).catch((error) => showError(error.message));
  });
  elements.personalNotificationsEnable.addEventListener("click", async () => {
    if (!("Notification" in window)) {
      showError("Este navegador no admite avisos locales.");
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission === "granted") {
      showNotice("Avisos locales del navegador habilitados para esta interfaz.");
      showPendingBrowserNotifications(state.personalData?.local_notifications || []);
    } else {
      showError("El navegador no autorizó los avisos locales.");
    }
  });
  elements.personalNotificationForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Actualizar notificación local",
      `${elements.personalNotificationId.value} · ${elements.personalNotificationStatus.value}`,
      "/api/personal/notifications/status",
      {
        notification_id: elements.personalNotificationId.value,
        status: elements.personalNotificationStatus.value,
      },
      elements.personalNotificationForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalIntentProposeForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Proponer aprendizaje lingüístico",
      `“${elements.personalIntentPhrase.value}” → ${elements.personalIntentName.value}`,
      "/api/personal/intents/proposals",
      {
        phrase: elements.personalIntentPhrase.value,
        intent: elements.personalIntentName.value,
        source: "owner_correction",
      },
      elements.personalIntentProposeForm,
    ).catch((error) => showError(error.message));
  });
  elements.personalIntentReviewForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmedPersonalWrite(
      "Revisar aprendizaje lingüístico",
      `${elements.personalIntentProposalId.value} · ${elements.personalIntentDecision.value}`,
      "/api/personal/intents/proposals/review",
      {
        proposal_id: elements.personalIntentProposalId.value,
        decision: elements.personalIntentDecision.value,
      },
      elements.personalIntentReviewForm,
    ).catch((error) => showError(error.message));
  });
  elements.refreshControl.addEventListener("click", () => loadControl().catch((error) => showError(error.message)));
  elements.trustProjectForm.addEventListener("submit", trustProject);
  elements.phpProfileForm.addEventListener("submit", savePhpProfile);
  elements.pythonProfileForm.addEventListener("submit", savePythonProfile);
  elements.packageInstallForm.addEventListener("submit", installAlexandriaPackage);
  elements.packageCreateForm.addEventListener("submit", createAlexandriaPackage);
  elements.packageExportForm.addEventListener("submit", exportAlexandriaPackage);
  elements.clearProfileForm.addEventListener("click", clearProfileForm);
  elements.clearPythonProfileForm.addEventListener("click", clearPythonProfileForm);
  elements.refreshControlAudit.addEventListener("click", () => loadControlAudit().catch((error) => showError(error.message)));
  elements.createLibrary.addEventListener("click", () => openLibraryDialog());
  elements.libraryDialog.addEventListener("close", () => {
    if (elements.libraryDialog.returnValue === "confirm") {
      saveAlexandriaLibrary();
    }
  });
  elements.alexandriaSearch.addEventListener("input", () => {
    window.clearTimeout(state.alexandriaSearchTimer);
    state.alexandriaSearchTimer = window.setTimeout(
      () => loadAlexandria().catch((error) => showError(error.message)),
      180,
    );
  });
  elements.searchAlexandria.addEventListener("click", () => searchAlexandriaContent());
  elements.alexandriaFile.addEventListener("change", () => {
    importAlexandriaFile(elements.alexandriaFile.files?.[0]);
  });
  elements.refreshInspector.addEventListener("click", () => refreshInspector().catch((error) => showError(error.message)));
  elements.inspectorTabs.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-view]");
    if (button) loadInspectorView(button.dataset.view).catch((error) => showError(error.message));
  });
  elements.inspectorSearch.addEventListener("input", () => {
    window.clearTimeout(state.inspectorTimer);
    state.inspectorTimer = window.setTimeout(
      () => loadInspectorView(state.inspectorView).catch((error) => showError(error.message)),
      180,
    );
  });
  elements.inspectorFilter.addEventListener("change", () => {
    loadInspectorView(state.inspectorView).catch((error) => showError(error.message));
  });
  elements.openSidebar.addEventListener("click", openSidebar);
  elements.closeSidebar.addEventListener("click", closeSidebar);
  elements.renameChat.addEventListener("click", () => renameChat());
  elements.chatActions.addEventListener("click", (event) => {
    event.stopPropagation();
    if (!state.activeChatId) return;
    const rect = elements.chatActions.getBoundingClientRect();
    showContextMenu(state.activeChatId, rect.right, rect.bottom + 5);
  });
  elements.contextMenu.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (button) handleContextAction(button.dataset.action);
  });
  document.addEventListener("click", (event) => {
    if (!elements.contextMenu.hidden && !elements.contextMenu.contains(event.target)) closeContextMenu();
    if (!elements.accountContextMenu.hidden && !elements.accountContextMenu.contains(event.target)) {
      closeAccountMenu();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeContextMenu();
      closeAccountMenu();
    }
  });
  let dragDepth = 0;
  document.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    dragDepth += 1;
    elements.dropOverlay.hidden = false;
  });
  document.addEventListener("dragover", (event) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.dataTransfer?.types.includes("Files")) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) elements.dropOverlay.hidden = true;
  });
  document.addEventListener("drop", (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    dragDepth = 0;
    elements.dropOverlay.hidden = true;
    attachFiles(event.dataTransfer.files);
  });
  elements.attach.addEventListener("click", () => elements.fileInput.click());
  elements.fileInput.addEventListener("change", () => attachFiles(elements.fileInput.files));
  elements.input.addEventListener("input", resizeComposer);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  elements.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });
  elements.search.addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(() => loadChats(elements.search.value), 180);
  });
  elements.historyFilter.addEventListener("change", async () => {
    renderWelcome();
    await loadChats(elements.search.value);
  });
  window.addEventListener("popstate", async () => {
    state.personalTimer = window.setInterval(() => {
      if (!state.personalActive) return;
      loadPersonal().catch((error) => showError(error.message));
    }, 15000);
    if (window.location.pathname === "/profile") {
      await openProfile({ updateUrl: false });
      return;
    }
    if (window.location.pathname === "/personal") {
      await openPersonal({ updateUrl: false });
      return;
    }
    if (window.location.pathname === "/control") {
      await openControl({ updateUrl: false });
      return;
    }
    if (window.location.pathname === "/alexandria") {
      await openAlexandria({ updateUrl: false });
      return;
    }
    if (window.location.pathname === "/memory") {
      await openMemoryInspector({ updateUrl: false });
      return;
    }
    const chatId = chatIdFromLocation();
    if (chatId) await openChat(chatId, { updateUrl: false });
    else renderWelcome();
  });
}

function showAuthMode(mode) {
  const register = mode === "register";
  elements.loginForm.hidden = register;
  elements.registerForm.hidden = !register;
  elements.authTabLogin.classList.toggle("active", !register);
  elements.authTabRegister.classList.toggle("active", register);
  elements.authTitle.textContent = register ? "Crear cuenta local" : "Iniciar sesión";
}

function showAuthScreen() {
  elements.authScreen.hidden = false;
  elements.appShell.hidden = true;
  const registered = Boolean(state.bootstrap?.auth?.registered);
  elements.authTabLogin.hidden = false;
  elements.authTabRegister.hidden = false;
  const count = Number(state.bootstrap?.auth?.account_count || 0);
  elements.authCopy.textContent = registered
    ? `Esta instalación tiene ${count} cuenta(s) local(es). Inicia sesión o registra otra cuenta aislada.`
    : "Crea la primera cuenta local de esta instalación.";
  const routeMode = window.location.pathname === "/register" ? "register" : "login";
  showAuthMode(routeMode);
}

function showApplication() {
  elements.authScreen.hidden = true;
  elements.appShell.hidden = false;
}

async function authenticateWeb(path, payload) {
  await api(path, { method: "POST", body: JSON.stringify(payload) });
  window.location.replace("/");
}

async function logoutWeb() {
  await api("/api/auth/logout", { method: "POST", body: JSON.stringify({}) });
  state.accountData = null;
  window.location.replace("/login");
}

async function confirmedAccountWrite(title, path, payload, logoutAfter = false) {
  const accepted = await confirmElyndraAction(
    title,
    "La modificación afecta la cuenta local y requiere confirmación explícita.",
    "Confirmar",
  );
  if (!accepted) return;
  await api(path, {
    method: "POST",
    body: JSON.stringify({ ...payload, approved: true }),
  });
  if (logoutAfter) {
    window.location.replace("/login");
    return;
  }
  await loadBootstrap();
  await loadProfile();
  showNotice("Cuenta local actualizada.");
}

async function initializeWorkspace() {
  await loadChats();
  await loadInspectorOverview();
  if (state.bootstrap.developer_mode) {
    const alexandriaOverview = await api("/api/alexandria/overview");
    state.alexandriaOverview = alexandriaOverview;
    renderAlexandriaOverview();
  }
  if (window.location.pathname === "/profile") {
    await openProfile({ updateUrl: false });
  } else if (window.location.pathname === "/personal") {
    await openPersonal({ updateUrl: false });
  } else if (window.location.pathname === "/control" && state.bootstrap.developer_mode) {
    await openControl({ updateUrl: false });
  } else if (window.location.pathname === "/alexandria" && state.bootstrap.developer_mode) {
    await openAlexandria({ updateUrl: false });
  } else if (window.location.pathname === "/memory") {
    await openMemoryInspector({ updateUrl: false });
  } else {
    const initialChatId = chatIdFromLocation();
    if (initialChatId) await openChat(initialChatId, { updateUrl: false });
    else renderWelcome();
  }
}

async function initialize() {
  bindEvents();
  resizeComposer();
  try {
    await loadBootstrap();
    const runtimeLabel = document.querySelector(".runtime-version");
    if (runtimeLabel && !runtimeLabel.textContent.includes(state.bootstrap.version)) {
      showNotice(`Runtime web desactualizado: página ${runtimeLabel.textContent}, servicio ${state.bootstrap.version}. Reinicia Elyndra Web.`);
    }
    if (window.location.pathname === "/register") {
      showAuthScreen();
      showAuthMode("register");
      return;
    }
    if (!state.bootstrap.auth?.authenticated) {
      if (window.location.pathname !== "/login") {
        window.location.replace("/login");
        return;
      }
      showAuthScreen();
      return;
    }
    if (window.location.pathname === "/login") {
      window.location.replace("/");
      return;
    }
    showApplication();
    await initializeWorkspace();
  } catch (error) {
    showError(error.message);
    showAuthScreen();
  }
  elements.input.focus();
}

initialize();
