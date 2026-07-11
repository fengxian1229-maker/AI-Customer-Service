'use strict';

function buildQuickRepliesEvent(response) {
  if (!response || response.kind !== 'buttons') return null;
  return {
    type: 'rich_message',
    template_id: 'quick_replies',
    elements: [
      {
        title: response.title,
        buttons: (response.buttons || []).map(button => ({
          type: 'message',
          text: button.label,
          value: button.label,
          postback_id: button.id,
          user_ids: [],
        })),
      },
    ],
  };
}

function buildButtonsFallbackText(response) {
  if (!response || response.kind !== 'buttons') return '';
  return [
    response.title,
    '',
    ...(response.buttons || []).map((button, index) => `${index + 1}. ${button.label}`),
  ].join('\n').trim();
}

module.exports = {
  buildQuickRepliesEvent,
  buildButtonsFallbackText,
};
