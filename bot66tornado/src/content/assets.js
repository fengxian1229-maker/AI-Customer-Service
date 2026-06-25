'use strict';

const path = require('path');

const ASSETS_DIR = path.resolve(__dirname, '..', '..', 'assets');
const TUTORIALS_DIR = path.join(ASSETS_DIR, 'tutorials');

const TUTORIAL_PLATFORM_CODES = [
  'JUE999',
  'MXN',
  'GNA777',
  'JG7',
  'PAG99',
  'CUM777',
  'CON777',
  'ZAP69',
];

function tutorialImage(platform, filename) {
  return path.join(TUTORIALS_DIR, platform, filename);
}

function tutorialImageMap(filename) {
  const mapped = Object.fromEntries(
    TUTORIAL_PLATFORM_CODES.map(platform => [platform, tutorialImage(platform, filename)])
  );
  return {
    ...mapped,
    TEST: tutorialImage('JUE999', filename),
    default: tutorialImage('JUE999', filename),
  };
}

function sopTutorialImageMap(filename) {
  const mapped = Object.fromEntries(
    TUTORIAL_PLATFORM_CODES.map(platform => [platform, [tutorialImage(platform, filename)]])
  );
  return {
    ...mapped,
    TEST: [tutorialImage('JUE999', filename)],
    default: [tutorialImage('JUE999', filename)],
  };
}

const FORGOT_PASSWORD_IMAGE_URLS = tutorialImageMap('forgot-password.jpg');

const SOP_IMAGE_URLS = {
  deposit_howto: sopTutorialImageMap('deposit.jpg'),
  withdrawal_howto: sopTutorialImageMap('withdrawal.jpg'),
  withdrawal_blocked: [
    'https://cdn.files-text.com/us-south1/api/lc/att/19282375/25fdf510d22b2d54fe1a8c4d74ba081e/image.png',
  ],
};

const SLIP_EXAMPLE_IMAGE_URLS = {
  main_deposito: [
    path.join(ASSETS_DIR, 'examples', 'deposit-payment-success-onepay.jpg'),
  ],
  main_retiro: [
    'https://cdn.files-text.com/us-south1/api/lc/att/19282375/b8ad62e07349d3ea4b48157fdf7fba03/Screenshot_20260509-212935.JUE999.png',
  ],
};

function forgotPasswordImageForPlatform(platform) {
  const key = String(platform || '').trim().toUpperCase();
  return FORGOT_PASSWORD_IMAGE_URLS[key] || FORGOT_PASSWORD_IMAGE_URLS.default;
}

function slipExampleUrlsForCategory(category) {
  return SLIP_EXAMPLE_IMAGE_URLS[category] || [];
}

function sopImageUrlsFor(intentId, platform = null) {
  if (intentId === 'forgot_password') {
    const imageUrl = forgotPasswordImageForPlatform(platform);
    return imageUrl ? [imageUrl] : [];
  }
  const map = SOP_IMAGE_URLS[intentId];
  if (!map) return [];
  if (Array.isArray(map)) return map;
  const key = String(platform || '').trim().toUpperCase();
  return map[key] || map.default || [];
}

module.exports = {
  FORGOT_PASSWORD_IMAGE_URLS,
  SOP_IMAGE_URLS,
  SLIP_EXAMPLE_IMAGE_URLS,
  forgotPasswordImageForPlatform,
  slipExampleUrlsForCategory,
  sopImageUrlsFor,
};
