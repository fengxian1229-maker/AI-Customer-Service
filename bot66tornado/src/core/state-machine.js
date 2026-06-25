'use strict';

const { menuFor, detectButton } = require('../content/menus');
const { getMessage, FLOW_MESSAGES } = require('../content/templates');
const { slipExampleUrlsForCategory, sopImageUrlsFor } = require('../content/assets');
const { OWNER, NEXT_STEP } = require('./owner');
const { extractIdentity, hasAttachment, isExplicitHumanRequest } = require('./extractors');
const { classifyWaitingBackendInput } = require('./waiting-backend-classifier');
const { nextVariant } = require('./template-history');

function createCase(initial = {}) {
  return {
    lang: initial.lang || 'es',
    stage: initial.stage || 'menu',
    owner: initial.owner || OWNER.CUSTOMER,
    fields: {
      accountOrPhone: null,
      depositScreenshot: null,
      withdrawalScreenshot: null,
      pendingReplyIdentity: null,
      forwardedAttachmentUrls: [],
      ...(initial.fields || {}),
    },
    templateHistory: {},
    sentAssets: {},
    repromptCounts: {
      ...(initial.repromptCounts || {}),
    },
    missingContent: [],
  };
}

function message(text, nextStepType, owner, extra = {}) {
  return {
    kind: 'message',
    text,
    nextStepType,
    owner,
    actions: [],
    ...extra,
  };
}

function buttons(menuName, lang, extra = {}) {
  const menu = menuFor(menuName, lang);
  return {
    kind: 'buttons',
    title: menu.title,
    buttons: menu.buttons,
    nextStepType: NEXT_STEP.BUTTONS,
    owner: OWNER.CUSTOMER,
    actions: [],
    ...extra,
  };
}

function actionOnly(nextStepType, owner, extra = {}) {
  return {
    kind: 'action_only',
    text: null,
    nextStepType,
    owner,
    actions: [],
    ...extra,
  };
}

function flowMessage(state, key, lang) {
  const entry = FLOW_MESSAGES[key];
  if (!entry) return getMessage(key, lang);
  const value = entry[lang] || entry.es;
  if (Array.isArray(value)) {
    return getMessage(key, lang, nextVariant(state, key, value.length));
  }
  return getMessage(key, lang);
}

function onceImageUrls(state, key, urls = []) {
  const list = Array.isArray(urls) ? urls.filter(Boolean) : [];
  if (!list.length) return [];
  state.sentAssets = state.sentAssets || {};
  if (state.sentAssets[key]) return [];
  state.sentAssets[key] = Date.now();
  return list;
}

function missing(caseState, key) {
  caseState.missingContent.push(key);
  return {
    kind: 'missing_content',
    key,
    text: null,
    nextStepType: NEXT_STEP.BUTTONS,
    owner: OWNER.CUSTOMER,
    actions: [],
  };
}

function transition(caseState, input) {
  const state = caseState || createCase();
  const lang = state.lang || 'es';
  const button = input.buttonId
    ? { id: input.buttonId }
    : detectButton(input.text, { lang, menuContext: menuContextForStage(state.stage) });

  if (button) return handleButton(state, button.id, input);

  switch (state.stage) {
    case 'menu':
      return handleMenuFreeText(state, input);
    case 'deposit_menu':
      return handleDepositMenuFreeText(state, input);
    case 'money_direction':
      return handleMoneyDirectionFreeText(state, input);
    case 'deposit_collect':
      return handleDepositCollect(state, input);
    case 'withdrawal_collect':
      return handleWithdrawalCollect(state, input);
    case 'withdrawal_menu':
      return handleWithdrawalMenuFreeText(state, input);
    case 'other_menu':
      return handleOtherMenuFreeText(state, input);
    case 'withdrawal_blocked':
      return handleWithdrawalBlocked(state, input);
    case 'after_deposit_howto':
      return handleSelfServiceAftercare(state, input, 'deposit_howto');
    case 'after_withdrawal_howto':
      return handleSelfServiceAftercare(state, input, 'withdrawal_howto');
    case 'forgot_password_sop':
      return handleSelfServiceAftercare(state, input, 'forgot_password');
    case 'pending_reply_collect':
      return handlePendingReplyCollect(state, input);
    case 'backend_querying':
      return {
        state,
        responses: [
          message(
            flowMessage(state, 'forwarded_followup_pool', lang),
            NEXT_STEP.BACKEND_QUERY,
            OWNER.BOT
          ),
        ],
      };
    case 'waiting_backend':
      return handleWaitingBackend(state, input);
    case 'backend_replied_waiting_next':
      return handleBackendRepliedNext(state, input);
    default:
      return handleMenuFreeText(state, input);
  }
}

function handleMenuFreeText(state, input) {
  const lang = state.lang || 'es';
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (isHighRiskHumanIssue(input.text)) {
    return handoffHuman(state, input, 'menu_high_risk_free_text');
  }
  if (isForgotPasswordFreeText(input.text)) {
    return handleButton(state, 'forgot_password', input);
  }
  if (isMenuVisibilityProblem(input.text)) {
    return handleMenuVisibilityFallback(state, input, 'main', 'menu');
  }
  if (isDepositFreeText(input.text)) {
    return handleButton(state, 'main_deposito', input);
  }
  if (isDepositHowtoFreeText(input.text)) {
    return handleButton(state, 'deposit_howto', input);
  }
  const withdrawalDecision = classifyWithdrawalIssue(input.text);
  const withdrawalRoute = routeWithdrawalDecision(state, input, withdrawalDecision, 'menu');
  if (withdrawalRoute) return withdrawalRoute;
  if (isPendingReplyFreeText(input.text)) {
    return handleButton(state, 'main_pending_reply', input);
  }
  if (isDepositMentionFreeText(input.text)) {
    return handleButton(state, 'deposit_menu', input);
  }
  if (isAmbiguousMoneyDirectionIssue(input.text)) {
    state.stage = 'money_direction';
    state.owner = OWNER.CUSTOMER;
    return { state, responses: [buttons('money_direction', lang)] };
  }
  if (isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'menu_unhandled_concrete_issue');
  }
  const reminderKey = menuReminderKey(input.text);
  return menuRepromptOrHandoff(state, input, 'menu', reminderKey, 'main');
}

function handleDepositMenuFreeText(state, input) {
  if (isExplicitHumanRequest(input.text) || isHighRiskHumanIssue(input.text)) {
    return handoffHuman(state, input, 'deposit_menu_human_issue');
  }
  if (hasAttachment(input)) {
    state.stage = 'deposit_collect';
    state.owner = OWNER.CUSTOMER;
    return handleDepositCollect(state, input);
  }
  if (isMenuVisibilityProblem(input.text)) {
    return handleMenuVisibilityFallback(state, input, 'deposit', 'deposit_menu');
  }
  if (isDepositFreeText(input.text)) {
    return handleButton(state, 'main_deposito', input);
  }
  if (isDepositHowtoFreeText(input.text)) {
    return handleButton(state, 'deposit_howto', input);
  }
  if (isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'deposit_menu_unhandled_concrete_issue');
  }
  return menuRepromptOrHandoff(state, input, 'deposit_menu', 'menu_button_reminder', 'deposit');
}

function handleMoneyDirectionFreeText(state, input) {
  if (isExplicitHumanRequest(input.text) || isHighRiskHumanIssue(input.text)) {
    return handoffHuman(state, input, 'money_direction_human_issue');
  }
  if (isMenuVisibilityProblem(input.text)) {
    return handleMenuVisibilityFallback(state, input, 'money_direction', 'money_direction');
  }
  if (isDepositFreeText(input.text)) {
    return handleButton(state, 'main_deposito', input);
  }
  const withdrawalDecision = classifyWithdrawalIssue(input.text);
  const withdrawalRoute = routeWithdrawalDecision(state, input, withdrawalDecision, 'money_direction');
  if (withdrawalRoute) return withdrawalRoute;
  if (isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'money_direction_unhandled_concrete_issue');
  }
  return menuRepromptOrHandoff(state, input, 'money_direction', 'menu_button_reminder', 'money_direction');
}

function handleOtherMenuFreeText(state, input) {
  if (isForgotPasswordFreeText(input.text)) {
    return handleButton(state, 'forgot_password', input);
  }
  if (isMenuVisibilityProblem(input.text)) {
    return handleMenuVisibilityFallback(state, input, 'other', 'other_menu');
  }
  if (isEmptyOrSmallTalk(input.text)) {
    return menuRepromptOrHandoff(state, input, 'other_menu', 'menu_button_reminder', 'other');
  }
  return handoffHuman(state, input, 'other_menu_free_text');
}

function handleWithdrawalMenuFreeText(state, input) {
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (hasAttachment(input)) {
    state.stage = 'withdrawal_collect';
    state.owner = OWNER.CUSTOMER;
    return handleWithdrawalCollect(state, input);
  }
  if (isMenuVisibilityProblem(input.text)) {
    return handleMenuVisibilityFallback(state, input, 'withdrawal', 'withdrawal_menu');
  }
  const withdrawalDecision = isAmbiguousMoneyDirectionIssue(input.text)
    ? { type: 'missing' }
    : classifyWithdrawalIssue(input.text);
  const withdrawalRoute = routeWithdrawalDecision(state, input, withdrawalDecision, 'withdrawal_menu');
  if (withdrawalRoute) return withdrawalRoute;
  if (isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'withdrawal_menu_unhandled_concrete_issue');
  }
  return menuRepromptOrHandoff(state, input, 'withdrawal_menu', 'withdrawal_menu_button_reminder', 'withdrawal');
}

function handleButton(state, id, input) {
  const lang = state.lang || 'es';
  resetUnknownRepromptCounts(state);
  if (id === 'global_human') {
    return handoffHuman(state, input, 'customer_selected_human');
  }
  if (id === 'route_main') {
    return routeToMenu(state, input, 'main');
  }
  if (id === 'route_previous') {
    return routeToMenu(state, input, recoveryScopeForState(state));
  }
  if (id === 'money_direction') {
    state.stage = 'money_direction';
    state.owner = OWNER.CUSTOMER;
    return { state, responses: [buttons('money_direction', lang)] };
  }
  if (id === 'deposit_menu') {
    state.stage = 'deposit_menu';
    state.owner = OWNER.CUSTOMER;
    return { state, responses: [buttons('deposit', lang)] };
  }
  if (id === 'main_deposito') {
    state.stage = 'deposit_collect';
    state.owner = OWNER.CUSTOMER;
    return handleDepositCollect(state, input, { startedByButton: true });
  }
  if (id === 'withdrawal_menu') {
    state.stage = 'withdrawal_menu';
    state.owner = OWNER.CUSTOMER;
    return { state, responses: [buttons('withdrawal', lang)] };
  }
  if (id === 'other_menu') {
    state.stage = 'other_menu';
    state.owner = OWNER.CUSTOMER;
    return { state, responses: [buttons('other', lang)] };
  }
  if (id === 'main_retiro') {
    state.stage = 'withdrawal_collect';
    state.owner = OWNER.CUSTOMER;
    return handleWithdrawalCollect(state, input, { startedByButton: true });
  }
  if (id === 'deposit_howto') {
    state.stage = 'after_deposit_howto';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [
        message(getMessage('deposit_howto_tutorial', lang), NEXT_STEP.SOP, OWNER.CUSTOMER, {
          imageUrls: onceImageUrls(state, 'sop.deposit_howto', sopImageUrlsFor('deposit_howto', input.platform)),
          imagePosition: 'before',
        }),
        buttons('deposit_recovery', lang),
      ],
    };
  }
  if (id === 'withdrawal_howto') {
    state.stage = 'after_withdrawal_howto';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [
        message(getMessage('withdrawal_howto_tutorial', lang), NEXT_STEP.SOP, OWNER.CUSTOMER, {
          imageUrls: onceImageUrls(state, 'sop.withdrawal_howto', sopImageUrlsFor('withdrawal_howto', input.platform)),
          imagePosition: 'before',
        }),
        buttons('withdrawal_recovery', lang),
      ],
    };
  }
  if (id === 'withdrawal_blocked') {
    state.stage = 'withdrawal_blocked';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [
        message(getMessage('withdrawal_blocked_tutorial', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER, {
          imageUrls: onceImageUrls(state, 'sop.withdrawal_blocked', sopImageUrlsFor('withdrawal_blocked', input.platform)),
          imagePosition: 'before',
        }),
        buttons('withdrawal_recovery', lang),
      ],
    };
  }
  if (id === 'forgot_password') {
    state.stage = 'forgot_password_sop';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [
        message(getMessage('forgot_password', lang), NEXT_STEP.SOP, OWNER.CUSTOMER, {
          imageUrls: onceImageUrls(state, 'sop.forgot_password', sopImageUrlsFor('forgot_password', input.platform)),
          imagePosition: 'before',
        }),
        buttons('other_recovery', lang),
      ],
    };
  }
  if (id === 'main_pending_reply') {
    state.stage = 'pending_reply_collect';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [message(flowMessage(state, 'pending_reply_ask_identity', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
    };
  }
  return {
    state,
    responses: [message(flowMessage(state, 'menu_button_reminder', lang), NEXT_STEP.BUTTONS, OWNER.CUSTOMER)],
  };
}

function routeToMenu(state, input, scope = 'main') {
  const lang = state.lang || input?.lang || 'es';
  const menuName = scope === 'deposit'
    ? 'deposit'
    : scope === 'withdrawal'
      ? 'withdrawal'
      : scope === 'other'
        ? 'other'
        : 'main';
  state.stage = menuName === 'main' ? 'menu' : `${menuName}_menu`;
  state.owner = OWNER.CUSTOMER;
  return { state, responses: [buttons(menuName, lang)] };
}

function handleDepositCollect(state, input, options = {}) {
  const lang = state.lang || 'es';
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (isServiceFrustrationHumanIssue(input.text)) {
    return handoffHuman(state, input, 'deposit_collect_service_frustration');
  }
  if (isAccountAccessHumanIssue(input.text) || isAccountProfileHumanIssue(input.text)) {
    return handoffHuman(state, input, 'deposit_collect_account_human_issue');
  }
  if (!hasAttachment(input) && isScreenshotUploadFailure(input.text)) {
    return handoffHuman(state, input, 'deposit_screenshot_upload_failed');
  }
  if (isWithdrawalHumanIssue(input.text)) {
    return handoffHuman(state, input, 'deposit_collect_withdrawal_human_issue');
  }
  if (isWithdrawalMissingFreeText(input.text) || isWithdrawalFreeText(input.text)) {
    if (hasDepositCaseData(state)) return handoffHuman(state, input, 'deposit_collect_conflicting_withdrawal_text');
    return handleButton(state, 'main_retiro', input);
  }
  collectDepositFields(state, input);
  const hasIdentity = !!state.fields.accountOrPhone;
  const hasSlip = !!state.fields.depositScreenshot;

  if (hasIdentity && hasSlip) {
    state.stage = 'waiting_backend';
    state.owner = OWNER.TG_BACKEND;
    return {
      state,
      responses: [
        message(flowMessage(state, 'deposito_done', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND, {
          actions: [{ type: 'forward_to_tg', caseType: 'deposit_missing' }],
        }),
      ],
    };
  }
  if (!hasIdentity && hasSlip) {
    return {
      state,
      responses: [message(flowMessage(state, 'deposito_ask_username_after_image', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
    };
  }
  if (hasIdentity && !hasSlip) {
    return {
      state,
      responses: [message(flowMessage(state, 'deposito_ask_slip', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER, {
        imageUrls: onceImageUrls(state, 'slip.main_deposito', slipExampleUrlsForCategory('main_deposito')),
        imagePosition: 'after',
      })],
    };
  }
  return {
    state,
    responses: [message(flowMessage(state, 'deposito_ask_username', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER, {
      imageUrls: options.startedByButton ? onceImageUrls(state, 'slip.main_deposito', slipExampleUrlsForCategory('main_deposito')) : [],
      imagePosition: 'after',
    })],
  };
}

function handleWithdrawalCollect(state, input) {
  const lang = state.lang || 'es';
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (isServiceFrustrationHumanIssue(input.text)) {
    return handoffHuman(state, input, 'withdrawal_collect_service_frustration');
  }
  if (classifyWithdrawalIssue(input.text).type === 'human') {
    return handoffHuman(state, input, 'withdrawal_collect_human_issue');
  }
  if (!hasAttachment(input) && isScreenshotUploadFailure(input.text)) {
    return handoffHuman(state, input, 'withdrawal_screenshot_upload_failed');
  }
  if (isDepositFreeText(input.text)) {
    if (hasWithdrawalCaseData(state)) return handoffHuman(state, input, 'withdrawal_collect_conflicting_deposit_text');
    return handleButton(state, 'main_deposito', input);
  }
  collectWithdrawalFields(state, input);
  const hasIdentity = !!state.fields.accountOrPhone;
  const hasSlip = !!state.fields.withdrawalScreenshot;

  if (hasIdentity && hasSlip) {
    state.stage = 'waiting_backend';
    state.owner = OWNER.TG_BACKEND;
    return {
      state,
      responses: [
        message(flowMessage(state, 'retiro_done', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND, {
          actions: [{ type: 'forward_to_tg', caseType: 'withdrawal_missing' }],
        }),
      ],
    };
  }
  if (!hasIdentity && hasSlip) {
    return {
      state,
      responses: [message(flowMessage(state, 'retiro_ask_username_after_image', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
    };
  }
  if (hasIdentity && !hasSlip) {
    return {
      state,
      responses: [message(flowMessage(state, 'retiro_ask_slip', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER, {
        imageUrls: onceImageUrls(state, 'slip.main_retiro', slipExampleUrlsForCategory('main_retiro')),
        imagePosition: 'after',
      })],
    };
  }
  return {
    state,
    responses: [message(flowMessage(state, 'retiro_ask_username', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER, {
      imageUrls: onceImageUrls(state, 'slip.main_retiro', slipExampleUrlsForCategory('main_retiro')),
      imagePosition: 'after',
    })],
  };
}

function handleWithdrawalBlocked(state, input) {
  const lang = state.lang || 'es';
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (classifyWithdrawalIssue(input.text).type === 'human') {
    return handoffHuman(state, input, 'withdrawal_blocked_human_issue');
  }
  const identity = extractIdentity(input.text);
  if (identity) {
    state.fields.accountOrPhone = identity.value;
    state.stage = 'backend_querying';
    state.owner = OWNER.BOT;
    return {
      state,
      responses: [
        actionOnly(NEXT_STEP.BACKEND_QUERY, OWNER.BOT, {
          actions: [{ type: 'query_backend', queryType: 'rollover' }],
        }),
      ],
    };
  }
  return {
    state,
    responses: [message(flowMessage(state, 'withdrawal_blocked_ask_username', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
  };
}

function handlePendingReplyCollect(state, input) {
  const lang = state.lang || 'es';
  if (isExplicitHumanRequest(input.text)) {
    return handoffHuman(state, input, 'explicit_human_request');
  }
  if (isHighRiskHumanIssue(input.text)) {
    return handoffHuman(state, input, 'pending_reply_human_issue');
  }
  if (isDepositFreeText(input.text)) {
    return handleButton(state, 'main_deposito', input);
  }
  if (isWithdrawalMissingFreeText(input.text)) {
    return handleButton(state, 'main_retiro', input);
  }
  if (isWithdrawalBlockedFreeText(input.text) || isWithdrawalContextHumanIssue(input.text)) {
    return handoffHuman(state, input, 'pending_reply_withdrawal_human_issue');
  }
  const identity = extractIdentity(input.text) || extractPendingReplyIdentity(input.text);
  if (!identity) {
    return {
      state,
      responses: [message(flowMessage(state, 'pending_reply_invalid_identity', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
    };
  }
  state.fields.pendingReplyIdentity = identity.value;
  state.stage = 'pending_reply_lookup';
  state.owner = OWNER.BOT;
  return {
    state,
    responses: [
      actionOnly(NEXT_STEP.BACKEND_QUERY, OWNER.BOT, {
        actions: [{ type: 'query_pending_reply' }],
      }),
    ],
  };
}

function extractPendingReplyIdentity(text) {
  const raw = String(text || '').trim();
  if (!raw) return null;
  if (/\s/.test(raw)) return null;
  if (!/^[a-zA-Z0-9_.@+-]{4,40}$/.test(raw)) return null;
  if (/^(hola|buenas|gracias|retiro|deposito|depósito|recarga|telefono|teléfono|correo|email|ayuda|humano|humana)$/i.test(raw)) {
    return null;
  }
  return { type: 'username', value: raw };
}

function handleSelfServiceAftercare(state, input, kind) {
  const lang = state.lang || 'es';
  if (isResolutionConfirmation(input.text)) {
    state.stage = 'soft_parked';
    state.owner = OWNER.SOFT_PARKED;
    return {
      state,
      responses: [message(flowMessage(state, 'customer_resolved_ack', lang), NEXT_STEP.TERMINAL, OWNER.SOFT_PARKED)],
    };
  }
  if (kind === 'forgot_password' && isAckOnly(input.text)) {
    return { state, responses: [] };
  }
  if (hasAttachment(input) && kind === 'deposit_howto') {
    state.stage = 'deposit_collect';
    state.owner = OWNER.CUSTOMER;
    return handleDepositCollect(state, input);
  }
  if (hasAttachment(input) && kind === 'withdrawal_howto') {
    state.stage = 'withdrawal_collect';
    state.owner = OWNER.CUSTOMER;
    return handleWithdrawalCollect(state, input);
  }
  if (isGreetingOnly(input.text)) {
    return { state, responses: [buttons(`${recoveryScopeForState(state)}_recovery`, lang)] };
  }
  if (isNonActionableText(input.text)) {
    return { state, responses: [] };
  }
  state.stage = 'human_handoff';
  state.owner = OWNER.HUMAN;
  return {
    state,
    responses: [
      message(flowMessage(state, 'human_done', lang), NEXT_STEP.HUMAN, OWNER.HUMAN, {
        actions: [{ type: 'handoff_human', reason: `aftercare_${kind}` }],
      }),
    ],
  };
}

function handleWaitingBackend(state, input) {
  const lang = state.lang || 'es';
  if (isResolutionConfirmation(input.text)) {
    state.stage = 'soft_parked';
    state.owner = OWNER.SOFT_PARKED;
    return {
      state,
      responses: [message(flowMessage(state, 'customer_resolved_ack', lang), NEXT_STEP.TERMINAL, OWNER.SOFT_PARKED)],
    };
  }
  if (isAckOnly(input.text)) {
    state.stage = 'waiting_backend';
    state.owner = OWNER.TG_BACKEND;
    return {
      state,
      responses: [message(flowMessage(state, 'backend_ack_waiting', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND)],
    };
  }
  if (!hasAttachment(input) && isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'waiting_backend_unhandled_concrete_issue');
  }
  const classification = classifyWaitingBackendInput(input);
  if (classification.type === 'supplement') {
    if (hasOnlyAlreadyForwardedAttachments(state, input)) {
      return { state, responses: [] };
    }
    rememberForwardedAttachments(state, input);
    return {
      state,
      responses: [
        message(flowMessage(state, 'forwarded_processing_ack', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND, {
          actions: [{ type: 'append_to_tg_case', reason: classification.reason }],
        }),
      ],
    };
  }
  if (classification.type === 'human') {
    state.stage = 'human_handoff';
    state.owner = OWNER.HUMAN;
    return {
      state,
      responses: [
        message(flowMessage(state, 'human_done', lang), NEXT_STEP.HUMAN, OWNER.HUMAN, {
          actions: [{ type: 'handoff_human' }],
        }),
      ],
    };
  }
  if (classification.type === 'followup' && shouldHandoffWaitingFollowup(state, input, 'waiting_backend_followup')) {
    return handoffHuman(state, input, 'waiting_backend_followup_repeat');
  }
  return {
    state,
    responses: [message(flowMessage(state, 'forwarded_followup_pool', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND)],
  };
}

function handleBackendRepliedNext(state, input) {
  const lang = state.lang || 'es';
  if (isResolutionConfirmation(input.text)) {
    state.stage = 'soft_parked';
    state.owner = OWNER.SOFT_PARKED;
    return {
      state,
      responses: [message(flowMessage(state, 'customer_resolved_ack', lang), NEXT_STEP.TERMINAL, OWNER.SOFT_PARKED)],
    };
  }
  if (isAckOnly(input.text)) {
    state.stage = 'waiting_backend';
    state.owner = OWNER.TG_BACKEND;
    return {
      state,
      responses: [message(flowMessage(state, 'backend_ack_waiting', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND)],
    };
  }
  if (isRolloverDisputeAfterQuery(state, input.text)) {
    const count = Number(state.fields.rolloverDisputeCount || 0);
    state.fields.rolloverDisputeCount = count + 1;
    if (count >= 1) {
      return handoffHuman(state, input, 'rollover_dispute_repeat');
    }
    state.stage = 'backend_replied_waiting_next';
    state.owner = OWNER.CUSTOMER;
    return {
      state,
      responses: [message(flowMessage(state, 'rollover_dispute_explain', lang), NEXT_STEP.FIXED_DATA, OWNER.CUSTOMER)],
    };
  }
  if (!hasAttachment(input) && isUnhandledConcreteIssue(input.text)) {
    return handoffHuman(state, input, 'backend_reply_unhandled_concrete_issue');
  }
  const classification = classifyWaitingBackendInput(input);
  if (classification.type === 'supplement') {
    state.stage = 'waiting_backend';
    state.owner = OWNER.TG_BACKEND;
    return handleWaitingBackend(state, input);
  }
  if (classification.type === 'human') {
    return handleButton(state, 'global_human', input);
  }
  if (classification.type === 'followup' && shouldHandoffWaitingFollowup(state, input, 'backend_reply_followup')) {
    return handoffHuman(state, input, 'backend_reply_followup_repeat');
  }
  state.stage = 'waiting_backend';
  state.owner = OWNER.TG_BACKEND;
  return {
    state,
    responses: [message(flowMessage(state, 'forwarded_followup_pool', lang), NEXT_STEP.WAITING_BACKEND, OWNER.TG_BACKEND)],
  };
}

function collectDepositFields(state, input) {
  const identity = extractIdentity(input.text);
  if (identity) state.fields.accountOrPhone = identity.value;
  if (hasAttachment(input)) {
    state.fields.depositScreenshot = input.attachments[0];
    rememberForwardedAttachments(state, input);
  }
}

function collectWithdrawalFields(state, input) {
  const identity = extractIdentity(input.text);
  if (identity) state.fields.accountOrPhone = identity.value;
  if (hasAttachment(input)) {
    state.fields.withdrawalScreenshot = input.attachments[0];
    rememberForwardedAttachments(state, input);
  }
}

function attachmentUrls(input) {
  return (input.attachments || [])
    .map(item => String(item?.url || '').trim())
    .filter(Boolean);
}

function rememberForwardedAttachments(state, input) {
  const urls = attachmentUrls(input);
  if (!urls.length) return;
  const existing = new Set(state.fields.forwardedAttachmentUrls || []);
  for (const url of urls) existing.add(url);
  state.fields.forwardedAttachmentUrls = [...existing];
}

function hasOnlyAlreadyForwardedAttachments(state, input) {
  const urls = attachmentUrls(input);
  if (!urls.length) return false;
  const existing = new Set(state.fields.forwardedAttachmentUrls || []);
  return urls.every(url => existing.has(url));
}

function hasDepositCaseData(state) {
  return !!(state.fields?.accountOrPhone || state.fields?.depositScreenshot);
}

function hasWithdrawalCaseData(state) {
  return !!(state.fields?.accountOrPhone || state.fields?.withdrawalScreenshot);
}

function menuContextForStage(stage) {
  if (stage === 'deposit_menu') return 'deposit';
  if (stage === 'money_direction') return 'money_direction';
  if (stage === 'withdrawal_menu') return 'withdrawal';
  if (stage === 'after_withdrawal_howto') return 'withdrawal';
  if (stage === 'other_menu') return 'other';
  if (stage === 'forgot_password_sop') return 'forgot_password_aftercare';
  return 'main';
}

function recoveryScopeForState(state) {
  const stage = state?.stage;
  if (stage === 'after_deposit_howto') return 'deposit';
  if (stage === 'after_withdrawal_howto' || stage === 'withdrawal_blocked') return 'withdrawal';
  if (stage === 'forgot_password_sop') return 'other';
  if (state?.fields?.recoveryScope) return state.fields.recoveryScope;
  return 'main';
}

function handoffHuman(state, input, reason = 'human_handoff') {
  const lang = state.lang || input?.lang || 'es';
  state.stage = 'human_handoff';
  state.owner = OWNER.HUMAN;
  return {
    state,
    responses: [
      message(flowMessage(state, 'human_done', lang), NEXT_STEP.HUMAN, OWNER.HUMAN, {
        actions: [{ type: 'handoff_human', reason }],
      }),
    ],
  };
}

function handleMenuVisibilityFallback(state, input, menuName, scope) {
  const lang = state.lang || input?.lang || 'es';
  const count = incrementReprompt(state, `${scope}_visibility`);
  if (count >= 2) {
    return handoffHuman(state, input, `${scope}_menu_not_visible`);
  }
  return {
    state,
    responses: [
      message(flowMessage(state, 'menu_visibility_resend', lang), NEXT_STEP.BUTTONS, OWNER.CUSTOMER),
      buttons(menuName, lang),
    ],
  };
}

function menuRepromptOrHandoff(state, input, scope, messageKey, menuName) {
  const lang = state.lang || input?.lang || 'es';
  const unknownCount = isCountableUnknownMenuText(input)
    ? incrementReprompt(state, `${scope}_unknown`)
    : 0;
  if (unknownCount >= 2) {
    return handoffHuman(state, input, `${scope}_unknown_after_reminder`);
  }
  incrementReprompt(state, scope);
  const responses = [message(flowMessage(state, messageKey, lang), NEXT_STEP.BUTTONS, OWNER.CUSTOMER)];
  return { state, responses };
}

function shouldHandoffWaitingFollowup(state, input, scope) {
  if (!isCountableWaitingFollowup(input)) return false;
  const count = incrementReprompt(state, scope);
  return count >= 2;
}

function incrementReprompt(state, scope) {
  state.repromptCounts = state.repromptCounts || {};
  state.repromptCounts[scope] = Number(state.repromptCounts[scope] || 0) + 1;
  return state.repromptCounts[scope];
}

function resetUnknownRepromptCounts(state) {
  state.repromptCounts = state.repromptCounts || {};
  for (const key of Object.keys(state.repromptCounts)) {
    if (key.endsWith('_unknown')) delete state.repromptCounts[key];
  }
}

function isCountableUnknownMenuText(input) {
  if (hasAttachment(input)) return false;
  const raw = normalizeFreeText(input?.text);
  if (!raw || raw.length < 4) return false;
  if (isNonActionableText(raw) || isGreetingOnly(raw)) return false;
  if (extractIdentity(input.text)) return false;
  return true;
}

function isCountableWaitingFollowup(input) {
  if (hasAttachment(input)) return false;
  const raw = normalizeFreeText(input?.text);
  if (!raw || raw.length < 4) return false;
  if (isNonActionableText(raw) || isGreetingOnly(raw)) return false;
  if (extractIdentity(input.text)) return false;
  return true;
}

function menuReminderKey(text) {
  if (isDepositFreeText(text)) return 'menu_deposit_button_reminder';
  if (isWithdrawalFreeText(text)) return 'menu_withdrawal_button_reminder';
  return 'menu_button_reminder';
}

function classifyWithdrawalIssue(text) {
  if (isWithdrawalContextHumanIssue(text) || isAccountAccessHumanIssue(text) || isAccountProfileHumanIssue(text)) {
    return { type: 'human' };
  }
  if (isWithdrawalMissingFreeText(text)) return { type: 'missing' };
  if (isWithdrawalBlockedFreeText(text)) return { type: 'blocked' };
  if (isWithdrawalHowtoFreeText(text)) return { type: 'howto' };
  if (isWithdrawalFreeText(text)) return { type: 'generic' };
  return { type: 'none' };
}

function routeWithdrawalDecision(state, input, decision, source) {
  if (!decision || decision.type === 'none') return null;
  if (decision.type === 'human') {
    return handoffHuman(state, input, `${source}_withdrawal_human_issue`);
  }
  if (decision.type === 'missing') {
    return handleButton(state, 'main_retiro', input);
  }
  if (decision.type === 'blocked') {
    return handleButton(state, 'withdrawal_blocked', input);
  }
  if (decision.type === 'howto') {
    return handleButton(state, 'withdrawal_howto', input);
  }
  if (decision.type === 'generic') {
    return handleButton(state, 'withdrawal_menu', input);
  }
  return null;
}

function isDepositFreeText(text) {
  const raw = normalizeFreeText(text);
  const hasDeposit = /\b(dep[oó]sito|deposito|deposit\w*|recarga|recargo|recargar|recargue|recarg[oó]|consignaci[oó]n|pago|comprobante)\b/i.test(raw);
  const hasCompletedPayment = /\b(hice|ise|hize|realice|realic[eé]|acabe|acab[eé]|pagu[eé]|pago|deposit[eé]|recargu[eé]|consign[eé]|primero hice)\b/i.test(raw);
  const hasMoneyToGame = /\b(\d{2,}|\d+\s*mil|mil|plata|dinero|saldo|monto)\b/i.test(raw) &&
    /\b(juego|plataforma)\b/i.test(raw) &&
    /\b(no(?:\s+\w{1,12}){0,4}\s+(?:lleg\w*|yeg\w*)|nada\s+q?\s*(?:lleg\w*|yeg\w*)|no aparece|no sale|no asign\w*|no reflej\w*)\b/i.test(raw);
  const hasMissingOrStatus = /\b(no(?:\s+\w{1,12}){0,3}\s+(?:lleg\w*|yeg\w*)|nada\s+q?\s*(?:lleg\w*|yeg\w*)|nunca\s+yeg\w*|ayeg\w*|no acredit|sin acreditar|no(?:\s+\w{1,12}){0,3}\s+aparec\w*|no(?:\s+\w{1,12}){0,3}\s+sale|no(?:\s+\w{1,12}){0,3}\s+reflej\w*|no se me refleja|no(?:\s+\w{1,12}){0,3}\s+asign\w*|pendiente|demora|tardando|perdid\w*|perdi[oó]?|se perdi[oó]|descontad\w*|descontaron|salieron|no(?:\s+\w{1,12}){0,3}\s+recib\w*|no se ha hecho efectivo|no se ha echo efectivo|que(?:\s+\w{1,12}){0,4}\s+pasad\w*|q(?:\s+\w{1,12}){0,4}\s+pasad\w*|que razon|q razon|a que horas llega|cuando llega|cu[aá]ndo llega|cuanto demora|cu[aá]nto demora|cuanto tarda|cu[aá]nto tarda|gone)\b/i.test(raw);
  return hasMoneyToGame || hasDeposit && (hasMissingOrStatus || hasCompletedPayment && /\b(no|nunca|nada|pendiente|descont|recib|aparec|reflej|efectivo|asign)\b/i.test(raw));
}

function isDepositHowtoFreeText(text) {
  const raw = normalizeFreeText(text);
  const asksHow = /\b(como|c[oó]mo|guia|gu[ií]a|instruccion|pasos?|how|guide|tutorial)\b/i.test(raw) ||
    /\b(necesito|quiero|kiero|deseo|tratando)\b.{0,36}\b(hacer\w*|realizar|depositar|recargar)\b/i.test(raw) ||
    /\bhacer\w*\s+n?\s+(dep[oó]sito|deposito|recarga)\b/i.test(raw) ||
    /\b(no puedo|no me deja|no deja)\b.{0,36}\b(depositar|recargar|hacer una recarga|hacer un deposito)\b/i.test(raw) ||
    /\b(depositar|recargar|hacer una recarga|hacer un deposito)\b.{0,36}\b(no puedo|no me deja|no deja)\b/i.test(raw);
  const hasDeposit = /\b(recarg\w*|deposit\w*|consign\w*|pagar|pago|metodo|m[eé]todo|depositar|recargar|recarga|deposito|recharge)\b/i.test(raw);
  return asksHow && hasDeposit &&
    !isDepositFreeText(raw);
}

function isWithdrawalFreeText(text) {
  const raw = normalizeFreeText(text);
  return /\b(retiro|retirar|retir\w*|withdraw|sacar|sacarla|sacarlo|cobrar)\b/i.test(raw);
}

function isWithdrawalMissingFreeText(text) {
  const raw = normalizeFreeText(text);
  const hasWithdrawal = /\b(retiro|retirar|retir[eé]|retire|withdraw|sacar|sacarla|sacarlo|cobrar)\b/i.test(raw);
  const notReceived = /\b(no(?:\s+\w{1,12}){0,4}\s+(?:lleg\w*|yeg\w*)|no recibido|no recib[ií]\w*|no(?:\s+\w{1,12}){0,3}\s+aparec\w*|no(?:\s+\w{1,12}){0,3}\s+sale|no se ve|no(?:\s+\w{1,12}){0,3}\s+reflej\w*|demora|tardando|pendiente|aun no|todavia no|no ha llegado|no me han llegado|cuanto tiempo|cu[aá]nto tiempo|cuanto tarda|cu[aá]nto tarda|cuando llega|cu[aá]ndo llega|se ve el pago)\b/i.test(raw);
  const notPaid = /\b(no|nunca|aun no|todavia no|todavia)\b(?:\s+\w{1,12}){0,4}\b(pag\w*|consign\w*)\b|\bno me han pagado\b|\bno me pagaron\b|\bnunca me pagaron\b|\bsin pagar\b/i.test(raw);
  return hasWithdrawal && (notReceived || notPaid);
}

function isWithdrawalBlockedFreeText(text) {
  const raw = normalizeFreeText(text);
  return /\b(no puedo|no me deja|no deja|bloquead\w*|rechazad\w*|fall\w*|error|problema)\b/i.test(raw) &&
    /\b(retir\w*|retiro|withdraw|sacar)\b/i.test(raw);
}

function isWithdrawalHowtoFreeText(text) {
  const raw = normalizeFreeText(text);
  return /\b(como|c[oó]mo|hacer|realizar|guia|gu[ií]a|instruccion|pasos?|how|guide|tutorial)\b/i.test(raw) &&
    /\b(retir\w*|retiro|withdraw|sacar)\b/i.test(raw) &&
    !isWithdrawalMissingFreeText(raw) &&
    !isWithdrawalBlockedFreeText(raw);
}

function isMenuVisibilityProblem(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return /\b(no veo|no aparece|no salen|no sale|no encuentro|donde|d[oó]nde|cual|cu[aá]l|que menu|qu[eé] menu|no hay|no tengo)\b/i.test(raw) &&
    /\b(menu|men[uú]|opcion|opciones|boton|bot[oó]n|botones|seleccion|arriba|above)\b/i.test(raw);
}

function isForgotPasswordFreeText(text) {
  const raw = normalizeFreeText(text);
  return /\b(olvide|olvid[eé]|recuperar|restablecer|reset|forgot|perdi|perd[ií]|no recuerdo|no me acuerdo|contrasena|comtrasena|password|clave)\b/i.test(raw) &&
    /\b(contrasena|comtrasena|password|clave|ingresar|login|entrar|cuenta|usuario|user)\b/i.test(raw);
}

function isPendingReplyFreeText(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return /\b(revisaste|revisaron|revisar|consultaste|consulta|estado|seguimiento|caso|solicitud|respuesta|para que dia|que dia|cuando esta|cuando queda|a que hora|a que horas)\b/i.test(raw) &&
    /\b(cuenta|caso|solicitud|deposito|retiro|dinero|plata|respuesta)\b/i.test(raw);
}

function isDepositMentionFreeText(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return /\b(dep[oó]sito|deposito|depositar|recarga|recargo|recargar|recargue|recarg[oó]|consignaci[oó]n)\b/i.test(raw) &&
    !isDepositFreeText(raw) &&
    !isDepositHowtoFreeText(raw);
}

function isEmptyOrSmallTalk(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return true;
  return /^(hola|buenas|buenos dias|buenas tardes|buenas noches|ok|okay|si|sí|gracias|dale|listo|hello|hi)$/i.test(raw);
}

function isGreetingOnly(text) {
  const raw = normalizeFreeText(text);
  return /^(hola|buenas|buenos dias|buenas tardes|buenas noches|hello|hi)$/i.test(raw);
}

function isNonActionableText(text) {
  return isEmptyOrSmallTalk(text) || isAckOnly(text);
}

function isHighRiskHumanIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return isWithdrawalContextHumanIssue(raw) ||
    isAccountAccessHumanIssue(raw) ||
    isAccountProfileHumanIssue(raw) ||
    isUnsupportedHumanIssue(raw) ||
    /\b(app|aplicacion|juego|saldo|monto|balance|dinero|plata|fondo|fondos|puntos?)\b/i.test(raw) && /\b(aparece en cero|saldo\s+(?:en\s+)?0+|se fue|no aparece|no me sale|no le sale|no sale|no se ve|no figura|desapareci\w*|se perdi\w*|quitaron|no muestra|no reflej\w*|actualic\w*|actualiz\w*)\b/i.test(raw) ||
    isServiceFrustrationHumanIssue(raw) ||
    /\b(carpeta segura|secure folder|safe folder|codigo de recuperacion|recovery code)\b/i.test(raw);
}

function isServiceFrustrationHumanIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return /\b(molest\w*|enojad\w*|cansad\w*|fastidiad\w*|indignad\w*)\b/i.test(raw) ||
    /\b(todo el tiempo lo mismo|siempre lo mismo|mismo mensaje|misma respuesta|solo lo mismo|otra vez lo mismo|nooo?\s+asi\s+no|asi no)\b/i.test(raw) ||
    /\b(no(?:\s+\w{1,12}){0,4}\s+(?:responden|responde|contestan|contesta|atienden|ayudan)|sin respuesta|no obtengo respuesta|nadie responde|nadie contesta)\b/i.test(raw) ||
    /\b(nunca(?:\s+\w{1,12}){0,4}\s+(?:envian|mandan|responden)|mejor(?:\s+\w{1,12}){0,5}\s+(?:dejo|retiro|salgo))\b/i.test(raw);
}

function isUnhandledConcreteIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw || isNonActionableText(raw)) return false;
  if (isHighRiskHumanIssue(raw)) return true;
  if (isKnownSelfServiceIssue(raw)) return false;
  const issueSignal = /\b(por que|porque|que pasa|qu[eé] pasa|que hago|qu[eé] hago|ayuda con|solucion\w*|problema|error|fall\w*|no puedo|no me deja|no deja|me sale|me aparece|aparece|sale|no sale|no aparece|no funciona|no entiendo|no se|no s[eé]|cual es|cu[aá]l es|como hago|c[oó]mo hago|como puedo|c[oó]mo puedo|quiero cambiar|necesito cambiar|configur\w*|redimir|canjear|reclamar|recuper\w*|bloquead\w*|rechazad\w*)\b/i.test(raw);
  const domainSignal = /\b(cuenta|codigo|c[oó]digo|bono|bonus|promo|promocion|promoci[oó]n|saldo|monto|dinero|plata|nequi|banco|cedula|c[eé]dula|documento|sim|whatsapp|telefono|tel[eé]fono|correo|email|app|aplicacion|aplicaci[oó]n|juego|pagina|p[aá]gina|password|contrasena|clave|usuario|perfil|billetera|wallet|datos|carpeta segura|archivo|captura|comprobante|retiro|deposito|dep[oó]sito|recarga)\b/i.test(raw);
  return issueSignal && domainSignal;
}

function isKnownSelfServiceIssue(text) {
  return isDepositFreeText(text) ||
    isDepositHowtoFreeText(text) ||
    isWithdrawalMissingFreeText(text) ||
    isWithdrawalBlockedFreeText(text) ||
    isWithdrawalHowtoFreeText(text) ||
    isPendingReplyFreeText(text) ||
    isForgotPasswordFreeText(text) ||
    isAmbiguousMoneyDirectionIssue(text);
}

function isUnsupportedHumanIssue(raw) {
  return /\b(problemas tecnicos|problema tecnico|tecnico|del juego|juego no|no abre|no carga|pantalla|error del juego|sistema|funcion[oó]|bug)\b/i.test(raw) ||
    /\b(bono|bonus|promocion|promotional?|promocional|promo code|codigo promoc|codigo promo|free spin|gratis|canjear|redimir|redeem|redeeming|claim free|coupon|voucher)\b/i.test(raw) ||
    /\b(code|codigo|c[oó]digo|clave)\b/i.test(raw) && /\b(redeem|redimir|canjear|claim|promo|promotion|promocion|promocional|bonus|bono|coupon|voucher)\b/i.test(raw) ||
    /\b(reembols\w*|refund|devolver|devolucion|devuelvan|regresar el dinero)\b/i.test(raw) ||
    /\b(afiliad|referid|referir|recomendar|recomendado|invitad|credito completo)\b/i.test(raw) ||
    /\b(registrarme|registrar|registro|no me deja registrar|nuevo usuario|crear cuenta|otra cuenta|dos cuentas|multiple cuenta)\b/i.test(raw) ||
    /\b(codigo|clave)\b/i.test(raw) && /\b(no(?:\s+\w{1,12}){0,3}\s+lleg\w*|nada\s+q?\s*lleg\w*|no(?:\s+\w{1,12}){0,3}\s+env[ií]a\w*|no(?:\s+\w{1,12}){0,3}\s+genera|no(?:\s+\w{1,12}){0,3}\s+sirve|cual es|facebook|promocion|promocional|verificacion|verificar|correo|email)\b/i.test(raw) ||
    /\b(roban|robar|robo|robad\w*|ladron\w*|jueput\w*|hijueput\w*|porqueria|estafa|fraude|solo he perdido|no se gana|manipulad)\b/i.test(raw) ||
    /\b(rechazad\w*|cancelad\w*|devuelt\w*|transaccion pendiente)\b/i.test(raw) ||
    /\b(enviar|mandar|subir|adjuntar|cargar|hacer)\b/i.test(raw) && /\b(captura|capture|comprobante|archivo|video)\b/i.test(raw) ||
    /\b(no pertenece a ustedes|aparec\w*.*mi juego|mi juego.*aparec\w*)\b/i.test(raw) ||
    /\b(cerr[oó]\w*.*p[aá]gina|se me cerr[oó]\w*)\b/i.test(raw) && /\b(abrir|cuenta|ingresar)\b/i.test(raw) ||
    /\b(no me da inicio|no puedo ingresar|no me deja ingresar|ingresar a mi cuenta)\b/i.test(raw) ||
    /\b(ganad[oa]s?|premio)\b/i.test(raw) && /\b(cuenta|aparece|llega|reclamar)\b/i.test(raw) ||
    /\b(no se cuenta con billetera digital|no.*agregad\w*.*cuenta|banco.*resolverlo|mediante de movimientos)\b/i.test(raw);
}

function isAccountAccessHumanIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const access = /\b(recuper\w*|restablec\w*|ingresar|entrar|login|inicio|iniciar|acceso|abrir)\b/i.test(raw) &&
    /\b(cuenta|usuario|password|contrasena|clave|codigo|c[oó]digo|verificacion|verificaci[oó]n)\b/i.test(raw);
  const codeBlocked = /\b(codigo|c[oó]digo|verificacion|verificaci[oó]n|sms|sim|whatsapp|telefono|tel[eé]fono|llamada|correo|email)\b/i.test(raw) &&
    /\b(no(?:\s+\w{1,12}){0,4}\s+(?:lleg\w*|recib\w*|env[ií]\w*|funcion\w*)|no puedo recibir|no me llega|no me llaman|no recibo|solo whatsapp|sin sim|sim)\b/i.test(raw);
  return access || codeBlocked;
}

function isAccountProfileHumanIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const accountField = /\b(cuenta|numero|n[uú]mero|nequi|banco|billetera|wallet|datos|informacion personal|informaci[oó]n personal|perfil|tarjeta|cedula|c[eé]dula|documento|nombre|titular)\b/i.test(raw);
  const changeOrSetup = /\b(configur\w*|cambi\w*|actualiz\w*|modific\w*|editar|edit\w*|elimin\w*|borr\w*|agreg\w*|registr\w*|vincul\w*|asoci\w*|llenar|llene|complet\w*|no me deja|no deja|ya existe|existe una cuenta|cuenta agregad\w*)\b/i.test(raw);
  return accountField && changeOrSetup;
}

function isScreenshotUploadFailure(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  return /\b(no puedo|no me deja|no deja|no sale|no funciona|fall\w*|error|imposible|no carga|no sube|upload failed|failed)\b/i.test(raw) &&
    /\b(enviar|mandar|subir|adjuntar|cargar|poner|hacer|captura|capture|comprobante|archivo|foto|imagen|screenshot)\b/i.test(raw);
}

function isWithdrawalContextHumanIssue(text) {
  return isWithdrawalHumanIssue(text) ||
    isWithdrawalAccountDisplayIssue(text) ||
    isWithdrawalIdentityWalletFormatIssue(text);
}

function isWithdrawalAccountDisplayIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const displaySignal = /\b(me sale|me aparece|aparece|sale|muestra|figura|veo|supuestamente)\b/i.test(raw);
  const wrongAccount = /\b(otra cuenta|otro numero|otro numero de cuenta|cuenta de otra|no es mi cuenta|cuenta equivocad\w*|cuenta supuestamente|otra cuenta supuestamente)\b/i.test(raw);
  const withdrawalChannel = /\b(canal|metodo|medio|opcion)\b.{0,36}\b(retiro|retirar|retir\w*)\b|\b(retiro|retirar|retir\w*)\b.{0,36}\b(canal|metodo|medio|opcion)\b/i.test(raw);
  const mismatchOrChanged = /\b(no es|no corresponde|no coincide|aparece|sale|muestra|figura|cambi\w*|actualiz\w*|otro|otra|diferente)\b/i.test(raw);
  return displaySignal && wrongAccount || withdrawalChannel && mismatchOrChanged;
}

function isWithdrawalIdentityWalletFormatIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const nequiNoAccountNumber = /\bnequi\b/i.test(raw) && (
    /\b(no tiene|no tengo|sin|no cuenta con)\b.{0,45}\b(numero|numero de cuenta|cuenta)\b/i.test(raw) ||
    /\b(me pide|pide|solicita)\b.{0,45}\b(numero|numero de cuenta)\b/i.test(raw)
  );
  const foreignIdentity = /\b(ppt|pasaporte|passport|extranjero|extranjera|venezolan\w*)\b/i.test(raw) ||
    /\b(no tengo|sin)\b.{0,35}\b(cedula|documento|identificacion)\b/i.test(raw);
  return nequiNoAccountNumber || foreignIdentity;
}

function isWithdrawalHumanIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const walletOrBank = /\b(nequi|davivienda|bancolombia|banco|billetera|wallet|cuenta|numero|datos bancarios|cuenta bancaria|titular)\b/i.test(raw);
  const walletMismatch = /\b(cambi\w*|actualiz\w*|modific\w*|editar|edit\w*|elimin\w*|borr\w*|agreg\w*|registr\w*|incorrect\w*|equivocad\w*|no registrad\w*|no coincid\w*|diferente\w*|duplicad\w*|asociad\w*|vinculad\w*|repetid\w*|mal|err[oó]ne\w*)\b/i.test(raw);
  const identityField = /\b(cedula|c[eé]dula|documento|identificacion|identificaci[oó]n|id|nombre|apellido|correo|email|mail|titular)\b/i.test(raw);
  const identityProblem = /\b(duplicad\w*|repetid\w*|no coincid\w*|no correspond\w*|incorrect\w*|equivocad\w*|asociad\w*|vinculad\w*|actualiz\w*|cambi\w*|especial\w*|caracter\w*|mal escrito|no me deja|no deja|error)\b/i.test(raw);
  const withdrawal = /\b(retiro|retirar|retir\w*|withdraw|sacar)\b/i.test(raw);
  const wrongDestination = /\b(otra cuenta|otro banco|cuenta equivocad\w*|banco equivocad\w*|no registrad\w*|no es mi cuenta)\b/i.test(raw);
  const withdrawalAccountProblem = withdrawal && /\b(nequi|banco|billetera|cuenta|datos|cedula|c[eé]dula|documento|nombre|titular)\b/i.test(raw) &&
    /\b(cambiar|cambio|cambi\w*|actualiz\w*|modific\w*|editar|equivocad\w*|incorrect\w*|mal|no coincid\w*|no correspond\w*|duplicad\w*|repetid\w*|registrad\w*|vinculad\w*|asociad\w*)\b/i.test(raw);
  const personalDataBlocked = /\b(informacion personal|informaci[oó]n personal|datos personales|llenar datos|llenar informacion|llenar informaci[oó]n|complete los datos|completar datos)\b/i.test(raw) &&
    /\b(no(?:\s+\w{1,12}){0,3}\s+(?:deja|permite|puedo)|ya|pero|retir\w*|retiro|sacar)\b/i.test(raw);
  const unresolvedWithdrawal = /\b(retir\w*|retiro|sacar|nequi|billetera|dinero|plata)\b/i.test(raw) &&
    /\b(tengo rato|todo el dia|toda la manana|toda la ma[nñ]ana|desde la manana|desde ayer|mucho tiempo|solucionen|solucionar|no solucionan|no me ayudan|sigo esperando|me tienen esperando|robar|robo|estafa|fraude)\b/i.test(raw);
  return (walletOrBank && walletMismatch) ||
    (identityField && identityProblem) ||
    (withdrawal && wrongDestination) ||
    withdrawalAccountProblem ||
    personalDataBlocked ||
    unresolvedWithdrawal;
}

function isRolloverDisputeAfterQuery(state, text) {
  const lastQuery = state?.fields?.lastBackendQuery;
  return lastQuery?.queryType === 'rollover' &&
    lastQuery.hasPendingRollover === true &&
    isRolloverDisputeText(text);
}

function isRolloverDisputeText(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  const played = /\b(ya|he|e|h[eé])\b.{0,35}\b(jugad\w*|apostad\w*|apost[eé]|aposte|jugu[eé]|jugue)\b/i.test(raw) ||
    /\b(ya jugue|ya jug[eé]|ya aposte|ya apost[eé]|he jugado|he apostado)\b/i.test(raw);
  const noDrop = /\b(no(?:\s+\w{1,12}){0,4}\s+(?:baja|disminuy\w*|cambi\w*|actualiz\w*)|se mantiene|mismo valor|igual|sigue igual|no me baja)\b/i.test(raw);
  const amountShouldDrop = /\b\d{4,}\b/.test(raw) &&
    /\b(baj\w*|disminuy\w*|mismo|igual|valor|rollover|debio|debia|deberia|hubiese)\b/i.test(raw);
  return (played && noDrop) || amountShouldDrop;
}

function isAmbiguousMoneyDirectionIssue(text) {
  const raw = normalizeFreeText(text);
  if (!raw) return false;
  if (isDepositFreeText(raw) || isWithdrawalFreeText(raw)) return false;
  const hasMoneySignal = /\b(plata|dinero|monto|saldo|cuenta|nequi|banco|pesos?|cop|\d{4,})\b/i.test(raw);
  const hasMissingSignal = /\b(no(?:\s+\w{1,12}){0,3}\s+lleg\w*|no aparece|no sale|no se ve|no reflej\w*|no entra|solo me lleg\w*|falt\w*|descontad\w*|perdid\w*)\b/i.test(raw);
  return hasMoneySignal && hasMissingSignal;
}

function normalizeFreeText(text) {
  return String(text || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .trim();
}

function isAckOnly(text) {
  const raw = normalizeFreeText(text);
  return /^(ok|okay|bueno|vale|listo|dale|de acuerdo|entiendo|gracias|muchas gracias|thanks|thank you|thx|perfecto|esta bien|est[aá] bien|好的|好|謝謝|谢谢|了解|知道了)$/i.test(raw);
}

function isResolutionConfirmation(text) {
  const raw = normalizeFreeText(text);
  if (/\b(no|aun no|todavia no|todavia|a[uú]n)\b.{0,40}\b(llego|llega|recibi|recibido|recibir|esta en mi cuenta)\b/i.test(raw)) {
    return false;
  }
  return /\b(resuelto|solucionado|ya quedo|ya me llego|ya llego|me llego|recibi|recibido|ya recibi|llego el dinero|ya esta en mi cuenta|好了|已收到|到帳了|解决|解決)\b/i.test(raw);
}

module.exports = {
  createCase,
  transition,
};
